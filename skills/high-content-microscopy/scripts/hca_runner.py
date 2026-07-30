#!/usr/bin/env python3
"""Run one plate's well jobs atomically with bounded CPU/GPU concurrency and retries."""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from hca_contract import gpu_inventory, provenance


def run_job(job: dict, command: str, output_root: Path, retries: int, gpus: list[int], config: Path | None = None) -> dict:
    well = job["well"]
    final = output_root / well
    done = final / "complete.json"
    if done.exists():
        return {"well": well, "status": "skipped", "output": str(final)}
    staging = output_root / f".{well}.staging"
    if staging.exists():
        return {"well": well, "status": "failed", "output": str(staging),
                "error": "staging directory already exists; inspect or archive it before retrying"}
    staging.mkdir(parents=True, exist_ok=True)
    gpu = gpus[hash(well) % len(gpus)] if gpus else None
    rendered = command.format(well=well, manifest=job["manifest"], output=str(staging), gpu="" if gpu is None else gpu,
                               config="" if config is None else str(config))
    env = os.environ.copy()
    if gpu is not None:
        env["CUDA_VISIBLE_DEVICES"] = str(gpu)
    attempts = []
    for attempt in range(retries + 1):
        started = time.time()
        result = subprocess.run(rendered, shell=True, cwd=staging, env=env, capture_output=True, text=True)
        attempts.append({"attempt": attempt + 1, "returncode": result.returncode, "seconds": time.time() - started,
                         "stdout": result.stdout[-4000:], "stderr": result.stderr[-4000:]})
        if result.returncode == 0:
            (staging / "complete.json").write_text(json.dumps({"well": well, "job": job, "gpu": gpu, "attempts": attempts}, indent=2) + "\n")
            os.replace(staging, final)
            return {"well": well, "status": "complete", "output": str(final)}
    (staging / "error.json").write_text(json.dumps({"well": well, "job": job, "gpu": gpu, "attempts": attempts}, indent=2) + "\n")
    return {"well": well, "status": "failed", "output": str(staging)}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--command", required=True, help="Shell template using {well}, {manifest}, {output}, and {gpu}")
    parser.add_argument("--config", type=Path, help="Assay config exposed to the command as {config} and recorded in provenance")
    parser.add_argument("--workers", type=int, default=1)
    parser.add_argument("--retries", type=int, default=1)
    parser.add_argument("--gpus", default="auto", help="auto, none, or comma-separated GPU IDs")
    parser.add_argument("--min-free-gib", type=int, default=8)
    args = parser.parse_args()
    plan = json.loads(args.plan.read_text())
    inventory = gpu_inventory()
    if args.gpus == "none":
        gpus = []
    elif args.gpus == "auto":
        gpus = [gpu["index"] for gpu in inventory if gpu["free_mib"] >= args.min_free_gib * 1024]
    else:
        gpus = [int(value) for value in args.gpus.split(",")]
    if args.workers < 1:
        parser.error("--workers must be at least 1")
    output = args.output_dir
    output.mkdir(parents=True, exist_ok=True)
    (output / "provenance.json").write_text(json.dumps(provenance(Path(plan["source_manifest"]), args.config), indent=2) + "\n")
    with ThreadPoolExecutor(max_workers=args.workers) as executor:
        futures = [executor.submit(run_job, job, args.command, output, args.retries, gpus, args.config) for job in plan["jobs"]]
        results = [future.result() for future in as_completed(futures)]
    (output / "run-summary.json").write_text(json.dumps(sorted(results, key=lambda item: item["well"]), indent=2) + "\n")
    return 0 if all(result["status"] != "failed" for result in results) else 2


if __name__ == "__main__":
    raise SystemExit(main())
