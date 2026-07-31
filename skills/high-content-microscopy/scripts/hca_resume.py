#!/usr/bin/env python3
"""Locate and audit a persisted PiHCA workflow before cross-session resume."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from hca_contract import default_output_dir, sha256
from hca_manifest import discover_plates


PHASES = {
    "assay_contract_required", "runtime_setup_required", "pilot_segmentation_required",
    "nuclei_review_required", "cell_segmentation_required", "cell_review_required",
    "filter_review_required", "heldout_validation_required", "release_approval_required",
    "production_canary_required", "batch_approval_required", "batch_running",
    "plate_qc_required", "complete",
}


def candidates(input_root: Path, workflow: Path | None) -> list[Path]:
    if workflow:
        return [workflow]
    plates = discover_plates(input_root) or [input_root]
    return sorted({default_output_dir(plate) / "workflow-state.json" for plate in plates
                   if (default_output_dir(plate) / "workflow-state.json").is_file()})


def audit(path: Path) -> dict:
    state = json.loads(path.read_text(encoding="utf-8"))
    if state.get("phase") not in PHASES:
        raise ValueError(f"workflow has an unsupported phase: {state.get('phase')!r}")
    for key in ("input", "output", "config", "manifest", "well_plan", "runtime_lock"):
        artifact = Path(state.get(key, "")).expanduser()
        if not artifact.exists():
            raise ValueError(f"workflow {key} is missing: {artifact}")
    config = Path(state["config"])
    if state.get("config_sha256") != sha256(config):
        raise ValueError("workflow config hash mismatch; this workflow is quarantined")
    for review in state.get("review_history", []):
        if review.get("stage") not in {"nucleus", "cell", "filter"} or not review.get("path"):
            raise ValueError("workflow review history contains an unbound record")
        artifact = Path(review["path"])
        if not artifact.is_file() or review.get("sha256") != sha256(artifact):
            raise ValueError(f"workflow review changed or is missing: {review.get('stage')}")
    return {"status": "resumable", "workflow_state": str(path.resolve()),
            "phase": state["phase"], "input": state["input"], "output": state["output"],
            "selected_acquisition": state["input"], "next_action": state.get("next_action")}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--workflow-state", type=Path)
    args = parser.parse_args()
    try:
        root = args.input.expanduser().resolve()
        workflows = candidates(root, args.workflow_state.expanduser().resolve() if args.workflow_state else None)
        if not workflows:
            print(json.dumps({"status": "not_found", "input": str(root)}))
            return 3
        if len(workflows) > 1:
            print(json.dumps({"status": "selection_required", "workflows": [str(path) for path in workflows]}))
            return 4
        print(json.dumps(audit(workflows[0]), indent=2))
        return 0
    except (OSError, ValueError, KeyError, json.JSONDecodeError) as error:
        print(json.dumps({"status": "integrity_error", "error": str(error)}), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
