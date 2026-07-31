#!/usr/bin/env python3
"""Build and serve a local HTML review UI for Pi HCA segmentation optimization."""
from __future__ import annotations

import argparse
import html
import json
import socket
import subprocess
import sys
import webbrowser
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse


def require(module: str):
    try:
        return __import__(module, fromlist=["*"])
    except ImportError as error:
        raise SystemExit("HTML image review requires: pip install '.[review]'") from error


def image_to_png(source: Path, destination: Path) -> None:
    numpy, tifffile = require("numpy"), require("tifffile")
    Image = require("PIL.Image")
    array = tifffile.imread(source)
    if array.ndim == 2:
        low, high = numpy.percentile(array, [1, 99.8])
        scaled = numpy.clip((array.astype("float32") - low) / max(float(high - low), 1.0), 0, 1)
        array = (scaled * 255).astype("uint8")
    elif array.ndim == 3 and array.shape[-1] in (3, 4):
        array = array.astype("uint8")
    else:
        raise ValueError(f"unsupported review image shape: {array.shape}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(array).save(destination)


def recommendations(review: dict) -> dict:
    selected = next((item for item in review.get("candidate_reviews", []) if item.get("id") == review.get("selected_candidate")), None)
    if not selected:
        return {"status": "no_candidate_selected", "next_sweep": None}
    parameters = dict(selected.get("parameters", {}))
    diameter = parameters.get("diameter")
    flow = float(parameters.get("flow_threshold", 0.4))
    probability = float(parameters.get("cellprob_threshold", 0.0))
    issues = set(selected.get("issues", []))
    diameter_values = [diameter] if diameter is None else sorted({round(diameter * 0.9, 2), diameter, round(diameter * 1.1, 2)})
    flow_values, probability_values = [flow], [probability]
    if "oversegmentation" in issues or "false_positives" in issues:
        flow_values = sorted({max(0.05, round(flow - 0.1, 2)), flow})
        probability_values = sorted({probability, round(probability + 0.5, 2)})
    if "undersegmentation" in issues or "missed_objects" in issues:
        flow_values = sorted(set(flow_values + [round(flow + 0.1, 2)]))
        probability_values = sorted(set(probability_values + [round(probability - 0.5, 2)]))
    return {"status": "approved" if review.get("review_status") == "approved" else "refinement_required",
            "selected_parameters": parameters,
            "next_sweep": {"diameters": diameter_values, "flow_thresholds": flow_values,
                           "cellprob_thresholds": probability_values} if review.get("review_status") != "approved" else None,
            "filter_recommendations": review.get("filter_recommendations", {})}


def validate_review(review: dict) -> None:
    if review.get("review_status") not in {"approved", "revise"}:
        raise ValueError("review status must be approved or revise")
    selected = review.get("selected_candidate")
    candidates = review.get("candidate_reviews", [])
    if not review.get("reviewer") or not selected or not any(item.get("id") == selected for item in candidates):
        raise ValueError("reviewer and a listed selected candidate are required")
    for candidate in candidates:
        score = candidate.get("score")
        if not isinstance(score, (int, float)) or not 0 <= score <= 100:
            raise ValueError(f"candidate {candidate.get('id')} requires a score from 0 to 100")
    if review.get("review_status") == "approved":
        selected_review = next(item for item in candidates if item.get("id") == selected)
        if selected_review.get("acceptable") is not True:
            raise ValueError("approved review must explicitly mark the selected candidate acceptable")
    for object_name, criteria in review.get("filter_recommendations", {}).items():
        for key, value in criteria.items():
            if value is not None and (not isinstance(value, (int, float)) or value < 0):
                raise ValueError(f"{object_name} filter {key} must be null or non-negative")
        for minimum, maximum in (("min_area_px", "max_area_px"), ("min_intensity_mean", "max_intensity_mean")):
            if criteria.get(minimum) is not None and criteria.get(maximum) is not None and criteria[minimum] > criteria[maximum]:
                raise ValueError(f"{object_name} filter {minimum} exceeds {maximum}")


def build(candidates_path: Path, output: Path) -> dict:
    candidates = json.loads(candidates_path.read_text(encoding="utf-8"))
    successful = [item for item in candidates.get("candidates", []) if item.get("returncode") == 0 and item.get("overlay")]
    if not successful:
        raise ValueError("candidate file contains no successful candidates with overlays")
    assets = output / "assets"
    image_to_png(Path(candidates["image"]), assets / "raw.png")
    rows = []
    for number, candidate in enumerate(successful, start=1):
        asset_name = f"candidate-{number:02d}.png"
        png = assets / asset_name
        image_to_png(Path(candidate["overlay"]), png)
        parameters = json.dumps(candidate["parameters"], sort_keys=True)
        candidate_id = html.escape(str(candidate["id"]), quote=True)
        encoded_parameters = html.escape(parameters, quote=True)
        rows.append(f"""<section class="candidate" data-id="{candidate_id}" data-parameters="{encoded_parameters}">
<header><label><input type="radio" name="selected" value="{candidate_id}"> {candidate_id}</label><code>{html.escape(parameters)}</code><span>{candidate.get('object_count','?')} objects</span></header>
<div class="images"><figure><img src="assets/raw.png"><figcaption>Raw image</figcaption></figure><figure><img src="assets/{asset_name}"><figcaption>Segmentation overlay</figcaption></figure></div>
<div class="controls"><label>Quality <input class="score" type="range" min="0" max="100" value="70"><output>70</output></label>
<label><input class="issue" type="checkbox" value="oversegmentation"> Over-segmented</label><label><input class="issue" type="checkbox" value="undersegmentation"> Under-segmented</label>
<label><input class="issue" type="checkbox" value="false_positives"> False objects</label><label><input class="issue" type="checkbox" value="missed_objects"> Missed objects</label>
<input class="notes" placeholder="Boundary or morphology notes"></div></section>""")
    document = """<!doctype html><html><head><meta charset="utf-8"><title>PiHCA segmentation review</title><style>
body{font:14px system-ui;margin:0;color:#172126;background:#f5f7f7}main{max-width:1180px;margin:auto;background:white;min-height:100vh;padding:20px 28px}h1{font-size:22px;margin:0 0 6px}.lead{color:#53636b;margin-bottom:20px}.candidate{border-top:1px solid #d8e0e2;padding:16px 0}header{display:flex;gap:18px;align-items:center}header code{flex:1;font-size:12px}.images{display:grid;grid-template-columns:1fr 1fr;gap:12px;margin:12px 0}figure{margin:0}img{width:100%;max-height:440px;object-fit:contain;background:#101719}figcaption{font-size:12px;color:#5d6a70}.controls{display:flex;gap:14px;align-items:center;flex-wrap:wrap}.controls input[type=text],.notes{min-width:260px;padding:6px}.filters{display:grid;grid-template-columns:repeat(4,minmax(130px,1fr));gap:10px;border-top:2px solid #273940;padding-top:16px}.filters label{display:grid;gap:4px}.filters input,#reviewer{padding:7px;border:1px solid #aab6ba}.actions{position:sticky;bottom:0;background:white;border-top:1px solid #d8e0e2;padding:14px 0;display:flex;gap:10px}button{padding:9px 14px;border:0;background:#126b5c;color:white;cursor:pointer}button.secondary{background:#53636b}@media(max-width:760px){.images,.filters{grid-template-columns:1fr}}</style></head>
<body><main><h1>PiHCA segmentation review</h1><p class="lead">Compare boundaries against the raw image. Select the biologically best candidate, mark defects, and record filter limits only when visible evidence supports them.</p>
<label>Reviewer <input id="reviewer" required placeholder="Name or identifier"></label>""" + "".join(rows) + """
<h2>Object filters</h2><div class="filters"><label>Nucleus min area<input id="n_min_area" type="number"></label><label>Nucleus max area<input id="n_max_area" type="number"></label><label>Nucleus min intensity<input id="n_min_intensity" type="number"></label><label>Nucleus max intensity<input id="n_max_intensity" type="number"></label><label>Cell min area<input id="c_min_area" type="number"></label><label>Cell max area<input id="c_max_area" type="number"></label><label>Cell min intensity<input id="c_min_intensity" type="number"></label><label>Cell max intensity<input id="c_max_intensity" type="number"></label></div>
<div class="actions"><button onclick="submitReview('approved')">Accept and save</button><button class="secondary" onclick="submitReview('revise')">Request another sweep</button><span id="status"></span></div></main><script>
document.querySelectorAll('.score').forEach(x=>x.oninput=()=>x.nextElementSibling.value=x.value);
async function submitReview(decision){const selected=document.querySelector('input[name=selected]:checked');const reviewer=document.querySelector('#reviewer').value.trim();if(!selected||!reviewer){alert('Select a candidate and enter a reviewer.');return}const reviews=[...document.querySelectorAll('.candidate')].map(row=>({id:row.dataset.id,parameters:JSON.parse(row.dataset.parameters),score:Number(row.querySelector('.score').value),acceptable:row.dataset.id===selected.value,issues:[...row.querySelectorAll('.issue:checked')].map(x=>x.value),notes:row.querySelector('.notes').value}));const value=id=>{const x=document.querySelector(id).value;return x===''?null:Number(x)};const filters=prefix=>({min_area_px:value(`#${prefix}_min_area`),max_area_px:value(`#${prefix}_max_area`),min_intensity_mean:value(`#${prefix}_min_intensity`),max_intensity_mean:value(`#${prefix}_max_intensity`)});const body={schema_version:1,reviewer,review_status:decision,selected_candidate:selected.value,candidate_reviews:reviews,filter_recommendations:{nucleus:filters('n'),cell:filters('c')}};const response=await fetch('/api/review',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});document.querySelector('#status').textContent=response.ok?'Saved. You may close this window.':'Save failed';}
</script></body></html>"""
    output.mkdir(parents=True, exist_ok=True)
    (output / "index.html").write_text(document, encoding="utf-8")
    payload = {"status": "ready", "candidates": len(successful), "failed_candidates": len(candidates.get("candidates", [])) - len(successful),
               "index": str(output / "index.html"), "review": str(output / "human-review.json")}
    (output / "ui.json").write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return payload


def serve(directory: Path, host: str, port: int, open_browser: bool) -> int:
    class Handler(SimpleHTTPRequestHandler):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, directory=str(directory), **kwargs)
        def do_POST(self):
            if urlparse(self.path).path != "/api/review":
                self.send_error(404); return
            length = int(self.headers.get("Content-Length", "0"))
            if length < 2 or length > 1024 * 1024:
                self.send_error(400, "invalid review size"); return
            try:
                review = json.loads(self.rfile.read(length))
                validate_review(review)
            except (json.JSONDecodeError, ValueError) as error:
                self.send_error(400, str(error)); return
            review["optimization"] = recommendations(review)
            (directory / "human-review.json").write_text(json.dumps(review, indent=2) + "\n", encoding="utf-8")
            self.send_response(200); self.send_header("Content-Type", "application/json"); self.end_headers()
            self.wfile.write(b'{"saved":true}')
    server = ThreadingHTTPServer((host, port), Handler)
    url = f"http://{host}:{server.server_port}/"
    (directory / "server.json").write_text(json.dumps({"url": url, "pid": __import__("os").getpid()}) + "\n")
    print(json.dumps({"status": "serving", "url": url}), flush=True)
    if open_browser:
        webbrowser.open(url)
    server.serve_forever()
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="action", required=True)
    build_parser = commands.add_parser("build"); build_parser.add_argument("--candidates", required=True, type=Path); build_parser.add_argument("--output-dir", required=True, type=Path)
    start = commands.add_parser("start"); start.add_argument("--candidates", required=True, type=Path); start.add_argument("--output-dir", required=True, type=Path); start.add_argument("--host", default="127.0.0.1"); start.add_argument("--port", type=int, default=0); start.add_argument("--open-browser", action="store_true")
    serve_parser = commands.add_parser("serve"); serve_parser.add_argument("--directory", required=True, type=Path); serve_parser.add_argument("--host", default="127.0.0.1"); serve_parser.add_argument("--port", required=True, type=int); serve_parser.add_argument("--open-browser", action="store_true")
    status = commands.add_parser("status"); status.add_argument("--directory", required=True, type=Path)
    args = parser.parse_args()
    if args.action == "build":
        payload = build(args.candidates, args.output_dir)
    elif args.action == "serve":
        return serve(args.directory, args.host, args.port, args.open_browser)
    elif args.action == "status":
        review = args.directory / "human-review.json"; payload = {"status": "complete" if review.exists() else "awaiting_review", "review": str(review)}
    else:
        payload = build(args.candidates, args.output_dir)
        with socket.socket() as sock:
            sock.bind((args.host, args.port)); port = sock.getsockname()[1]
        command = [sys.executable, str(Path(__file__)), "serve", "--directory", str(args.output_dir), "--host", args.host, "--port", str(port)]
        if args.open_browser: command.append("--open-browser")
        process = subprocess.Popen(command, stdout=(args.output_dir / "server.log").open("a"), stderr=subprocess.STDOUT, start_new_session=True)
        payload.update({"status": "awaiting_review", "url": f"http://{args.host}:{port}/", "pid": process.pid})
    print(json.dumps(payload, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
