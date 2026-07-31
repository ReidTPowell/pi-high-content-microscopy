#!/usr/bin/env python3
"""Create and validate human review decisions before scaling an analysis."""
from __future__ import annotations

import argparse
import json
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--qc", required=True, type=Path, help="Output from hca_qc.py")
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--approve", action="store_true", help="Mark review approved after a human has completed it")
    parser.add_argument("--reviewer", default=None)
    parser.add_argument("--notes", default=None)
    parser.add_argument("--decisions", type=Path,
                        help="JSON mapping each sampled image path to accepted or rejected")
    args = parser.parse_args()
    qc = json.loads(args.qc.read_text())
    decisions = json.loads(args.decisions.read_text(encoding="utf-8")) if args.decisions else {}
    review_images = [{"path": record["path"], "decision": decisions.get(record["path"], "pending"),
                      "notes": None} for record in qc["sample"]]
    if args.approve:
        if not args.reviewer:
            parser.error("--approve requires --reviewer")
        if not review_images or any(item["decision"] != "accepted" for item in review_images):
            parser.error("--approve requires an accepted decision for every sampled image")
    decision = {
        "review_status": "approved" if args.approve else "pending",
        "reviewer": args.reviewer,
        "notes": args.notes,
        "sample_seed": qc["seed"],
        "review_images": review_images,
        "required_checks": qc["checks"],
        "qc_flag_count": len(qc["flags"]),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(decision, indent=2) + "\n")
    print(json.dumps({"status": decision["review_status"], "images": len(decision["review_images"]), "output": str(args.output)}))
    return 0 if args.approve else 3


if __name__ == "__main__":
    raise SystemExit(main())
