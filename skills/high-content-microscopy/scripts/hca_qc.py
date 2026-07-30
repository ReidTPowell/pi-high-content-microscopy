#!/usr/bin/env python3
"""Create seeded, stratified QC samples and metadata saturation flags for one plate."""
from __future__ import annotations

import argparse
import json
import random
from collections import defaultdict
from pathlib import Path

from hca_contract import load_jsonl


def stratified_sample(records: list[dict], size: int, seed: int) -> list[dict]:
    groups: dict[tuple, list[dict]] = defaultdict(list)
    for record in records:
        groups[(record.get("row"), record.get("column"), record.get("channel"), record.get("timepoint"))].append(record)
    randomizer = random.Random(seed)
    selected = [randomizer.choice(values) for _, values in sorted(groups.items())]
    if len(selected) > size:
        return randomizer.sample(selected, size)
    remaining = [record for record in records if record not in selected]
    return selected + randomizer.sample(remaining, min(size - len(selected), len(remaining)))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--sample-size", type=int, default=48)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--saturation-fraction", type=float, default=0.005)
    args = parser.parse_args()
    records = load_jsonl(args.manifest)
    flagged = []
    for record in records:
        acquisition = record.get("acquisition", {})
        try:
            if float(acquisition["MaxIntensity"]) >= 65535:
                flagged.append({"path": record["path"], "reason": "metadata_max_at_16bit_limit"})
        except (KeyError, TypeError, ValueError):
            continue
    payload = {"seed": args.seed, "sample": stratified_sample(records, args.sample_size, args.seed),
               "flags": flagged, "checks": ["metadata saturation", "manual focus review", "manual segmentation overlay review"]}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, indent=2) + "\n")
    print(json.dumps({"sampled": len(payload["sample"]), "flags": len(flagged), "seed": args.seed}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
