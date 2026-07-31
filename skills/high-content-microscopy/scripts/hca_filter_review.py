#!/usr/bin/env python3
"""Build and serve before/after review evidence for PiHCA object filters."""
from __future__ import annotations

import argparse
import html
import json
import socket
import subprocess
import sys
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

from hca_contract import atomic_write_json
from hca_release import approved_review
from hca_review_ui import image_to_png


EMPTY_FILTER = {"min_area_px": None, "max_area_px": None,
                "min_intensity_mean": None, "max_intensity_mean": None}


def channel_for_role(config: dict, role: str) -> int:
    matches = [int(channel) for channel, details in config["channels"].items() if details.get("role") == role]
    if len(matches) != 1:
        raise ValueError(f"expected exactly one channel with role {role!r}, found {matches}")
    return matches[0]


def run(command: list[str]) -> None:
    process = subprocess.run(command, capture_output=True, text=True)
    if process.returncode:
        raise ValueError(process.stderr.strip() or process.stdout.strip() or "filter evidence command failed")


def build(state_path: Path, segmentation_review: Path, output: Path) -> dict:
    state = json.loads(state_path.read_text(encoding="utf-8"))
    if state.get("phase") != "filter_review_required":
        raise ValueError(f"workflow phase is {state.get('phase')!r}, expected 'filter_review_required'")
    review = approved_review(segmentation_review)
    existing = output / "evidence.json"
    if existing.is_file() and (output / "index.html").is_file():
        payload = json.loads(existing.read_text(encoding="utf-8"))
        payload["status"] = "complete" if (output / "filter-review.json").is_file() else "awaiting_review"
        return payload
    if output.exists() and any(output.iterdir()):
        raise ValueError("filter review directory contains incomplete evidence; archive it before retrying")
    recommendations = review.get("filter_recommendations", {})
    config = json.loads(Path(state["config"]).read_text(encoding="utf-8"))
    field = state["pilot_field"]
    script_dir = Path(__file__).parent
    evidence = []
    normalized_recommendations = {}
    rows = []
    for stage in ("nucleus", "cell"):
        if stage not in state.get("accepted", {}):
            continue
        proposed = recommendations.get(stage, recommendations.get("nuclei", {})) if stage == "nucleus" else recommendations.get(stage, {})
        criteria = {**EMPTY_FILTER, **proposed}
        normalized_recommendations[stage] = criteria
        stage_config = config["analysis"]["segmentation"][stage]
        channel = channel_for_role(config, stage_config["channel_role"])
        image = Path(state["input"]) / field["channels"][str(channel)]["path"]
        labels = Path(state["accepted"][stage]["labels"])
        stage_dir = output / stage
        filtered = stage_dir / "filtered-labels.tif"
        audit = stage_dir / "filter-audit.json"
        before = stage_dir / "before-overlay.tif"
        after = stage_dir / "after-overlay.tif"
        command = [sys.executable, str(script_dir / "hca_filter.py"), "--labels", str(labels),
                   "--image", str(image), "--output", str(filtered), "--audit", str(audit)]
        flags = {"min_area_px": "--min-area-px", "max_area_px": "--max-area-px",
                 "min_intensity_mean": "--min-intensity-mean", "max_intensity_mean": "--max-intensity-mean"}
        for key, flag in flags.items():
            if criteria.get(key) is not None:
                command.extend([flag, str(criteria[key])])
        run(command)
        run([sys.executable, str(script_dir / "hca_overlay.py"), "--image", str(image),
             "--labels", str(labels), "--output", str(before)])
        run([sys.executable, str(script_dir / "hca_overlay.py"), "--image", str(image),
             "--labels", str(filtered), "--output", str(after)])
        image_to_png(before, stage_dir / "before.png")
        image_to_png(after, stage_dir / "after.png")
        audit_payload = json.loads(audit.read_text(encoding="utf-8"))
        record = {"object": stage, "criteria": criteria, "before": str(before), "after": str(after),
                  "audit": str(audit), "removed": audit_payload["input_object_count"] - audit_payload["output_object_count"]}
        evidence.append(record)
        criteria_text = html.escape(json.dumps(criteria, sort_keys=True))
        rows.append(f"""<section data-stage="{stage}"><h2>{stage.title()}</h2><code>{criteria_text}</code>
<p>{record['removed']} objects excluded</p><div class="images"><figure><img src="{stage}/before.png"><figcaption>Before filtering</figcaption></figure>
<figure><img src="{stage}/after.png"><figcaption>After filtering</figcaption></figure></div>
<label><input type="radio" name="{stage}" value="accepted"> Accept</label>
<label><input type="radio" name="{stage}" value="rejected"> Reject</label></section>""")
    if not evidence:
        raise ValueError("workflow contains no accepted labels for filter review")
    document = """<!doctype html><html><head><meta charset="utf-8"><title>PiHCA filter review</title><style>
body{font:14px system-ui;margin:0;color:#172126;background:#f5f7f7}main{max-width:1180px;margin:auto;background:white;min-height:100vh;padding:20px 28px}.images{display:grid;grid-template-columns:1fr 1fr;gap:12px}img{width:100%;max-height:460px;object-fit:contain;background:#101719}section{border-top:1px solid #d8e0e2;padding:16px 0}button{padding:9px 14px;border:0;background:#126b5c;color:white}input[type=text]{padding:7px}</style></head><body><main>
<h1>PiHCA filter review</h1><p>Inspect every excluded boundary before accepting these assay-level filters.</p>
<label>Reviewer <input id="reviewer" type="text"></label>""" + "".join(rows) + """
<button onclick="submit()">Approve filter evidence</button><span id="status"></span><script>
async function submit(){const reviewer=document.querySelector('#reviewer').value.trim();const decisions={};document.querySelectorAll('section').forEach(s=>{const x=s.querySelector('input:checked');if(x)decisions[s.dataset.stage]=x.value});if(!reviewer||Object.keys(decisions).length!==document.querySelectorAll('section').length){alert('Enter a reviewer and decide every object type.');return}const r=await fetch('/api/review',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({reviewer,decisions})});document.querySelector('#status').textContent=r.ok?'Saved. You may close this window.':'Save failed';}
</script></main></body></html>"""
    output.mkdir(parents=True, exist_ok=True)
    (output / "index.html").write_text(document, encoding="utf-8")
    payload = {"schema_version": 1, "status": "awaiting_review", "workflow_state": str(state_path),
               "source_review": str(segmentation_review), "filter_recommendations": normalized_recommendations,
               "filter_evidence": evidence, "review": str(output / "filter-review.json")}
    atomic_write_json(output / "evidence.json", payload)
    return payload


