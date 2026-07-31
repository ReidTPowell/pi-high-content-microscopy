#!/usr/bin/env python3
"""Run a bounded secondary-cell sweep from accepted PiHCA nuclei."""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

from hca_contract import atomic_write_json


def role_channel(config: dict, role: str) -> int:
    channels = [int(channel) for channel, details in config["channels"].items() if details.get("role") == role]
    if len(channels) != 1:
        raise ValueError(f"expected exactly one channel with role {role!r}, found {channels}")
    return channels[0]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workflow-state", required=True, type=Path)
    parser.add_argument("--diameters", default="auto,24,30")
    parser.add_argument("--flow-thresholds", default="0.3,0.4")
    parser.add_argument("--cellprob-thresholds", default="-0.5,0")
    args = parser.parse_args()
    state_path = args.workflow_state.expanduser().resolve()
    state = json.loads(state_path.read_text(encoding="utf-8"))
    if state.get("phase") != "cell_segmentation_required":
        parser.error(f"workflow phase is {state.get('phase')!r}, expected 'cell_segmentation_required'")
    config = json.loads(Path(state["config"]).read_text(encoding="utf-8"))
    field = state.get("pilot_field")
    if not field:
        parser.error("workflow has no recorded pilot field")
    nucleus = config["analysis"]["segmentation"]["nucleus"]
    cell = config["analysis"]["segmentation"]["cell"]
    nucleus_image = Path(state["input"]) / field["channels"][str(role_channel(config, nucleus["channel_role"]))]["path"]
    cell_image = Path(state["input"]) / field["channels"][str(role_channel(config, cell["channel_role"]))]["path"]
    labels = Path(state["accepted"]["nucleus"]["labels"])
    candidates = Path(state["pilot"]) / "cell" / f"{field['well']}-{field['site']}"
    command = [
        sys.executable, str(Path(__file__).parent / "hca_cellpose_tune.py"),
        "--image", str(cell_image), "--nuclear-image", str(nucleus_image),
        "--reference-nuclei", str(labels), "--model", cell.get("model", "cyto3"),
        f"--diameters={args.diameters}", f"--flow-thresholds={args.flow_thresholds}",
        f"--cellprob-thresholds={args.cellprob_thresholds}", "--output-dir", str(candidates),
        "--min-overlap", str(config["analysis"]["segmentation"].get("relationship", {}).get("min_overlap", 0.5)),
    ]
    if cell.get("gpu"):
        command.append("--gpu")
    result = subprocess.run(command, capture_output=True, text=True)
    if result.returncode:
        raise RuntimeError(result.stderr.strip() or result.stdout.strip() or "cell Cellpose sweep failed")
    state.update({"phase": "cell_review_required", "cell_candidates": str(candidates / "candidates.json"),
                  "next_action": "Review cell boundaries and nucleus-to-cell relationship QC before filters."})
    atomic_write_json(state_path, state)
    print(json.dumps({"status": state["phase"], "candidates": state["cell_candidates"],
                      "workflow_state": str(state_path), "next_action": state["next_action"]}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
