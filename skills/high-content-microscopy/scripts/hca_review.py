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
    args = parser.parse_args()
    qc = json.loads(args.qc.read_text())
    decision = {
        "review_status": "approved" if args.approve else "pending",
        "reviewer": args.reviewer,
        "notes": args.notes,
        "sample_seed": qc["seed"],
        "review_images": [{"path": record["path"], "decision": "pending", "notes": None} for record in qc["sample"]],
        "required_checks": qc["checks"],
        "qc_flag_count": len(qc["flags"]),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(decision, indent=2) + "\n")
    print(json.dumps({"status": decision["review_status"], "images": len(decision["review_images"]), "output": str(args.output)}))
    return 0 if args.approve else 3


if __name__ == "__main__":
    raise SystemExit(main())
