#!/usr/bin/env python3
"""Create reproducible, sequential-plate batch plans with parallel well jobs."""
from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plates", required=True, type=Path, help="Output from hca_manifest.py --discover-plates")
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--workers-per-plate", type=int, default=1)
    args = parser.parse_args()
    if args.workers_per_plate < 1:
        parser.error("--workers-per-plate must be at least 1")
    script_dir = Path(__file__).parent
    plates = json.loads(args.plates.read_text())["plates"]
    result = []
    for number, plate in enumerate(plates, start=1):
        plate_dir = args.output_dir / f"plate-{number:03d}"
        manifest, summary, plan_dir = plate_dir / "manifest.jsonl", plate_dir / "summary.json", plate_dir / "well-jobs"
        subprocess.run(["python3", str(script_dir / "hca_manifest.py"), "--input", plate, "--output", str(manifest), "--summary", str(summary)], check=True)
        subprocess.run(["python3", str(script_dir / "hca_validate.py"), "--manifest", str(manifest), "--config", str(args.config), "--output", str(plate_dir / "validation.json")], check=True)
        subprocess.run(["python3", str(script_dir / "hca_well_plan.py"), "--manifest", str(manifest), "--output-dir", str(plan_dir), "--workers", str(args.workers_per_plate)], check=True)
        result.append({"plate": plate, "manifest": str(manifest), "well_plan": str(plan_dir / "plan.json"), "workers": args.workers_per_plate})
    payload = {"execution": "sequential plates; parallel wells within each plate", "config": str(args.config), "plates": result}
    args.output_dir.mkdir(parents=True, exist_ok=True)
    (args.output_dir / "batch-plan.json").write_text(json.dumps(payload, indent=2) + "\n")
    print(json.dumps({"plates": len(result), "workers_per_plate": args.workers_per_plate, "output": str(args.output_dir / "batch-plan.json")}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
