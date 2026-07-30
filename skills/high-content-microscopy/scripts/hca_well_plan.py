#!/usr/bin/env python3
"""Create deterministic, per-well work manifests for one microscopy plate."""
from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path
from typing import Any


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--workers", type=int, default=1,
                        help="Maximum independent well jobs the caller should run concurrently")
    args = parser.parse_args()
    if args.workers < 1:
        parser.error("--workers must be at least 1")
    wells: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for record in load_jsonl(args.manifest):
        if record.get("well") is None:
            continue
        wells[record["well"]].append(record)
    if not wells:
        parser.error("manifest has no coordinate-bearing well records")
    output = args.output_dir
    output.mkdir(parents=True, exist_ok=True)
    jobs = []
    for well, records in sorted(wells.items()):
        path = output / f"{well}.jsonl"
        with path.open("w", encoding="utf-8") as handle:
            for record in records:
                handle.write(json.dumps(record, sort_keys=True) + "\n")
        jobs.append({"well": well, "manifest": str(path), "images": len(records)})
    plan = {"source_manifest": str(args.manifest), "max_workers": args.workers, "jobs": jobs}
    (output / "plan.json").write_text(json.dumps(plan, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"wells": len(jobs), "max_workers": args.workers, "plan": str(output / "plan.json")}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
