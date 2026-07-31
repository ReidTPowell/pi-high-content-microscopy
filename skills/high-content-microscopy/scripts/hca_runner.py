#!/usr/bin/env python3
"""Run one approved PiHCA plate release with parallel, atomic well jobs."""
from __future__ import annotations

import argparse
import fcntl
import json
import os
import subprocess
import sys
import threading
import time
from collections import Counter
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from hca_contract import atomic_write_json, gpu_inventory, provenance, sha256
from hca_release import verify_release
from hca_runtime import verify


JOURNAL_LOCK = threading.Lock()
STARTUP_MARKERS = (
    "modulenotfounderror", "importerror", "no such file or directory", "permission denied",
    "runtime lock verification failed", "unrecognized arguments", "error: argument", "invalid assay",
)
TRANSIENT_MARKERS = ("cuda out of memory", "resource temporarily unavailable", "timed out", "timeout")


def timestamp() -> str:
    return datetime.now(timezone.utc).isoformat()


def classify_failure(returncode: int, stdout: str, stderr: str) -> tuple[str, str]:
    detail = (stderr or stdout).strip()
    lowered = detail.lower()
    if returncode in {137, 143} or any(marker in lowered for marker in TRANSIENT_MARKERS):
        category = "transient"
    elif any(marker in lowered for marker in STARTUP_MARKERS):
        category = "startup"
    else:
        category = "analysis"
    last_line = next((line.strip() for line in reversed(detail.splitlines()) if line.strip()), f"returncode {returncode}")
    return category, f"{category}:{last_line[-500:]}"


def append_journal(path: Path, payload: dict) -> None:
    with JOURNAL_LOCK:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, sort_keys=True) + "\n")
            handle.flush()
            os.fsync(handle.fileno())


def pipeline_invocation(job: dict, release: dict, staging: Path, pipeline_script: Path,
                        fail_on_qc: bool) -> list[str]:
    command = [
        sys.executable, str(pipeline_script),
        "--well-manifest", str(Path(job["manifest"]).resolve()),
        "--config", release["config"]["path"],
        "--source-root", release["source_root"],
        "--output-dir", str(staging),
    ]
    if fail_on_qc:
        command.append("--fail-on-qc")
    return command


@contextmanager
def gpu_reservation(gpu: int | None):
    """Serialize GPU use across independent PiHCA runner processes on one host."""
    if gpu is None:
        yield None
        return
    lock_path = Path("/tmp") / f"pihca-gpu-{gpu}.lock"
    with lock_path.open("a", encoding="utf-8") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            yield str(lock_path)
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def run_job(job: dict, release: dict, output_root: Path, retries: int, gpus: list[int], job_index: int,
            pipeline_script: Path, journal: Path | None = None, fail_on_qc: bool = False) -> dict:
    well = job["well"]
    final = output_root / well
    done = final / "complete.json"
    if done.exists():
        result = {"well": well, "status": "skipped", "output": str(final), "job_index": job_index}
        if journal:
            append_journal(journal, {"timestamp": timestamp(), **result})
        return result
    staging = output_root / f".{well}.staging"
    if staging.exists():
        result = {"well": well, "status": "failed", "output": str(staging), "job_index": job_index,
                  "failure_class": "startup", "error_signature": "startup:staging directory already exists",
                  "error": "staging directory already exists; use hca_recover.py to archive it before retrying"}
        if journal:
            append_journal(journal, {"timestamp": timestamp(), **result})
        return result
    staging.mkdir(parents=True, exist_ok=False)
    gpu = gpus[job_index % len(gpus)] if gpus else None
    invocation = pipeline_invocation(job, release, staging, pipeline_script, fail_on_qc)
    env = os.environ.copy()
    if gpu is not None:
        env["CUDA_VISIBLE_DEVICES"] = str(gpu)
    attempts = []
    failure_class = error_signature = None
    with gpu_reservation(gpu) as gpu_lock:
        for attempt in range(retries + 1):
            started = time.time()
            process = subprocess.run(invocation, cwd=staging, env=env, capture_output=True, text=True)
            attempt_record = {"attempt": attempt + 1, "returncode": process.returncode,
                              "seconds": round(time.time() - started, 3), "stdout": process.stdout[-4000:],
                              "stderr": process.stderr[-4000:]}
            attempts.append(attempt_record)
            if process.returncode == 0:
                complete = {"well": well, "job": job, "gpu": gpu, "gpu_lock": gpu_lock,
                            "invocation": invocation, "release_id": release["id"], "attempts": attempts}
                atomic_write_json(staging / "complete.json", complete)
                os.replace(staging, final)
                result = {"well": well, "status": "complete", "output": str(final), "gpu": gpu,
                          "job_index": job_index, "attempts": len(attempts)}
                if journal:
                    append_journal(journal, {"timestamp": timestamp(), **result})
                return result
            failure_class, error_signature = classify_failure(process.returncode, process.stdout, process.stderr)
            if failure_class != "transient":
                break
    error = {"well": well, "job": job, "gpu": gpu, "invocation": invocation, "attempts": attempts,
             "failure_class": failure_class, "error_signature": error_signature}
    atomic_write_json(staging / "error.json", error)
    result = {"well": well, "status": "failed", "output": str(staging), "gpu": gpu,
              "job_index": job_index, "attempts": len(attempts), "failure_class": failure_class,
              "error_signature": error_signature}
    if journal:
        append_journal(journal, {"timestamp": timestamp(), **result})
    return result


