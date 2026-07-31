#!/usr/bin/env python3
"""Run and visually review deterministic held-out PiHCA validation wells."""
from __future__ import annotations

import argparse
import json
import os
import socket
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

from hca_contract import atomic_write_json, load_jsonl, sha256
from hca_resources import admit_gpus, gpu_inventory, gpu_reservation, resolve_workers
from hca_review_ui import image_to_png
from hca_runtime import verify


def next_validation(output: Path) -> Path:
    parent = output / "validation"
    numbers = [int(path.name.rsplit("-", 1)[1]) for path in parent.glob("heldout-*")
               if path.name.rsplit("-", 1)[-1].isdigit()]
    return parent / f"heldout-{max(numbers, default=0) + 1:03d}"


def spread(items: list[dict], count: int) -> list[dict]:
    if count >= len(items):
        return items
    if count == 1:
        return [items[len(items) // 2]]
    indices = [round(index * (len(items) - 1) / (count - 1)) for index in range(count)]
    return [items[index] for index in indices]


def field_ids(manifest: Path) -> list[str]:
    return sorted({f"{item['well']}-{item['site']}-t{item['timepoint']}-z{item['z']}" for item in load_jsonl(manifest)})


def choose_jobs(plan: dict, pilot_well: str | None, minimum_wells: int, minimum_fields: int) -> list[dict]:
    eligible = [job for job in plan["jobs"] if job["well"] != pilot_well]
    if len(eligible) < minimum_wells:
        raise ValueError(f"held-out plan has only {len(eligible)} untouched wells; {minimum_wells} required")
    count = minimum_wells
    while count <= len(eligible):
        selected = spread(eligible, count)
        if sum(len(field_ids(Path(job["manifest"]))) for job in selected) >= minimum_fields:
            return selected
        count += 1
    raise ValueError(f"held-out plan cannot provide {minimum_fields} independent fields")


def requires_gpu(config: dict) -> bool:
    segmentation = config.get("analysis", {}).get("segmentation", {})
    return (any(stage.get("enabled") and stage.get("gpu") for stage in segmentation.values()
                if isinstance(stage, dict))
            or config.get("analysis", {}).get("embedding", {}).get("enabled", False))


def run_validation(state_path: Path, workers: int, gpus: str = "auto") -> dict:
    state = json.loads(state_path.read_text(encoding="utf-8"))
    if state.get("phase") != "heldout_validation_required":
        raise ValueError(f"workflow phase is {state.get('phase')!r}, expected 'heldout_validation_required'")
    ready, errors = verify(Path(state["runtime_lock"]))
    if not ready:
        raise ValueError("runtime lock verification failed: " + "; ".join(errors))
    config = json.loads(Path(state["config"]).read_text(encoding="utf-8"))
    optimization = config.get("analysis", {}).get("optimization", {})
    minimum_wells = int(optimization.get("minimum_heldout_wells", 3))
    minimum_fields = int(optimization.get("minimum_heldout_fields", 9))
    plan = json.loads(Path(state["well_plan"]).read_text(encoding="utf-8"))
    selected = choose_jobs(plan, (state.get("pilot_field") or {}).get("well"), minimum_wells, minimum_fields)
    inventory = gpu_inventory()
    admitted = admit_gpus(gpus, inventory=inventory)
    worker_count = resolve_workers(workers, gpu_ids=admitted, requires_gpu=requires_gpu(config),
                                   cpu_default=int(plan.get("max_workers", 1)), job_count=len(selected))
    output = next_validation(Path(state["output"])); wells_dir = output / "wells"
    pipeline = Path(__file__).parent / "hca_pipeline.py"

    def execute(index: int, job: dict) -> dict:
        destination = wells_dir / job["well"]
        command = [sys.executable, str(pipeline), "--well-manifest", job["manifest"],
                   "--config", state["config"], "--source-root", state["input"], "--output-dir", str(destination)]
        gpu = admitted[index % len(admitted)] if admitted else None
        env = os.environ.copy()
        if gpu is not None:
            env["CUDA_VISIBLE_DEVICES"] = str(gpu)
        with gpu_reservation(gpu):
            process = subprocess.run(command, env=env, capture_output=True, text=True)
        return {"well": job["well"], "returncode": process.returncode, "output": str(destination),
                "gpu": gpu, "stdout": process.stdout[-2000:], "stderr": process.stderr[-2000:]}

    with ThreadPoolExecutor(max_workers=worker_count) as executor:
        results = [future.result() for future in as_completed(
            [executor.submit(execute, index, job) for index, job in enumerate(selected)])]
    if failed := [item for item in results if item["returncode"] != 0]:
        atomic_write_json(output / "execution-failures.json", {"failures": failed})
        raise ValueError(f"{len(failed)} held-out wells failed; inspect {output / 'execution-failures.json'}")
    fields = []
    for result in sorted(results, key=lambda item: item["well"]):
        summary = json.loads((Path(result["output"]) / "pipeline-summary.json").read_text(encoding="utf-8"))
        for field in summary["fields"]:
            field_dir = Path(result["output"]) / f"{field['site']}-t{field['timepoint']}-z{field['z']}"
            overlay = next((field_dir / name for name in ("relationship-overlay.tif", "cell-overlay.tif", "nuclei-overlay.tif", "confluence-overlay.tif")
                            if (field_dir / name).is_file()), None)
            if overlay is None:
                raise ValueError(f"held-out field has no review overlay: {field_dir}")
            all_overlays = [field_dir / name for name in ("nuclei-overlay.tif", "cell-overlay.tif",
                            "relationship-overlay.tif", "confluence-overlay.tif") if (field_dir / name).is_file()]
            fields.append({"id": f"{result['well']}-{field['site']}-t{field['timepoint']}-z{field['z']}",
                           "well": result["well"], "overlay": str(overlay), "overlay_sha256": sha256(overlay),
                           "overlays": [{"kind": path.stem.replace("-overlay", ""), "path": str(path),
                                         "sha256": sha256(path)} for path in all_overlays],
                           "raw_images": [field.get(key) for key in ("nuclear_source_image", "cell_source_image")
                                          if field.get(key)],
                           "relationship_qc": field.get("relationship_qc"), "relationship": field.get("relationship")})
    evidence = {"schema_version": 1, "status": "pending_visual_review", "wells": sorted({item["well"] for item in fields}),
                "fields": fields, "visual_review_complete": False, "config": state["config"],
                "workflow_state": str(state_path), "execution": sorted(results, key=lambda item: item["well"]),
                "resources": {"requested_gpus": gpus, "admitted_gpus": admitted,
                              "workers": worker_count, "inventory": inventory}}
    atomic_write_json(output / "heldout-evidence.json", evidence)
    return build_review(output)


def build_review(output: Path) -> dict:
    evidence = json.loads((output / "heldout-evidence.json").read_text(encoding="utf-8"))
    assets = output / "review-assets"; rows = []
    for number, field in enumerate(evidence["fields"], start=1):
        views = field.get("overlays") or [{"kind": "segmentation", "path": field["overlay"]}]
        images = []
        for view_number, view in enumerate(views, start=1):
            png = assets / f"field-{number:03d}-{view_number:02d}.png"
            image_to_png(Path(view["path"]), png)
            images.append(f'<figure><img src="review-assets/{png.name}"><figcaption>{view["kind"]}</figcaption></figure>')
        relationship = json.dumps(field.get("relationship") or {}, sort_keys=True)
        rows.append(f"""<section data-field="{field['id']}"><h2>{field['id']}</h2><code>{relationship}</code>
<div class="images">{''.join(images)}</div><label><input type="radio" name="f{number}" value="accepted"> Accept</label>
<label><input type="radio" name="f{number}" value="rejected"> Reject</label></section>""")
    document = """<!doctype html><html><head><meta charset="utf-8"><title>PiHCA held-out validation</title><style>
	body{font:14px system-ui;margin:0;color:#172126;background:#f5f7f7}main{max-width:1200px;margin:auto;background:white;padding:20px 28px}section{border-top:1px solid #d8e0e2;padding:14px 0}.images{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:10px}figure{margin:0}img{display:block;width:100%;max-height:520px;object-fit:contain;background:#101719}figcaption{color:#53636b}button,input{padding:8px}</style></head><body><main>
<h1>PiHCA held-out validation</h1><p>Review every independent field. Any rejected field prevents release.</p><label>Reviewer <input id="reviewer"></label>""" + "".join(rows) + """
<button onclick="submit()">Submit held-out review</button><span id="status"></span><script>
async function submit(){const reviewer=document.querySelector('#reviewer').value.trim();const decisions={};document.querySelectorAll('section').forEach(s=>{const x=s.querySelector('input:checked');if(x)decisions[s.dataset.field]=x.value});if(!reviewer||Object.keys(decisions).length!==document.querySelectorAll('section').length){alert('Enter a reviewer and decide every field.');return}const r=await fetch('/api/review',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({reviewer,decisions})});document.querySelector('#status').textContent=r.ok?'Saved. You may close this window.':'Save failed';}
</script></main></body></html>"""
    (output / "index.html").write_text(document, encoding="utf-8")
    return {"status": "awaiting_review", "fields": len(evidence["fields"]), "wells": len(evidence["wells"]),
            "index": str(output / "index.html"), "validation": str(output / "heldout-validation.json")}


def serve(directory: Path, host: str, port: int) -> int:
    evidence = json.loads((directory / "heldout-evidence.json").read_text(encoding="utf-8"))
    class Handler(SimpleHTTPRequestHandler):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, directory=str(directory), **kwargs)
        def do_POST(self):
            if urlparse(self.path).path != "/api/review":
                self.send_error(404); return
            try:
                body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", "0"))))
                if not body.get("reviewer"):
                    raise ValueError("named reviewer is required")
                decisions = body.get("decisions", {})
                reviewed = [{**field, "decision": decisions.get(field["id"], "pending")} for field in evidence["fields"]]
                complete = all(field["decision"] in {"accepted", "rejected"} for field in reviewed)
                passed = complete and all(field["decision"] == "accepted" and field.get("relationship_qc") != "failed" for field in reviewed)
                validation = {"schema_version": 1, "status": "passed" if passed else "failed",
                              "reviewer": body["reviewer"], "wells": evidence["wells"], "fields": reviewed,
                              "visual_review_complete": complete}
                atomic_write_json(directory / "heldout-validation.json", validation)
            except (ValueError, json.JSONDecodeError) as error:
                self.send_error(400, str(error)); return
            self.send_response(200); self.end_headers(); self.wfile.write(b'{"saved":true}')
    server = ThreadingHTTPServer((host, port), Handler)
    atomic_write_json(directory / "server.json", {"url": f"http://{host}:{server.server_port}/",
                                                   "pid": __import__("os").getpid()})
    server.serve_forever()
    return 0


def submit_vision_review(directory: Path, reviewer: str, decisions: list[dict]) -> dict:
    evidence = json.loads((directory / "heldout-evidence.json").read_text(encoding="utf-8"))
    expected = {field["id"]: field for field in evidence["fields"]}
    supplied = {item.get("id"): item for item in decisions}
    if None in supplied or set(supplied) != set(expected) or len(supplied) != len(decisions):
        raise ValueError("held-out vision review requires exactly one decision for every field")
    reviewed = [{**field, "decision": supplied[field_id].get("decision"),
                 "notes": supplied[field_id].get("notes")}
                for field_id, field in expected.items()]
    if any(field["decision"] not in {"accepted", "rejected"} for field in reviewed):
        raise ValueError("held-out decisions must be accepted or rejected")
    passed = all(field["decision"] == "accepted" and field.get("relationship_qc") != "failed"
                 for field in reviewed)
    return {"schema_version": 1, "status": "human_approval_required" if passed else "refinement_required",
            "vision_reviewer": reviewer, "wells": evidence["wells"], "fields": reviewed,
            "visual_review_complete": True, "resources": evidence.get("resources"),
            "decision_rule": "Every held-out field and relationship check must pass; named human approval remains required."}


def approve_vision_review(proposal: dict, reviewer: str) -> dict:
    if proposal.get("status") != "human_approval_required":
        raise ValueError("held-out proposal cannot be approved because at least one field failed")
    return {**proposal, "status": "passed", "reviewer": reviewer,
            "approved_vision_reviewer": proposal.get("vision_reviewer")}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="action", required=True)
    run_parser = commands.add_parser("run")
    run_parser.add_argument("--workflow-state", required=True, type=Path); run_parser.add_argument("--workers", type=int, default=0)
    run_parser.add_argument("--gpus", default="auto")
    run_parser.add_argument("--mode", choices=("human", "automated"), default="human"); run_parser.add_argument("--host", default="127.0.0.1"); run_parser.add_argument("--port", type=int, default=0)
    serve_parser = commands.add_parser("serve")
    serve_parser.add_argument("--directory", required=True, type=Path); serve_parser.add_argument("--host", default="127.0.0.1"); serve_parser.add_argument("--port", type=int, required=True)
    status = commands.add_parser("status"); status.add_argument("--directory", required=True, type=Path)
    submission = commands.add_parser("submit")
    submission.add_argument("--directory", required=True, type=Path); submission.add_argument("--reviewer", required=True)
    submission.add_argument("--decisions", required=True, type=Path); submission.add_argument("--output", required=True, type=Path)
    approval = commands.add_parser("approve")
    approval.add_argument("--proposal", required=True, type=Path); approval.add_argument("--reviewer", required=True)
    approval.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    if args.action == "serve":
        return serve(args.directory, args.host, args.port)
    if args.action == "submit":
        decisions = json.loads(args.decisions.read_text(encoding="utf-8"))["decisions"]
        payload = submit_vision_review(args.directory, args.reviewer, decisions)
        atomic_write_json(args.output, payload)
        print(json.dumps({"status": payload["status"], "output": str(args.output)}, indent=2))
        return 0
    if args.action == "approve":
        proposal = json.loads(args.proposal.read_text(encoding="utf-8"))
        payload = approve_vision_review(proposal, args.reviewer)
        atomic_write_json(args.output, payload)
        print(json.dumps({"status": payload["status"], "output": str(args.output)}, indent=2))
        return 0
    if args.action == "status":
        validation = args.directory / "heldout-validation.json"
        payload = {"status": "complete" if validation.exists() else "awaiting_review", "validation": str(validation)}
    else:
        payload = run_validation(args.workflow_state.resolve(), args.workers, args.gpus)
        output = Path(payload["index"]).parent
        if args.mode == "human":
            with socket.socket() as sock:
                sock.bind((args.host, args.port)); port = sock.getsockname()[1]
            command = [sys.executable, str(Path(__file__)), "serve", "--directory", str(output), "--host", args.host, "--port", str(port)]
            with (output / "server.log").open("a") as handle:
                process = subprocess.Popen(command, stdout=handle, stderr=subprocess.STDOUT, start_new_session=True)
            payload.update({"url": f"http://{args.host}:{port}/", "pid": process.pid})
        else:
            payload["instruction"] = "Inspect every review asset with the image-capable model and write heldout-validation.json using the evidence field IDs."
    print(json.dumps(payload, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
