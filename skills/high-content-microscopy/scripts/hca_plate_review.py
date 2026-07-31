#!/usr/bin/env python3
"""Generate and serve final plate-level PiHCA visual QC."""
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


def build(run_dir: Path) -> dict:
    summary = run_dir / "run-summary.json"
    if not summary.is_file():
        raise ValueError("production run summary does not exist")
    run = json.loads(summary.read_text(encoding="utf-8"))
    if run.get("aborted") or any(item["status"] in {"failed", "not_started"} for item in run["results"]):
        raise ValueError("plate review cannot begin while production is incomplete")
    report_dir = run_dir / "report"
    if not (report_dir / "report.json").is_file():
        process = subprocess.run([sys.executable, str(Path(__file__).parent / "hca_report.py"),
                                  "--analysis-root", str(run_dir / "wells"), "--output-dir", str(report_dir)],
                                 capture_output=True, text=True)
        if process.returncode:
            raise ValueError(process.stderr.strip() or process.stdout.strip() or "plate report failed")
    report = json.loads((report_dir / "report.json").read_text(encoding="utf-8"))
    if report["fields"] < 1 or not report["figures"]:
        raise ValueError("plate report has no fields or review figures")
    review_dir = run_dir / "plate-review"
    existing = review_dir / "evidence.json"
    if existing.is_file() and (review_dir / "index.html").is_file():
        evidence = json.loads(existing.read_text(encoding="utf-8"))
        return {"status": "complete" if (review_dir / "plate-review.json").is_file() else "awaiting_review",
                "url_path": str(review_dir / "index.html"), "review": str(review_dir / "plate-review.json"),
                "images": len(evidence.get("images", []))}
    if review_dir.exists() and any(review_dir.iterdir()):
        raise ValueError("plate review contains incomplete evidence; archive it before retrying")
    rows = []
    images = []
    for number, relative in enumerate(report["figures"], start=1):
        source = report_dir / relative
        images.append(str(source))
        rows.append(f"""<section data-path="{html.escape(str(source), quote=True)}"><h2>Plate QC field {number}</h2><img src="../report/{html.escape(relative, quote=True)}">
<label><input type="radio" name="f{number}" value="accepted"> Accept</label>
<label><input type="radio" name="f{number}" value="rejected"> Reject</label></section>""")
    document = """<!doctype html><html><head><meta charset="utf-8"><title>PiHCA plate QC</title><style>
body{font:14px system-ui;margin:0;color:#172126;background:#f5f7f7}main{max-width:1100px;margin:auto;background:white;padding:20px 28px}section{border-top:1px solid #d8e0e2;padding:14px 0}img{display:block;width:100%;max-height:520px;object-fit:contain;background:#101719;margin:10px 0}button,input{padding:8px}</style></head><body><main>
<h1>PiHCA plate QC</h1><p>Review every sampled production overlay before completing and sharing this plate.</p><label>Reviewer <input id="reviewer"></label>""" + "".join(rows) + """
<button onclick="submit()">Submit plate review</button><span id="status"></span><script>
async function submit(){const reviewer=document.querySelector('#reviewer').value.trim();const decisions={};document.querySelectorAll('section').forEach(s=>{const x=s.querySelector('input:checked');if(x)decisions[s.dataset.path]=x.value});if(!reviewer||Object.keys(decisions).length!==document.querySelectorAll('section').length){alert('Enter a reviewer and decide every field.');return}const r=await fetch('/api/review',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({reviewer,decisions})});document.querySelector('#status').textContent=r.ok?'Saved. You may close this window.':'Save failed';}
</script></main></body></html>"""
    review_dir.mkdir(parents=True, exist_ok=False)
    (review_dir / "index.html").write_text(document, encoding="utf-8")
    atomic_write_json(review_dir / "evidence.json", {"report": str(report_dir / "report.json"), "images": images})
    return {"status": "awaiting_review", "url_path": str(review_dir / "index.html"),
            "review": str(review_dir / "plate-review.json"), "images": len(images)}


def serve(directory: Path, host: str, port: int) -> int:
    evidence = json.loads((directory / "evidence.json").read_text(encoding="utf-8"))
    class Handler(SimpleHTTPRequestHandler):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, directory=str(directory.parent), **kwargs)
        def do_POST(self):
            if urlparse(self.path).path != "/api/review":
                self.send_error(404); return
            try:
                body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", "0"))))
                if not body.get("reviewer"):
                    raise ValueError("named reviewer is required")
                decisions = body.get("decisions", {})
                images = [{"path": path, "decision": decisions.get(path, "pending"), "notes": None}
                          for path in evidence["images"]]
                complete = all(item["decision"] in {"accepted", "rejected"} for item in images)
                approved = complete and all(item["decision"] == "accepted" for item in images)
                atomic_write_json(directory / "plate-review.json", {"schema_version": 1,
                    "review_status": "approved" if approved else "revise", "reviewer": body["reviewer"],
                    "review_images": images, "report": evidence["report"]})
            except (ValueError, json.JSONDecodeError) as error:
                self.send_error(400, str(error)); return
            self.send_response(200); self.end_headers(); self.wfile.write(b'{"saved":true}')
    server = ThreadingHTTPServer((host, port), Handler)
    atomic_write_json(directory / "server.json", {"url": f"http://{host}:{server.server_port}/plate-review/",
                                                   "pid": __import__("os").getpid()})
    server.serve_forever()
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="action", required=True)
    start = commands.add_parser("start"); start.add_argument("--run-dir", required=True, type=Path)
    start.add_argument("--host", default="127.0.0.1"); start.add_argument("--port", type=int, default=0)
    build_parser = commands.add_parser("build"); build_parser.add_argument("--run-dir", required=True, type=Path)
    serve_parser = commands.add_parser("serve"); serve_parser.add_argument("--directory", required=True, type=Path)
    serve_parser.add_argument("--host", default="127.0.0.1"); serve_parser.add_argument("--port", required=True, type=int)
    status = commands.add_parser("status"); status.add_argument("--run-dir", required=True, type=Path)
    args = parser.parse_args()
    if args.action == "serve":
        return serve(args.directory, args.host, args.port)
    if args.action == "status":
        review = args.run_dir / "plate-review/plate-review.json"
        payload = {"status": "complete" if review.exists() else "awaiting_review", "review": str(review)}
    else:
        payload = build(args.run_dir.resolve())
        if args.action == "start":
            directory = Path(payload["url_path"]).parent
            with socket.socket() as sock:
                sock.bind((args.host, args.port)); port = sock.getsockname()[1]
            command = [sys.executable, str(Path(__file__)), "serve", "--directory", str(directory),
                       "--host", args.host, "--port", str(port)]
            with (directory / "server.log").open("a") as handle:
                process = subprocess.Popen(command, stdout=handle, stderr=subprocess.STDOUT, start_new_session=True)
            payload.update({"url": f"http://{args.host}:{port}/plate-review/", "pid": process.pid})
    print(json.dumps(payload, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
