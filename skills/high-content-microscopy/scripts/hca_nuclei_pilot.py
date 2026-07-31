#!/usr/bin/env python3
"""Run one bounded nuclei Cellpose sweep from a prepared PiHCA workflow."""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

from hca_contract import atomic_write_json


def next_pilot(output: Path) -> Path:
    pilots = output / "pilots"
    versions = [int(path.name.rsplit("-", 1)[1]) for path in pilots.glob("pilot-*")
                if path.name.rsplit("-", 1)[-1].isdigit()]
    return pilots / f"pilot-{max(versions, default=0) + 1:03d}"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workflow-state", required=True, type=Path)
    parser.add_argument("--field-index", type=int, default=1,
                        help="Zero-based pilot field; defaults to the median-ranked field")
    parser.add_argument("--diameters", default="auto,18,24")
    parser.add_argument("--flow-thresholds", default="0.3,0.4")
    parser.add_argument("--cellprob-thresholds", default="-1,0")
    parser.add_argument("--gpus", default="auto")
    parser.add_argument("--workers", type=int, default=0)
    args = parser.parse_args()

    state_path = args.workflow_state.expanduser().resolve()
    state = json.loads(state_path.read_text(encoding="utf-8"))
    if state.get("phase") != "pilot_segmentation_required":
        parser.error(f"workflow phase is {state.get('phase')!r}, expected 'pilot_segmentation_required'")
    config = json.loads(Path(state["config"]).read_text(encoding="utf-8"))
    plan = json.loads(Path(state["pilot_fields"]).read_text(encoding="utf-8"))
    if not 0 <= args.field_index < len(plan["fields"]):
        parser.error(f"--field-index must be between 0 and {len(plan['fields']) - 1}")

    nucleus_stage = config["analysis"]["segmentation"]["nucleus"]
    role = nucleus_stage["channel_role"]
    channels = [int(channel) for channel, details in config["channels"].items() if details.get("role") == role]
    if len(channels) != 1:
        parser.error(f"expected exactly one channel with role {role!r}, found {channels}")
    field = plan["fields"][args.field_index]
    image = Path(state["input"]) / field["channels"][str(channels[0])]["path"]
    pilot = next_pilot(Path(state["output"]))
    candidates = pilot / "nuclei" / f"{field['well']}-{field['site']}"
    command = [
        sys.executable, str(Path(__file__).parent / "hca_cellpose_tune.py"),
        "--image", str(image), "--model", nucleus_stage.get("model", "nuclei"),
        f"--diameters={args.diameters}", f"--flow-thresholds={args.flow_thresholds}",
        f"--cellprob-thresholds={args.cellprob_thresholds}", "--output-dir", str(candidates),
    ]
    if nucleus_stage.get("gpu"):
        command.extend(["--gpu", "--gpus", args.gpus, "--workers", str(args.workers)])
    process = subprocess.run(command, capture_output=True, text=True)
    if process.returncode:
        raise RuntimeError(process.stderr.strip() or process.stdout.strip() or "nuclei Cellpose sweep failed")

    state.update({
        "phase": "nuclei_review_required",
        "pilot": str(pilot),
        "pilot_field": field,
        "nuclei_candidates": str(candidates / "candidates.json"),
        "next_action": (
            "Open human review or perform structured vision review of every raw/overlay candidate. "
            "Select nuclei parameters before tuning cells."
        ),
    })
    atomic_write_json(state_path, state)
    print(json.dumps({
        "status": "nuclei_review_required",
        "field": {key: field[key] for key in ("well", "site", "timepoint", "z", "combined_intensity_rank")},
        "image": str(image),
        "candidates": str(candidates / "candidates.json"),
        "workflow_state": str(state_path),
        "next_action": state["next_action"],
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
