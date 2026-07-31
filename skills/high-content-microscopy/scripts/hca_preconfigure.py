#!/usr/bin/env python3
"""Prepare a safe Pi HCA pilot packet: manifest, validation, well plan, QC, and pending review."""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

from hca_contract import default_output_dir


def invoke(command: list[str], allowed: set[int] = {0}) -> dict:
    result = subprocess.run(command, capture_output=True, text=True)
    if result.returncode not in allowed:
        raise RuntimeError(result.stderr.strip() or result.stdout.strip() or f"stage failed: {' '.join(command)}")
    return {"command": command, "returncode": result.returncode, "stdout": result.stdout[-2000:], "stderr": result.stderr[-2000:]}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, type=Path, help="One acquisition root, never a multi-plate batch root")
    parser.add_argument("--config", required=True, type=Path, help="Draft assay configuration selected after channel-role confirmation")
    parser.add_argument("--plate-map", type=Path, help="Optional CSV with a required well column and experimental metadata")
    parser.add_argument("--output-dir", type=Path, help="Defaults to <Barcode>_piHCA/preconfiguration")
    parser.add_argument("--workers", type=int, default=1)
    parser.add_argument("--sample-size", type=int, default=48)
    parser.add_argument("--seed", type=int, default=7)
    args = parser.parse_args()
    if args.workers < 1:
        parser.error("--workers must be at least 1")
    output = args.output_dir or default_output_dir(args.input) / "preconfiguration"
    output.mkdir(parents=True, exist_ok=True)
    script_dir = Path(__file__).parent
    manifest, curated_manifest = output / "manifest.jsonl", output / "manifest.curated.jsonl"
    validation, qc = output / "validation.json", output / "qc.json"
    review, plan_dir = output / "review.pending.json", output / "well-jobs"
    stages = []
    stages.append(invoke([sys.executable, str(script_dir / "hca_doctor.py"), "--config", str(args.config), "--source-root", str(args.input)], {0, 2}))
    stages.append(invoke([sys.executable, str(script_dir / "hca_preflight.py"), "--config", str(args.config)], {0, 2}))
    stages.append(invoke([sys.executable, str(script_dir / "hca_manifest.py"), "--input", str(args.input), "--output", str(manifest), "--summary", str(output / "manifest-summary.json")]))
    metadata_command = [sys.executable, str(script_dir / "hca_metadata.py"), "--manifest", str(manifest),
                        "--output", str(curated_manifest), "--report", str(output / "metadata-report.json")]
    if args.plate_map:
        metadata_command.extend(["--plate-map", str(args.plate_map)])
    stages.append(invoke(metadata_command))
    stages.append(invoke([sys.executable, str(script_dir / "hca_validate.py"), "--manifest", str(curated_manifest), "--config", str(args.config), "--output", str(validation)]))
    stages.append(invoke([sys.executable, str(script_dir / "hca_well_plan.py"), "--manifest", str(curated_manifest), "--output-dir", str(plan_dir), "--workers", str(args.workers)]))
    stages.append(invoke([sys.executable, str(script_dir / "hca_qc.py"), "--manifest", str(curated_manifest), "--output", str(qc), "--sample-size", str(args.sample_size), "--seed", str(args.seed)]))
    stages.append(invoke([sys.executable, str(script_dir / "hca_review.py"), "--qc", str(qc), "--output", str(review)], {0, 3}))
    questions = [
        "Confirm each channel role and the intended primary object (for example nuclei).",
        "Confirm the secondary object boundary/cytoplasm channel and whether it should be guided by the primary raw image.",
        "Provide controls, expected object morphology, and unacceptable objects visible in pilot overlays.",
        "After the pilot, approve reviewed size and intensity filter limits before batch submission.",
    ]
    (output / "operator-questions.json").write_text(json.dumps({"questions": questions}, indent=2) + "\n", encoding="utf-8")
    ready = all(stage["returncode"] == 0 for stage in stages[:-1])
    status = "pending_human_review" if ready else "runtime_or_model_setup_required"
    (output / "preconfiguration.json").write_text(json.dumps({"input": str(args.input.resolve()), "config": str(args.config.resolve()),
        "manifest": str(curated_manifest), "source_manifest": str(manifest), "metadata_report": str(output / "metadata-report.json"),
        "well_plan": str(plan_dir / "plan.json"), "qc": str(qc), "review": str(review),
        "status": status, "stages": stages}, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": status, "output": str(output), "questions": str(output / "operator-questions.json")}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
