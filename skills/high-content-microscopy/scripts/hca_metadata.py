#!/usr/bin/env python3
"""Curate manifest metadata and join an optional well-level plate map."""
from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path

from hca_contract import load_jsonl


def load_plate_map(path: Path) -> tuple[dict[str, dict], list[str]]:
    with path.open(newline="", encoding="utf-8-sig") as handle:
        rows = list(csv.DictReader(handle))
    if not rows or "well" not in rows[0]:
        raise ValueError("plate map must contain a 'well' column")
    index = {}
    for row in rows:
        well = row["well"].strip().upper()
        if well in index:
            raise ValueError(f"duplicate plate-map well: {well}")
        index[well] = {key: value for key, value in row.items() if key != "well" and value not in (None, "")}
    return index, list(rows[0].keys())


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--plate-map", type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--report", required=True, type=Path)
    args = parser.parse_args()
    records = load_jsonl(args.manifest)
    plate_map, columns = ({}, []) if args.plate_map is None else load_plate_map(args.plate_map)
    observed_wells = {record.get("well") for record in records if record.get("well")}
    unmatched_manifest = sorted(well for well in observed_wells if plate_map and well not in plate_map)
    unmatched_plate_map = sorted(well for well in plate_map if well not in observed_wells)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", encoding="utf-8") as handle:
        for record in records:
            curated = dict(record)
            curated["plate_map"] = plate_map.get(record.get("well"), {})
            handle.write(json.dumps(curated, sort_keys=True) + "\n")
    report = {"records": len(records), "wells": len(observed_wells), "plate_map": str(args.plate_map) if args.plate_map else None,
              "plate_map_columns": columns, "unmatched_manifest_wells": unmatched_manifest,
              "unmatched_plate_map_wells": unmatched_plate_map, "ok": not unmatched_manifest and not unmatched_plate_map}
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2))
    return 0 if report["ok"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
