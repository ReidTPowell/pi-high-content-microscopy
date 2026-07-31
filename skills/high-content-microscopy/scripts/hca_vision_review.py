#!/usr/bin/env python3
"""Create and finalize reproducible vision-model QC reviews for Cellpose and filtering pilots."""
from __future__ import annotations

import argparse
import json
from pathlib import Path


def load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def template(candidates: Path, audits: list[Path]) -> dict:
    candidate_data = load(candidates)
    entries = []
    for candidate in candidate_data.get("candidates", []):
        entries.append({"id": candidate["id"], "overlay": candidate.get("overlay"), "parameters": candidate["parameters"],
                        "object_count": candidate.get("object_count"), "score": None, "acceptable": None,
                        "relationship": candidate.get("relationship"), "issues": [],
                        "oversegmentation": None, "undersegmentation": None, "boundary_quality": None, "notes": None})
    filter_entries = []
    for path in audits:
        audit = load(path)
        filter_entries.append({"audit": str(path), "criteria": audit.get("criteria", {}),
                               "reviewed_source_labels": [], "recommended_filter": None,
                               "notes": "Inspect source_label and centroid_yx against the raw and overlay images."})
    return {"schema_version": 1, "status": "pending_vision_review", "reviewer": None,
            "image": candidate_data.get("image"), "model": candidate_data.get("model"),
            "instructions": ["A vision-capable model must inspect every overlay against its raw image.",
                             "Score boundary quality from 0 to 100; lower scores indicate biologically implausible split, merge, missed, or spurious objects.",
                             "Do not select on object count alone. Record uncertainty and set acceptable=false when a candidate is not defensible.",
                             "For filtering, identify source labels by audit centroid and recommend only criteria supported by the review."],
            "candidate_reviews": entries, "filter_reviews": filter_entries}


def finalize(review: dict) -> dict:
    if not review.get("reviewer"):
        raise ValueError("reviewer must identify the vision model or human reviewer")
    accepted = []
    for entry in review.get("candidate_reviews", []):
        if not isinstance(entry.get("score"), (int, float)) or not 0 <= entry["score"] <= 100:
            raise ValueError(f"candidate {entry.get('id')} requires a score from 0 to 100")
        if not isinstance(entry.get("acceptable"), bool):
            raise ValueError(f"candidate {entry.get('id')} requires an acceptable true/false decision")
        relationship = entry.get("relationship") or {}
        nuclei = max(int(relationship.get("nuclei", 0)), 1)
        entry["objective_score"] = round(
            float(entry["score"]) - 30.0 * relationship.get("orphan", 0) / nuclei
            - 40.0 * relationship.get("ambiguous", 0) / nuclei, 3
        )
        if entry["acceptable"]:
            accepted.append(entry)
    ranked = sorted(accepted, key=lambda entry: (-entry["objective_score"], entry["id"]))
    all_ranked = sorted(review.get("candidate_reviews", []), key=lambda entry: (-entry["objective_score"], entry["id"]))
    return {"schema_version": 1, "status": "human_approval_required" if ranked else "refinement_required",
            "reviewer": review["reviewer"],
            "selected_candidate": ranked[0] if ranked else None, "reference_candidate": all_ranked[0] if all_ranked else None,
            "ranked_acceptable_candidates": ranked,
            "filter_reviews": review.get("filter_reviews", []),
            "decision_rule": "The top acceptable candidate is ranked by vision score with orphan and ambiguous relationship penalties. It remains a proposal until a named human approves the config."}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="action", required=True)
    create = commands.add_parser("template")
    create.add_argument("--candidates", required=True, type=Path, help="candidates.json from hca_cellpose_tune.py")
    create.add_argument("--filter-audit", action="append", default=[], type=Path)
    create.add_argument("--output", required=True, type=Path)
    complete = commands.add_parser("finalize")
    complete.add_argument("--review", required=True, type=Path, help="Completed template produced by a vision-capable Pi session")
    complete.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    try:
        payload = template(args.candidates, args.filter_audit) if args.action == "template" else finalize(load(args.review))
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        print(json.dumps({"status": payload["status"], "output": str(args.output),
                          "selected_candidate": payload.get("selected_candidate", {}).get("id") if payload.get("selected_candidate") else None}, indent=2))
        return 0
    except (OSError, ValueError, json.JSONDecodeError) as error:
        print(json.dumps({"error": str(error)}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
