#!/usr/bin/env python3
"""Create and verify immutable PiHCA production releases."""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

from hca_contract import atomic_write_json, sha256


def required_review_stages(config: dict) -> set[str]:
    segmentation = config.get("analysis", {}).get("segmentation", {})
    required = {stage for stage in ("nucleus", "cell") if segmentation.get(stage, {}).get("enabled")}
    if required:
        required.add("filter")
    return required


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def approved_review(path: Path) -> dict:
    review = json.loads(path.read_text(encoding="utf-8"))
    if review.get("review_status") != "approved" or not review.get("reviewer"):
        raise ValueError(f"review is not approved by a named reviewer: {path}")
    candidates = review.get("candidate_reviews", [])
    if candidates:
        selected = review.get("selected_candidate")
        selected_review = next((item for item in candidates if item.get("id") == selected), None)
        if not selected_review or selected_review.get("acceptable") is not True:
            raise ValueError(f"selected candidate is not explicitly acceptable: {path}")
        for candidate in candidates:
            score = candidate.get("score")
            if not isinstance(score, (int, float)) or not 0 <= score <= 100:
                raise ValueError(f"every candidate requires a 0..100 score: {path}")
    images = review.get("review_images", [])
    if images and any(item.get("decision") not in {"accepted", "approved"} for item in images):
        raise ValueError(f"every review image must have an accepted decision: {path}")
    return review


def passed_heldout(path: Path, config: dict) -> dict:
    validation = json.loads(path.read_text(encoding="utf-8"))
    if validation.get("status") != "passed":
        raise ValueError("held-out validation must have status 'passed'")
    optimization = config.get("analysis", {}).get("optimization", {})
    minimum_wells = int(optimization.get("minimum_heldout_wells", 3))
    minimum_fields = int(optimization.get("minimum_heldout_fields", 9))
    wells = set(validation.get("wells", []))
    fields = validation.get("fields", [])
    field_count = len(fields) if isinstance(fields, list) else int(fields)
    if len(wells) < minimum_wells or field_count < minimum_fields:
        raise ValueError(
            f"held-out validation requires at least {minimum_wells} wells and {minimum_fields} fields; "
            f"found {len(wells)} wells and {field_count} fields"
        )
    if validation.get("visual_review_complete") is not True:
        raise ValueError("held-out validation requires completed visual review")
    if not isinstance(fields, list) or any(not isinstance(field, dict) for field in fields):
        raise ValueError("held-out validation fields must contain structured review evidence")
    for field in fields:
        if field.get("decision") not in {"accepted", "approved"}:
            raise ValueError("every held-out field must have an accepted visual decision")
        overlay = Path(field.get("overlay", ""))
        if not overlay.is_absolute():
            overlay = path.parent / overlay
        if not overlay.is_file() or sha256(overlay) != field.get("overlay_sha256"):
            raise ValueError(f"held-out overlay is missing or changed: {field.get('id')}")
    return validation


def approved_filter_review(path: Path) -> dict:
    review = approved_review(path)
    recommendations = review.get("filter_recommendations")
    if not isinstance(recommendations, dict):
        raise ValueError("filter review must record explicit filter recommendations")
    uses_filter = any(value is not None for criteria in recommendations.values() for value in criteria.values())
    if uses_filter:
        evidence = review.get("filter_evidence", [])
        if not evidence or any(item.get("decision") not in {"accepted", "approved"} for item in evidence):
            raise ValueError("approved filters require accepted exclusion evidence")
    return review


def copy_bound_json(source: Path, destination: Path, evidence_dir: Path) -> None:
    payload = json.loads(source.read_text(encoding="utf-8"))
    evidence_dir.mkdir(parents=True, exist_ok=True)
    counter = 0

    def bind(value, key: str | None = None):
        nonlocal counter
        if isinstance(value, dict):
            return {item_key: bind(item_value, item_key) for item_key, item_value in value.items()}
        if isinstance(value, list):
            return [bind(item) for item in value]
        if isinstance(value, str) and key in {"path", "before", "after", "audit", "overlay"}:
            artifact = Path(value)
            if artifact.is_file():
                counter += 1
                copy = evidence_dir / f"{counter:03d}-{artifact.name}"
                shutil.copy2(artifact, copy)
                return str(copy.relative_to(destination.parent))
        return value

    atomic_write_json(destination, bind(payload))


def verify_release(path: Path) -> dict:
    release = json.loads(path.read_text(encoding="utf-8"))
    if release.get("status") != "approved" or not release.get("approval", {}).get("reviewer"):
        raise ValueError("release is not approved by a named reviewer")
    for name in ("manifest", "config", "runtime_lock", "heldout"):
        record = release.get(name, {})
        artifact = Path(record.get("path", ""))
        if not artifact.is_absolute():
            artifact = path.parent / artifact
        if not artifact.is_file() or sha256(artifact) != record.get("sha256"):
            raise ValueError(f"release {name} artifact is missing or changed")
        record["path"] = str(artifact.resolve())
    for record in release.get("reviews", []):
        artifact = Path(record.get("path", ""))
        if not artifact.is_absolute():
            artifact = path.parent / artifact
        if not artifact.is_file() or sha256(artifact) != record.get("sha256"):
            raise ValueError(f"release review artifact is missing or changed: {record.get('stage')}")
        approved_filter_review(artifact) if record.get("stage") == "filter" else approved_review(artifact)
        record["path"] = str(artifact.resolve())
    stages = {record.get("stage") for record in release.get("reviews", [])}
    config = json.loads(Path(release["config"]["path"]).read_text(encoding="utf-8"))
    required = required_review_stages(config)
    if missing := sorted(required - stages):
        raise ValueError("release is missing approved review stages: " + ", ".join(missing))
    passed_heldout(Path(release["heldout"]["path"]), config)
    return release