def serve(directory: Path, host: str, port: int) -> int:
    evidence = json.loads((directory / "evidence.json").read_text(encoding="utf-8"))
    class Handler(SimpleHTTPRequestHandler):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, directory=str(directory), **kwargs)
        def do_POST(self):
            if urlparse(self.path).path != "/api/review":
                self.send_error(404); return
            try:
                length = int(self.headers.get("Content-Length", "0"))
                body = json.loads(self.rfile.read(length))
                if not body.get("reviewer"):
                    raise ValueError("named reviewer is required")
                decisions = body.get("decisions", {})
                records = [{**item, "decision": decisions.get(item["object"], "pending")}
                           for item in evidence["filter_evidence"]]
                approved = all(item["decision"] == "accepted" for item in records)
                review = {"schema_version": 1, "review_status": "approved" if approved else "revise",
                          "reviewer": body["reviewer"], "review_images": [],
                          "filter_recommendations": evidence["filter_recommendations"],
                          "filter_evidence": records}
                atomic_write_json(directory / "filter-review.json", review)
            except (ValueError, json.JSONDecodeError) as error:
                self.send_error(400, str(error)); return
            self.send_response(200); self.end_headers(); self.wfile.write(b'{"saved":true}')
    server = ThreadingHTTPServer((host, port), Handler)
    atomic_write_json(directory / "server.json", {"url": f"http://{host}:{server.server_port}/",
                                                   "pid": __import__("os").getpid()})
    server.serve_forever()
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="action", required=True)
    build_parser = commands.add_parser("build")
    build_parser.add_argument("--workflow-state", required=True, type=Path); build_parser.add_argument("--review", required=True, type=Path)
    build_parser.add_argument("--output-dir", required=True, type=Path)
    start = commands.add_parser("start")
    start.add_argument("--workflow-state", required=True, type=Path); start.add_argument("--review", required=True, type=Path)
    start.add_argument("--output-dir", required=True, type=Path); start.add_argument("--host", default="127.0.0.1"); start.add_argument("--port", type=int, default=0)
    serve_parser = commands.add_parser("serve")
    serve_parser.add_argument("--directory", required=True, type=Path); serve_parser.add_argument("--host", default="127.0.0.1"); serve_parser.add_argument("--port", type=int, required=True)
    status = commands.add_parser("status"); status.add_argument("--directory", required=True, type=Path)
    args = parser.parse_args()
    if args.action == "serve":
        return serve(args.directory, args.host, args.port)
    if args.action == "status":
        review = args.directory / "filter-review.json"
        payload = {"status": "complete" if review.exists() else "awaiting_review", "review": str(review)}
    else:
        payload = build(args.workflow_state.resolve(), args.review.resolve(), args.output_dir.resolve())
        if args.action == "start":
            with socket.socket() as sock:
                sock.bind((args.host, args.port)); port = sock.getsockname()[1]
            command = [sys.executable, str(Path(__file__)), "serve", "--directory", str(args.output_dir.resolve()),
                       "--host", args.host, "--port", str(port)]
            log = args.output_dir / "server.log"
            with log.open("a") as handle:
                process = subprocess.Popen(command, stdout=handle, stderr=subprocess.STDOUT, start_new_session=True)
            payload.update({"url": f"http://{args.host}:{port}/", "pid": process.pid})
    print(json.dumps(payload, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
