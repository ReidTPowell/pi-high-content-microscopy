#!/usr/bin/env python3
"""Derive relational per-cell metrics from reviewed nucleus and cytoplasm measurements."""
from __future__ import annotations

import argparse
import json
from pathlib import Path


def indexed(path: Path) -> dict[int, dict]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    return {int(item["object_id"]): item for item in payload.get("objects", [])}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--nucleus-measurements", required=True, type=Path)
    parser.add_argument("--cytoplasm-measurements", required=True, type=Path)
    parser.add_argument("--relationships", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    nuclei, cytoplasm = indexed(args.nucleus_measurements), indexed(args.cytoplasm_measurements)
    relationships = json.loads(args.relationships.read_text(encoding="utf-8"))["relationships"]
    by_cell: dict[int, list[int]] = {}
    for item in relationships:
        if item.get("status") == "assigned":
            by_cell.setdefault(int(item["cell_id"]), []).append(int(item["nucleus_id"]))
    rows = []
    for cell_id, nucleus_ids in sorted(by_cell.items()):
        cyto = cytoplasm.get(cell_id)
        available = [nuclei[nucleus_id] for nucleus_id in nucleus_ids if nucleus_id in nuclei]
        if not cyto or not available:
            continue
        channels = {}
        for role, cyto_values in cyto.get("channels", {}).items():
            nucleus_values = [item.get("channels", {}).get(role) for item in available]
            nucleus_values = [item for item in nucleus_values if item]
            if not nucleus_values:
                continue
            nuclear_sum = sum(item["sum"] for item in nucleus_values)
            nuclear_area = sum(nuclei[nucleus_id].get("area_px", 0) for nucleus_id in nucleus_ids if nucleus_id in nuclei)
            nuclear_mean = nuclear_sum / max(nuclear_area, 1)
            channels[role] = {"nuclear_mean": nuclear_mean, "cytoplasm_mean": cyto_values["mean"],
                              "nucleus_to_cytoplasm_ratio": nuclear_mean / max(abs(cyto_values["mean"]), 1e-12)}
        rows.append({"cell_id": cell_id, "nucleus_ids": nucleus_ids, "channels": channels})
    payload = {"schema_version": 1, "metric": "nucleus_to_cytoplasm_ratio", "cell_count": len(rows), "cells": rows}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"cell_count": len(rows), "output": str(args.output)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
