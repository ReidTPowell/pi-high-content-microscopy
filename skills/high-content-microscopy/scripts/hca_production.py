#!/usr/bin/env python3
"""Run PiHCA canaries and submit approved single-plate production releases."""
from __future__ import annotations

import argparse
import json
import os
import socket
import subprocess
import sys
from pathlib import Path

from hca_contract import atomic_write_json
from hca_queue import initialise, publish_release, register_worker, rows, submit
from hca_release import approved_review, verify_release


def next_directory(parent: Path, prefix: str) -> Path:
    versions = []
    for path in parent.glob(f"{prefix}-*") if parent.exists() else []:
        suffix = path.name.rsplit("-", 1)[-1]
        if suffix.isdigit():
            versions.append(int(suffix))
    return parent / f"{prefix}-{max(versions, default=0) + 1:03d}"


def canary(state: dict, state_path: Path, well: str | None, gpus: str) -> dict:
    if state.get("phase") != "production_canary_required":
        raise ValueError(f"workflow phase is {state.get('phase')!r}, expected 'production_canary_required'")
    release = verify_release(Path(state["release"]))
    plan = json.loads(Path(state["well_plan"]).read_text(encoding="utf-8"))
    pilot_well = state.get("pilot_field", {}).get("well")
    heldout = json.loads(Path(state["heldout_validation"]).read_text(encoding="utf-8"))
    excluded = {pilot_well, *heldout.get("wells", [])}
    available = [job["well"] for job in plan["jobs"] if job["well"] not in excluded]
    selected = well or (available[len(available) // 2] if available else None)
    if not selected or selected not in available:
        raise ValueError("canary must be an untouched well present in the production plan")
    run_dir = next_directory(Path(state["output"]) / "runs", "canary")
    command = [sys.executable, str(Path(__file__).parent / "hca_runner.py"),
               "--plan", state["well_plan"], "--run-dir", str(run_dir), "--release", state["release"],
               "--workers", "1", "--retries", "0", "--canary-well", selected, "--gpus", gpus, "--fail-on-qc"]
    result = subprocess.run(command, capture_output=True, text=True)
    if result.returncode:
        raise ValueError(result.stderr.strip() or result.stdout.strip() or "production canary failed")
    state.update({"phase": "batch_approval_required", "canary": str(run_dir / "run-summary.json"),
                  "next_action": "Review the successful canary and obtain explicit operator approval for this plate batch."})
    atomic_write_json(state_path, state)
    return {"status": state["phase"], "well": selected, "run_dir": str(run_dir),
            "summary": state["canary"], "next_action": state["next_action"]}


def submit_batch(state: dict, state_path: Path, operator: str, workers: int, retries: int, gpus: str) -> dict:
    if state.get("phase") != "batch_approval_required":
        raise ValueError(f"workflow phase is {state.get('phase')!r}, expected 'batch_approval_required'")
    release = verify_release(Path(state["release"]))
    if release["approval"]["operator"] != operator:
        raise ValueError("batch operator must match the named release approver")
    if not Path(state.get("canary", "")).is_file():
        raise ValueError("a successful production canary is required before batch submission")
    queue_dir = Path(state["output"]) / "queue"
    initialise(queue_dir)
    published = publish_release(queue_dir, Path(state["release"]), operator)
    run_dir = next_directory(Path(state["output"]) / "runs", "run")
    request = submit(queue_dir, Path(state["well_plan"]), run_dir, published["id"], operator, workers, retries)
    worker_id = f"{socket.gethostname()}-{os.getpid()}"
    register_worker(queue_dir, worker_id, {"execution": "local", "release_id": release["id"]})
    log = queue_dir / "logs" / f"dispatcher-{request['job_id']}.log"
    command = [sys.executable, str(Path(__file__).parent / "hca_queue.py"), "--queue-dir", str(queue_dir),
               "dispatch", "--worker-id", worker_id, "--gpus", gpus, "--max-jobs", "1"]
    with log.open("a", encoding="utf-8") as handle:
        process = subprocess.Popen(command, stdout=handle, stderr=subprocess.STDOUT, start_new_session=True)
    state.update({"phase": "batch_running", "batch_job": request["job_id"], "batch_run": str(run_dir),
                  "queue": str(queue_dir), "dispatcher_pid": process.pid,
                  "next_action": "Poll PiHCA status; do not start another plate until plate QC completes."})
    atomic_write_json(state_path, state)
    return {"status": state["phase"], "job_id": request["job_id"], "run_dir": str(run_dir),
            "dispatcher_pid": process.pid, "status_file": str(run_dir / "status.json")}


def batch_status(state: dict, state_path: Path) -> dict:
    if not state.get("batch_job") or not state.get("queue"):
        return {"phase": state.get("phase"), "next_action": state.get("next_action")}
    jobs = [job for job in rows(Path(state["queue"]), "jobs") if job["id"] == state["batch_job"]]
    if not jobs:
        raise ValueError("batch job is missing from the queue")
    job = jobs[0]
    status_file = Path(state["batch_run"]) / "status.json"
    progress = json.loads(status_file.read_text(encoding="utf-8")) if status_file.is_file() else None
    if job["state"] == "complete" and state.get("phase") == "batch_running":
        state.update({"phase": "plate_qc_required",
                      "next_action": "Generate and review the plate report before marking the analysis complete."})
        atomic_write_json(state_path, state)
    return {"phase": state.get("phase"), "job": job, "progress": progress,
            "next_action": state.get("next_action")}


def complete_plate_qc(state: dict, state_path: Path, review_path: Path) -> dict:
    if state.get("phase") != "plate_qc_required":
        raise ValueError(f"workflow phase is {state.get('phase')!r}, expected 'plate_qc_required'")
    review = approved_review(review_path)
    run_dir = Path(state["batch_run"])
    summary = json.loads((run_dir / "run-summary.json").read_text(encoding="utf-8"))
    if summary.get("aborted") or any(result["status"] in {"failed", "not_started"} for result in summary["results"]):
        raise ValueError("plate QC cannot complete while production wells failed or were not started")
    report_dir = run_dir / "report"
    process = subprocess.run([sys.executable, str(Path(__file__).parent / "hca_report.py"),
                              "--analysis-root", str(run_dir / "wells"), "--output-dir", str(report_dir)],
                             capture_output=True, text=True)
    if process.returncode:
        raise ValueError(process.stderr.strip() or process.stdout.strip() or "plate report failed")
    report = json.loads((report_dir / "report.json").read_text(encoding="utf-8"))
    if report["fields"] < 1 or report["relationship_qc_failed"]:
        raise ValueError("plate report is empty or contains relationship QC failures")
    qc = {"schema_version": 1, "status": "approved", "reviewer": review["reviewer"],
          "review": str(review_path.resolve()), "report": str(report_dir / "report.json"),
          "release": state["release"], "run_summary": str(run_dir / "run-summary.json")}
    atomic_write_json(run_dir / "plate-qc.json", qc)
    share_base = Path(state["output"]) / "shares" / f"{Path(state['release']).parent.name}-{run_dir.name}"
    share = subprocess.run([sys.executable, str(Path(__file__).parent / "hca_share.py"),
                            "--analysis-dir", str(run_dir), "--output", str(share_base)],
                           capture_output=True, text=True)
    if share.returncode:
        raise ValueError(share.stderr.strip() or share.stdout.strip() or "share bundle failed")
    state.update({"phase": "complete", "plate_qc": str(run_dir / "plate-qc.json"),
                  "share_bundle": share.stdout.strip(), "next_action": "Analysis complete; interpret biology only with plate-map and control context."})
    atomic_write_json(state_path, state)
    return {"status": "complete", "report": str(report_dir / "report.html"),
            "plate_qc": state["plate_qc"], "share_bundle": state["share_bundle"]}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workflow-state", required=True, type=Path)
    commands = parser.add_subparsers(dest="action", required=True)
    canary_parser = commands.add_parser("canary")
    canary_parser.add_argument("--well"); canary_parser.add_argument("--gpus", default="auto")
    submit_parser = commands.add_parser("submit")
    submit_parser.add_argument("--operator", required=True); submit_parser.add_argument("--workers", type=int, default=1)
    submit_parser.add_argument("--retries", type=int, default=1); submit_parser.add_argument("--gpus", default="auto")
    commands.add_parser("status")
    complete = commands.add_parser("complete-plate-qc"); complete.add_argument("--review", required=True, type=Path)
    args = parser.parse_args()
    try:
        state_path = args.workflow_state.expanduser().resolve()
        state = json.loads(state_path.read_text(encoding="utf-8"))
        if args.action == "canary":
            payload = canary(state, state_path, args.well, args.gpus)
        elif args.action == "submit":
            payload = submit_batch(state, state_path, args.operator, args.workers, args.retries, args.gpus)
        elif args.action == "status":
            payload = batch_status(state, state_path)
        else:
            payload = complete_plate_qc(state, state_path, args.review.expanduser().resolve())
        print(json.dumps(payload, indent=2))
        return 0
    except (KeyError, OSError, ValueError, json.JSONDecodeError) as error:
        print(json.dumps({"error": str(error)}), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