def preflight(plan: dict, release: dict, jobs: list[dict], run_dir: Path, pipeline_script: Path) -> None:
    if not pipeline_script.is_file():
        raise ValueError(f"pipeline script does not exist: {pipeline_script}")
    source_manifest = Path(plan.get("source_manifest", ""))
    if not source_manifest.is_file() or sha256(source_manifest) != release["manifest"]["sha256"]:
        raise ValueError("well plan source manifest does not match the approved release")
    existing_release = run_dir / "release.json"
    if existing_release.exists():
        recorded = json.loads(existing_release.read_text(encoding="utf-8"))
        if recorded.get("id") != release["id"]:
            raise ValueError("run directory is already bound to a different release")
    wells = run_dir / "wells"
    stale = [str(wells / f".{job['well']}.staging") for job in jobs if (wells / f".{job['well']}.staging").exists()]
    if stale:
        raise ValueError(f"preflight found {len(stale)} stale staging directories; archive them with hca_recover.py")


def execute(plan: dict, release: dict, run_dir: Path, workers: int, retries: int, gpus: list[int],
            fail_fast_count: int, pipeline_script: Path, canary_well: str | None = None,
            fail_on_qc: bool = False) -> tuple[list[dict], bool]:
    indexed = list(enumerate(plan["jobs"]))
    if canary_well:
        indexed = [(index, job) for index, job in indexed if job["well"] == canary_well]
        if not indexed:
            raise ValueError(f"canary well is not present in the plan: {canary_well}")
    jobs = [job for _, job in indexed]
    preflight(plan, release, jobs, run_dir, pipeline_script)
    wells = run_dir / "wells"
    wells.mkdir(parents=True, exist_ok=True)
    atomic_write_json(run_dir / "release.json", {"id": release["id"], "path": release["_path"],
                                                   "sha256": sha256(Path(release["_path"]))})
    journal = run_dir / "journal.jsonl"
    status_path = run_dir / "status.json"
    results: list[dict] = []
    signatures: Counter[str] = Counter()
    aborted = False
    next_job = 0
    active = {}

    def update_status() -> None:
        counts = Counter(item["status"] for item in results)
        atomic_write_json(status_path, {"updated_at": timestamp(), "release_id": release["id"],
            "total": len(indexed), "completed": len(results), "counts": dict(counts),
            "running": len(active), "not_started": len(indexed) - len(results) - len(active), "aborted": aborted})

    with ThreadPoolExecutor(max_workers=workers) as executor:
        while next_job < len(indexed) and len(active) < workers:
            index, job = indexed[next_job]; next_job += 1
            active[executor.submit(run_job, job, release, wells, retries, gpus, index,
                                   pipeline_script, journal, fail_on_qc)] = (index, job)
        update_status()
        while active:
            done, _ = wait(active, return_when=FIRST_COMPLETED)
            for future in done:
                active.pop(future)
                result = future.result()
                results.append(result)
                if result.get("failure_class") == "startup":
                    signatures[result["error_signature"]] += 1
                    if signatures[result["error_signature"]] >= fail_fast_count:
                        aborted = True
                print(json.dumps({"event": "well_finished", **result}), flush=True)
            while not aborted and next_job < len(indexed) and len(active) < workers:
                index, job = indexed[next_job]; next_job += 1
                active[executor.submit(run_job, job, release, wells, retries, gpus, index,
                                       pipeline_script, journal, fail_on_qc)] = (index, job)
            update_status()
    if aborted and next_job < len(indexed):
        for index, job in indexed[next_job:]:
            results.append({"well": job["well"], "status": "not_started", "job_index": index,
                            "reason": "batch aborted after repeated startup failures"})
    return sorted(results, key=lambda item: item["job_index"]), aborted


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", required=True, type=Path)
    parser.add_argument("--run-dir", required=True, type=Path)
    parser.add_argument("--release", required=True, type=Path)
    parser.add_argument("--pipeline-script", type=Path, default=Path(__file__).parent / "hca_pipeline.py")
    parser.add_argument("--workers", type=int, default=1)
    parser.add_argument("--retries", type=int, default=1)
    parser.add_argument("--gpus", default="auto", help="auto, none, or comma-separated GPU IDs")
    parser.add_argument("--min-free-gib", type=int, default=8)
    parser.add_argument("--fail-fast-count", type=int, default=3)
    parser.add_argument("--canary-well")
    parser.add_argument("--fail-on-qc", action="store_true")
    args = parser.parse_args()
    try:
        if args.workers < 1 or args.retries < 0 or args.fail_fast_count < 1:
            raise ValueError("workers and fail-fast-count must be positive; retries cannot be negative")
        release_path = args.release.expanduser().resolve()
        release = verify_release(release_path)
        release["_path"] = str(release_path)
        ready, errors = verify(Path(release["runtime_lock"]["path"]))
        if not ready:
            raise ValueError("runtime lock verification failed: " + "; ".join(errors))
        plan = json.loads(args.plan.read_text(encoding="utf-8"))
        inventory = gpu_inventory()
        if args.gpus == "none":
            gpus = []
        elif args.gpus == "auto":
            gpus = [gpu["index"] for gpu in inventory if gpu["free_mib"] >= args.min_free_gib * 1024]
        else:
            gpus = [int(value) for value in args.gpus.split(",")]
            available = {gpu["index"] for gpu in inventory}
            if missing := sorted(set(gpus) - available):
                raise ValueError(f"requested GPUs are unavailable: {missing}")
        requires_gpu = any(stage.get("enabled") and stage.get("gpu") for stage in
                           json.loads(Path(release["config"]["path"]).read_text())["analysis"].get("segmentation", {}).values()
                           if isinstance(stage, dict))
        if requires_gpu and not gpus:
            raise ValueError("approved configuration requires a GPU, but none passed admission control")
        workers = min(args.workers, len(gpus)) if requires_gpu else args.workers
        run_dir = args.run_dir.expanduser().resolve()
        run_dir.mkdir(parents=True, exist_ok=True)
        atomic_write_json(run_dir / "provenance.json", provenance(Path(plan["source_manifest"]),
                          Path(release["config"]["path"]), Path(release["runtime_lock"]["path"])))
        results, aborted = execute(plan, release, run_dir, workers, args.retries, gpus,
                                   args.fail_fast_count, args.pipeline_script.expanduser().resolve(),
                                   args.canary_well, args.fail_on_qc)
        summary = {"release_id": release["id"], "aborted": aborted, "results": results,
                   "counts": dict(Counter(item["status"] for item in results))}
        atomic_write_json(run_dir / "run-summary.json", summary)
        print(json.dumps({"event": "run_finished", **summary["counts"], "aborted": aborted,
                          "summary": str(run_dir / "run-summary.json")}), flush=True)
        return 0 if not aborted and all(item["status"] not in {"failed", "not_started"} for item in results) else 2
    except (KeyError, OSError, ValueError, json.JSONDecodeError) as error:
        print(json.dumps({"error": str(error)}), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