def create_release(state: dict, operator: str, reviewer: str) -> tuple[Path, dict]:
    if state.get("phase") != "release_approval_required":
        raise ValueError(f"workflow phase is {state.get('phase')!r}, expected 'release_approval_required'")
    if not operator.strip() or not reviewer.strip():
        raise ValueError("a named operator and reviewer are required")
    config_source = Path(state["config"])
    runtime_source = Path(state["runtime_lock"])
    manifest_source = Path(state["manifest"])
    heldout_source = Path(state["heldout_validation"])
    config = json.loads(config_source.read_text(encoding="utf-8"))
    passed_heldout(heldout_source, config)
    reviews = []
    for record in state.get("review_history", []):
        review_path = Path(record["path"])
        if record.get("sha256") and record["sha256"] != sha256(review_path):
            raise ValueError(f"review changed after workflow acceptance: {record.get('stage')}")
        decision = approved_filter_review(review_path) if record["stage"] == "filter" else approved_review(review_path)
        reviews.append({"stage": record["stage"], "source": review_path, "reviewer": decision["reviewer"]})
    required = required_review_stages(config)
    if missing := sorted(required - {item["stage"] for item in reviews}):
        raise ValueError("cannot release without approved stages: " + ", ".join(missing))

    identity = {
        "config": sha256(config_source),
        "runtime_lock": sha256(runtime_source),
        "manifest": sha256(manifest_source),
        "heldout": sha256(heldout_source),
        "reviews": [{"stage": item["stage"], "sha256": sha256(item["source"])} for item in reviews],
        "operator": operator,
        "reviewer": reviewer,
    }
    digest = hashlib.sha256(json.dumps(identity, sort_keys=True).encode("utf-8")).hexdigest()
    release_id = f"release-{digest[:12]}"
    directory = Path(state["output"]) / "releases" / release_id
    release_path = directory / "release.json"
    if release_path.exists():
        return release_path, verify_release(release_path)
    directory.mkdir(parents=True, exist_ok=False)
    copies = {
        "config": (config_source, directory / "assay.json"),
        "runtime_lock": (runtime_source, directory / "runtime-lock.json"),
        "manifest": (manifest_source, directory / "manifest.jsonl"),
    }
    for source, destination in copies.values():
        shutil.copy2(source, destination)
    heldout_destination = directory / "heldout-validation.json"
    copy_bound_json(heldout_source, heldout_destination, directory / "heldout-evidence")
    review_records = []
    for number, item in enumerate(reviews, start=1):
        destination = directory / f"review-{number:02d}-{item['stage']}.json"
        copy_bound_json(item["source"], destination, directory / "review-evidence" / f"review-{number:02d}")
        review_records.append({"stage": item["stage"], "path": str(destination.relative_to(directory)),
                               "sha256": sha256(destination), "reviewer": item["reviewer"]})
    payload = {
        "schema_version": 1,
        "id": release_id,
        "status": "approved",
        "created_at": now(),
        "source_root": state["input"],
        "manifest": {"path": copies["manifest"][1].name, "sha256": sha256(copies["manifest"][1])},
        "config": {"path": copies["config"][1].name, "sha256": sha256(copies["config"][1])},
        "runtime_lock": {"path": copies["runtime_lock"][1].name, "sha256": sha256(copies["runtime_lock"][1])},
        "heldout": {"path": heldout_destination.name, "sha256": sha256(heldout_destination)},
        "reviews": review_records,
        "approval": {"operator": operator, "reviewer": reviewer, "approved_at": now()},
    }
    atomic_write_json(release_path, payload)
    return release_path, verify_release(release_path)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="action", required=True)
    create = commands.add_parser("create")
    create.add_argument("--workflow-state", required=True, type=Path)
    create.add_argument("--operator", required=True)
    create.add_argument("--reviewer", required=True)
    verify = commands.add_parser("verify")
    verify.add_argument("--release", required=True, type=Path)
    args = parser.parse_args()
    try:
        if args.action == "create":
            state_path = args.workflow_state.expanduser().resolve()
            state = json.loads(state_path.read_text(encoding="utf-8"))
            path, payload = create_release(state, args.operator, args.reviewer)
            state.update({"phase": "production_canary_required", "release": str(path),
                          "next_action": "Run one untouched production canary well using this immutable release."})
            atomic_write_json(state_path, state)
        else:
            path, payload = args.release.expanduser().resolve(), verify_release(args.release.expanduser().resolve())
        print(json.dumps({"status": payload["status"], "release": str(path), "id": payload["id"]}, indent=2))
        return 0
    except (KeyError, OSError, ValueError, json.JSONDecodeError) as error:
        print(json.dumps({"error": str(error)}), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
