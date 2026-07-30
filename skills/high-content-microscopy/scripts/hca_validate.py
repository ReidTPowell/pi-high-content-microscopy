#!/usr/bin/env python3
"""Validate an image manifest against a JSON assay configuration."""
from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path
from typing import Any

from hca_contract import validate_config, validate_record


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def validate(records: list[dict[str, Any]], config: dict[str, Any]) -> dict[str, Any]:
    contract_errors = validate_config(config)
    valid_records = []
    for index, record in enumerate(records):
        record_errors = validate_record(record)
        contract_errors.extend(f"record {index}: {error}" for error in record_errors)
        if not record_errors:
            valid_records.append(record)
    expected = config.get("input", {}).get("expected_channels")
    expected_z = config.get("input", {}).get("expected_z_planes")
    groups: dict[tuple[Any, ...], set[int]] = defaultdict(set)
    z_groups: dict[tuple[Any, ...], set[int]] = defaultdict(set)
    generic = 0
    for record in valid_records:
        if record["adapter"] in {"generic-tiff", "bioio-required"}:
            generic += 1
        key = (record["plate"], record["well"], record["site"], record["timepoint"], record["z"])
        if record["channel"] is not None:
            groups[key].add(record["channel"])
        z_key = key[:-1]
        if record["z"] is not None:
            z_groups[z_key].add(record["z"])
    missing_channels = []
    if expected is not None:
        expected_set = set(expected)
        for key, observed in groups.items():
            missing = sorted(expected_set - observed)
            if missing:
                missing_channels.append({"field": key, "missing_channels": missing})
    missing_z = []
    if expected_z is not None:
        expected_set = set(expected_z)
        for key, observed in z_groups.items():
            missing = sorted(expected_set - observed)
            if missing:
                missing_z.append({"field": key, "missing_z_planes": missing})
    return {
        "images": len(records), "generic_coordinate_records": generic,
        "incomplete_channel_fields": missing_channels, "incomplete_z_fields": missing_z,
        "contract_errors": contract_errors,
        "ok": not missing_channels and not missing_z and not contract_errors,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    report = validate(load_jsonl(args.manifest), json.loads(args.config.read_text(encoding="utf-8")))
    payload = json.dumps(report, indent=2) + "\n"
    if args.output:
        args.output.write_text(payload, encoding="utf-8")
    print(payload, end="")
    return 0 if report["ok"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
