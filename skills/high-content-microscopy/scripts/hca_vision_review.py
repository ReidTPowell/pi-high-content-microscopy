#!/usr/bin/env python3
"""Create and finalize reproducible vision-model QC reviews for Cellpose and filtering pilots."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from hca_contract import atomic_write_json, sha256


def load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def template(candidates: Path, audits: list[Path], review_dir: Path | None = None) -> dict:
    candidate_data = load(candidates)
    review_dir = review_dir or candidates.parent / "automated-review"
    entries = []
    successful = [candidate for candidate in candidate_data.get("candidates", [])
                  if candidate.get("returncode") == 0 and candidate.get("overlay")]
    for number, candidate in enumerate(successful, start=1):
        review_image = review_dir / "assets" / f"candidate-{number:02d}.png"
        entries.append({"id": candidate["id"], "overlay": candidate.get("overlay"), "parameters": candidate["parameters"],
                        "overlay_sha256": sha256(Path(candidate["overlay"])),
                        "review_image": str(review_image),
                        "review_image_sha256": sha256(review_image) if review_image.is_file() else None,
                        "object_count": candidate.get("object_count"), "score": None, "acceptable": None,
                        "relationship": candidate.get("relationship"), "issues": [],
                        "oversegmentation": None, "undersegmentation": None, "boundary_quality": None, "notes": None})
    filter_entries = []
    for path in audits:
        audit = load(path)
        filter_entries.append({"audit": str(path), "criteria": audit.get("criteria", {}),
                               "reviewed_source_labels": [], "recommended_filter": None,
                               "notes": "Inspect source_label and centroid_yx against the raw and overlay images."})
    raw_review = review_dir / "assets" / "raw.png"
    return {"schema_version": 1, "status": "pending_vision_review", "reviewer": None,
            "source_candidates": str(candidates.resolve()), "source_candidates_sha256": sha256(candidates),
            "raw_review_image": str(raw_review),
            "raw_review_image_sha256": sha256(raw_review) if raw_review.is_file() else None,
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
            "candidate_reviews": review.get("candidate_reviews", []),
            "filter_reviews": review.get("filter_reviews", []),
            "filter_recommendations": review.get("filter_recommendations", {}),
            "review_provenance": {key: review.get(key) for key in
                                  ("source_candidates", "source_candidates_sha256",
                                   "raw_review_image", "raw_review_image_sha256")},
            "decision_rule": "The top acceptable candidate is ranked by vision score with orphan and ambiguous relationship penalties. It remains a proposal until a named human approves the config."}


def submit(review_template: dict, reviewer: str, decisions: list[dict],
           filter_recommendations: dict | None = None) -> dict:
    expected = {entry["id"]: entry for entry in review_template.get("candidate_reviews", [])}
    supplied = {entry.get("id"): entry for entry in decisions}
    if None in supplied or set(supplied) != set(expected) or len(supplied) != len(decisions):
        raise ValueError("vision submission must contain exactly one decision for every candidate")
    completed = dict(review_template)
    completed["reviewer"] = reviewer
    completed["candidate_reviews"] = []
    completed["filter_recommendations"] = filter_recommendations or {}
    for object_name, criteria in completed["filter_recommendations"].items():
        for key, value in criteria.items():
            if value is not None and (not isinstance(value, (int, float)) or value < 0):
                raise ValueError(f"{object_name} filter {key} must be null or non-negative")
        for minimum, maximum in (("min_area_px", "max_area_px"),
                                 ("min_intensity_mean", "max_intensity_mean")):
            if criteria.get(minimum) is not None and criteria.get(maximum) is not None \
                    and criteria[minimum] > criteria[maximum]:
                raise ValueError(f"{object_name} filter {minimum} exceeds {maximum}")
    for candidate_id, entry in expected.items():
        decision = supplied[candidate_id]
        completed["candidate_reviews"].append({**entry,
            "score": decision.get("score"), "acceptable": decision.get("acceptable"),
            "issues": decision.get("issues", []), "oversegmentation": decision.get("oversegmentation"),
            "undersegmentation": decision.get("undersegmentation"),
            "boundary_quality": decision.get("boundary_quality"), "notes": decision.get("notes")})
    return finalize(completed)


def approve(proposal: dict, human_reviewer: str) -> dict:
    if proposal.get("status") != "human_approval_required" or not proposal.get("selected_candidate"):
        raise ValueError("only a complete vision proposal with an acceptable candidate can be approved")
    selected = proposal["selected_candidate"]["id"]
    return {"schema_version": 1, "review_status": "approved", "reviewer": human_reviewer,
            "vision_reviewer": proposal["reviewer"], "selected_candidate": selected,
            "candidate_reviews": proposal["candidate_reviews"],
            "review_images": [{"path": entry.get("review_image"),
                               "sha256": entry.get("review_image_sha256"), "decision": "accepted"}
                              for entry in proposal["candidate_reviews"]],
            "review_provenance": proposal.get("review_provenance", {}),
            "filter_recommendations": proposal.get("filter_recommendations", {}),
            "approval_basis": "Named human approval of a hash-bound vision-model proposal."}


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
    submission = commands.add_parser("submit")
    submission.add_argument("--template", required=True, type=Path)
    submission.add_argument("--reviewer", required=True)
    submission.add_argument("--decisions", required=True, type=Path)
    submission.add_argument("--output", required=True, type=Path)
    approval = commands.add_parser("approve")
    approval.add_argument("--proposal", required=True, type=Path)
    approval.add_argument("--reviewer", required=True)
    approval.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    try:
        if args.action == "template":
            payload = template(args.candidates.resolve(), args.filter_audit, args.output.parent.resolve())
        elif args.action == "finalize":
            payload = finalize(load(args.review))
        elif args.action == "submit":
            submitted = load(args.decisions)
            payload = submit(load(args.template), args.reviewer, submitted["decisions"],
                             submitted.get("filter_recommendations"))
        else:
            payload = approve(load(args.proposal), args.reviewer)
        atomic_write_json(args.output, payload)
        selected = payload.get("selected_candidate")
        selected_id = selected.get("id") if isinstance(selected, dict) else selected
        print(json.dumps({"status": payload.get("status", payload.get("review_status")),
                          "output": str(args.output), "selected_candidate": selected_id}, indent=2))
        return 0
    except (OSError, ValueError, json.JSONDecodeError) as error:
        print(json.dumps({"error": str(error)}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
