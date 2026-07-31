#!/usr/bin/env python3
"""Select deterministic paired pilot fields spanning acquisition intensity diversity."""
from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path

from hca_contract import atomic_write_json, load_jsonl


def role_channel(config: dict, role: str) -> int:
    matches = [int(channel) for channel, value in config["channels"].items() if value.get("role") == role]
    if len(matches) != 1:
        raise ValueError(f"expected exactly one channel with role {role!r}, found {matches}")
    return matches[0]


def mean_intensity(record: dict) -> float:
    try:
        return float(record.get("acquisition", {}).get("MeanIntensity"))
    except (TypeError, ValueError):
        return 0.0


def select_fields(records: list[dict], config: dict, count: int) -> list[dict]:
    segmentation = config["analysis"]["segmentation"]
    nucleus_channel = role_channel(config, segmentation["nucleus"]["channel_role"])
    cell_stage = segmentation.get("cell", {})
    cell_channel = role_channel(config, cell_stage["channel_role"]) if cell_stage.get("enabled") else None
    required = {nucleus_channel} | ({cell_channel} if cell_channel is not None else set())
    grouped: dict[tuple, dict[int, dict]] = defaultdict(dict)
    for record in records:
        grouped[(record.get("well"), record.get("site"), record.get("timepoint"), record.get("z"))][record.get("channel")] = record
    paired = [(key, channels) for key, channels in grouped.items() if required.issubset(channels)]
    if not paired:
        raise ValueError("manifest has no fields containing all configured segmentation channels")
    if count > len(paired):
        raise ValueError(f"requested {count} fields but only {len(paired)} paired fields are available")

    channels_to_rank = sorted(required)
    ranks: dict[int, dict[tuple, float]] = {}
    for channel in channels_to_rank:
        ordered = sorted(paired, key=lambda item: (mean_intensity(item[1][channel]), item[0]))
        denominator = max(len(ordered) - 1, 1)
        ranks[channel] = {key: index / denominator for index, (key, _) in enumerate(ordered)}
    scored = [(sum(ranks[channel][key] for channel in channels_to_rank) / len(channels_to_rank), key, values)
              for key, values in paired]

    targets = [0.5] if count == 1 else [index / (count - 1) for index in range(count)]
    selected, used_keys, used_wells = [], set(), set()
    for target in targets:
        options = sorted(scored, key=lambda item: (item[1][0] in used_wells, abs(item[0] - target), item[1]))
        score, key, values = next(item for item in options if item[1] not in used_keys)
        used_keys.add(key)
        used_wells.add(key[0])
        selected.append({
            "well": key[0], "site": key[1], "timepoint": key[2], "z": key[3],
            "combined_intensity_rank": round(score, 6),
            "channels": {str(channel): {"path": values[channel]["path"], "mean_intensity": mean_intensity(values[channel])}
                         for channel in channels_to_rank},
        })
    return selected


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--count", type=int, default=3)
    args = parser.parse_args()
    if args.count < 1:
        parser.error("--count must be at least 1")
    try:
        fields = select_fields(load_jsonl(args.manifest), json.loads(args.config.read_text()), args.count)
    except ValueError as error:
        parser.error(str(error))
    payload = {
        "schema_version": 1,
        "selection_basis": "paired fields spanning combined per-channel acquisition metadata mean-intensity rank",
        "treatment_blinded": True,
        "fields": fields,
        "next_action": "Run the bounded nuclei Cellpose grid on these fields and compare overlays before secondary cell tuning.",
    }
    atomic_write_json(args.output, payload)
    print(json.dumps({"fields": len(fields), "output": str(args.output), "next_action": payload["next_action"]}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
