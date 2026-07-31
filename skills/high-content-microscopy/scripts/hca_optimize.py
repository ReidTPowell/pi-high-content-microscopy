#!/usr/bin/env python3
"""Track bounded human or vision-guided segmentation optimization rounds."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from hca_contract import atomic_write_json
from hca_review_ui import recommendations


def selected_review(review: dict) -> dict | None:
    selected = review.get("selected_candidate")
    if isinstance(selected, dict):
        return selected
    if selected is None and isinstance(review.get("reference_candidate"), dict):
        return review["reference_candidate"]
    return next((item for item in review.get("candidate_reviews", []) if item.get("id") == selected), None)


def initialize(mode: str, candidates: Path, max_rounds: int, acceptance_score: float) -> dict:
    return {"schema_version": 1, "mode": mode, "round": 1, "max_rounds": max_rounds,
            "acceptance_score": acceptance_score, "status": "review_required",
            "candidate_history": [str(candidates.resolve())], "review_history": [], "next_sweep": None}


def advance(state: dict, review_path: Path, next_candidates: Path | None = None) -> dict:
    review = json.loads(review_path.read_text(encoding="utf-8"))
    selected = selected_review(review)
    if not selected:
        raise ValueError("review does not select an acceptable candidate")
    updated = dict(state)
    updated["review_history"] = [*state.get("review_history", []), str(review_path.resolve())]
    updated["selected_candidate"] = selected
    human_approved = review.get("review_status") == "approved"
    score = float(selected.get("objective_score", selected.get("score", 0)))
    if state["mode"] == "human" and human_approved:
        updated.update({"status": "complete", "next_sweep": None})
        return updated
    if state["mode"] == "automated" and selected.get("acceptable") and score >= state["acceptance_score"]:
        updated.update({"status": "human_approval_required", "next_sweep": None})
        return updated
    if state["round"] >= state["max_rounds"]:
        updated.update({"status": "human_intervention_required", "next_sweep": None})
        return updated
    normalized = dict(review)
    normalized["selected_candidate"] = selected["id"]
    normalized["candidate_reviews"] = review.get("candidate_reviews", [selected])
    proposal = recommendations(normalized)
    updated.update({"round": state["round"] + 1, "status": "next_sweep_required",
                    "next_sweep": proposal.get("next_sweep")})
    if next_candidates:
        updated["candidate_history"] = [*state.get("candidate_history", []), str(next_candidates.resolve())]
    return updated


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="action", required=True)
    init = commands.add_parser("init")
    init.add_argument("--mode", choices=["human", "automated"], required=True)
    init.add_argument("--candidates", required=True, type=Path)
    init.add_argument("--max-rounds", type=int, default=3)
    init.add_argument("--acceptance-score", type=float, default=90)
    init.add_argument("--output", required=True, type=Path)
    step = commands.add_parser("advance")
    step.add_argument("--state", required=True, type=Path)
    step.add_argument("--review", required=True, type=Path)
    step.add_argument("--next-candidates", type=Path)
    step.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    try:
        if args.action == "init":
            if args.max_rounds < 1 or not 0 <= args.acceptance_score <= 100:
                parser.error("max rounds must be positive and acceptance score must be 0..100")
            payload = initialize(args.mode, args.candidates, args.max_rounds, args.acceptance_score)
        else:
            state = json.loads(args.state.read_text(encoding="utf-8"))
            payload = advance(state, args.review, args.next_candidates)
        atomic_write_json(args.output, payload)
        print(json.dumps({"status": payload["status"], "round": payload["round"], "output": str(args.output)}, indent=2))
        return 0
    except (OSError, ValueError, KeyError, json.JSONDecodeError) as error:
        print(json.dumps({"error": str(error)}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
