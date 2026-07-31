#!/usr/bin/env python3
"""Apply guarded, atomic transitions to a PiHCA workflow state."""
from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

from hca_contract import atomic_write_json, sha256
from hca_release import approved_review, passed_heldout


def next_config(config: Path) -> Path:
    numbers = []
    for path in config.parent.glob("assay-*.json"):
        try:
            numbers.append(int(path.stem.rsplit("-", 1)[1]))
        except (IndexError, ValueError):
            continue
    return config.parent / f"assay-{max(numbers, default=0) + 1:03d}.json"


def candidate_record(candidates_path: Path, review: dict) -> dict:
    candidates = json.loads(candidates_path.read_text(encoding="utf-8")).get("candidates", [])
    selected = next((item for item in candidates if item.get("id") == review.get("selected_candidate")), None)
    if not selected or selected.get("returncode") != 0 or not Path(selected.get("labels", "")).is_file():
        raise ValueError("selected candidate is missing, failed, or has no label artifact")
    return selected


def version_config(state: dict, update) -> Path:
    source = Path(state["config"])
    config = json.loads(source.read_text(encoding="utf-8"))
    update(config)
    destination = next_config(source)
    atomic_write_json(destination, config)
    state.update({"config": str(destination), "config_sha256": sha256(destination)})
    return destination


def record_review(state: dict, stage: str, path: Path) -> None:
    history = [item for item in state.get("review_history", []) if item.get("stage") != stage]
    history.append({"stage": stage, "path": str(path.resolve()), "sha256": sha256(path)})
    state["review_history"] = history


def accept_segmentation(state: dict, state_path: Path, stage: str, review_path: Path) -> dict:
    expected = "nuclei_review_required" if stage == "nucleus" else "cell_review_required"
    if state.get("phase") != expected:
        raise ValueError(f"workflow phase is {state.get('phase')!r}, expected {expected!r}")
    review = approved_review(review_path)
    candidates_path = Path(state[f"{stage}_candidates"])
    selected = candidate_record(candidates_path, review)

    def update(config: dict) -> None:
        target = config["analysis"]["segmentation"][stage]
        parameters = selected["parameters"]
        target["diameter"] = parameters.get("diameter")
        target.setdefault("cellpose", {}).update({
            "flow_threshold": parameters.get("flow_threshold"),
            "cellprob_threshold": parameters.get("cellprob_threshold"),
        })

    version_config(state, update)
    record_review(state, stage, review_path)
    state.setdefault("accepted", {})[stage] = {
        "candidate": selected["id"], "labels": selected["labels"], "parameters": selected["parameters"],
        "review": str(review_path.resolve()),
    }
    if stage == "nucleus" and json.loads(Path(state["config"]).read_text())["analysis"]["segmentation"].get("cell", {}).get("enabled"):
        state.update({"phase": "cell_segmentation_required",
                      "next_action": "Tune secondary cells with nuclear guidance and reviewed nuclear labels."})
    else:
        state.update({"phase": "filter_review_required",
                      "next_action": "Review explicit no-filter or size/intensity filter settings with exclusion evidence."})
    atomic_write_json(state_path, state)
    return state


def accept_filters(state: dict, state_path: Path, review_path: Path) -> dict:
    if state.get("phase") != "filter_review_required":
        raise ValueError(f"workflow phase is {state.get('phase')!r}, expected 'filter_review_required'")
    review = approved_review(review_path)
    recommendations = review.get("filter_recommendations")
    if not isinstance(recommendations, dict) or not {"nucleus", "cell"}.intersection(recommendations):
        raise ValueError("filter review must explicitly record nucleus or cell filter recommendations")
    uses_filter = any(value is not None for criteria in recommendations.values() for value in criteria.values())
    if uses_filter:
        evidence = review.get("filter_evidence", [])
        if not evidence or any(item.get("decision") not in {"accepted", "approved"} for item in evidence):
            raise ValueError("non-empty filters require accepted before/after exclusion evidence")

    def update(config: dict) -> None:
        segmentation = config["analysis"]["segmentation"]
        for object_name, criteria in recommendations.items():
            stage = "nucleus" if object_name in {"nucleus", "nuclei"} else object_name
            if stage in {"nucleus", "cell"} and stage in segmentation:
                segmentation[stage]["filter"] = criteria

    version_config(state, update)
    record_review(state, "filter", review_path)
    state.update({"phase": "heldout_validation_required",
                  "next_action": "Run the accepted config on independent held-out wells and complete visual review."})
    atomic_write_json(state_path, state)
    return state


def record_heldout(state: dict, state_path: Path, validation_path: Path) -> dict:
    if state.get("phase") != "heldout_validation_required":
        raise ValueError(f"workflow phase is {state.get('phase')!r}, expected 'heldout_validation_required'")
    config = json.loads(Path(state["config"]).read_text(encoding="utf-8"))
    passed_heldout(validation_path, config)
    destination = Path(state["output"]) / "validation" / "heldout-validation.json"
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists() and sha256(destination) != sha256(validation_path):
        raise ValueError("held-out validation is immutable; create a new workflow revision")
    if not destination.exists():
        shutil.copy2(validation_path, destination)
    state.update({"phase": "release_approval_required", "heldout_validation": str(destination),
                  "next_action": "Obtain named human approval and create an immutable production release."})
    atomic_write_json(state_path, state)
    return state


def status(state: dict) -> dict:
    return {key: state.get(key) for key in ("phase", "input", "output", "config", "runtime_lock", "release", "canary", "batch_job", "next_action")}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workflow-state", required=True, type=Path)
    commands = parser.add_subparsers(dest="action", required=True)
    review = commands.add_parser("accept-review")
    review.add_argument("--stage", choices=("nucleus", "cell"), required=True)
    review.add_argument("--review", required=True, type=Path)
    filters = commands.add_parser("accept-filters")
    filters.add_argument("--review", required=True, type=Path)
    heldout = commands.add_parser("record-heldout")
    heldout.add_argument("--validation", required=True, type=Path)
    commands.add_parser("status")
    args = parser.parse_args()
    try:
        state_path = args.workflow_state.expanduser().resolve()
        state = json.loads(state_path.read_text(encoding="utf-8"))
        if args.action == "accept-review":
            payload = accept_segmentation(state, state_path, args.stage, args.review.expanduser().resolve())
        elif args.action == "accept-filters":
            payload = accept_filters(state, state_path, args.review.expanduser().resolve())
        elif args.action == "record-heldout":
            payload = record_heldout(state, state_path, args.validation.expanduser().resolve())
        else:
            payload = status(state)
        print(json.dumps(payload, indent=2))
        return 0
    except (KeyError, OSError, ValueError, json.JSONDecodeError) as error:
        print(json.dumps({"error": str(error)}), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
