#!/usr/bin/env python3
"""Create a portable JSONL image manifest from HCSai-like TIFF exports."""
from __future__ import annotations

import argparse
import csv
import json
import re
from collections import Counter
from pathlib import Path
from typing import Any

HCSAI_PATTERN = re.compile(
    r"^(?P<prefix>.+)_t(?P<timepoint>\d+)_(?P<row>[A-Za-z]+)(?P<column>\d+)_"
    r"(?P<site>s\d+)_w(?P<channel>\d+)_z(?P<z>\d+)\.(?:tif|tiff)$",
    re.IGNORECASE,
)
IMAGE_EXTENSIONS = {".tif", ".tiff", ".ome.tif", ".ome.tiff", ".czi", ".nd2", ".lif"}


def hcsai_record(path: Path, root: Path) -> dict[str, Any] | None:
    match = HCSAI_PATTERN.match(path.name)
    if not match:
        return None
    fields = match.groupdict()
    return {
        "path": str(path.relative_to(root)), "format": "tiff", "adapter": "hcsai",
        "plate": None, "well": f"{fields['row'].upper()}{int(fields['column']):02d}",
        "row": fields["row"].upper(), "column": int(fields["column"]),
        "site": fields["site"], "timepoint": int(fields["timepoint"]),
        "channel": int(fields["channel"]), "z": int(fields["z"]), "prefix": fields["prefix"],
    }


def generic_record(path: Path, root: Path) -> dict[str, Any]:
    suffix = path.suffix.lower()
    format_name = {".czi": "czi", ".nd2": "nd2", ".lif": "lif"}.get(
        suffix, "ome-tiff" if ".ome." in path.name.lower() else "tiff"
    )
    return {
        "path": str(path.relative_to(root)), "format": format_name,
        "adapter": "bioio-required" if format_name in {"czi", "nd2", "lif", "ome-tiff"} else "generic-tiff",
        "plate": None, "well": None, "row": None, "column": None,
        "site": None, "timepoint": None, "channel": None, "z": None, "prefix": path.stem,
    }


def discover_plates(root: Path) -> list[Path]:
    """Return acquisition roots containing HCSai image metadata."""
    return sorted({path.parent.parent for path in root.rglob("image_metadata_*.csv")})


def metadata_index(root: Path) -> dict[str, dict[str, str]]:
    """Index metadata by path relative to this acquisition, not basename."""
    index: dict[str, dict[str, str]] = {}
    for path in root.rglob("image_metadata_*.csv"):
        with path.open(newline="", encoding="utf-8-sig") as handle:
            for row in csv.DictReader(handle):
                filename = row.get("ImageFileName")
                if filename:
                    subfolder = row.get("ImageSubFolderPath", "")
                    if subfolder:
                        image_path = path.parent / subfolder / filename
                        try:
                            key = str(image_path.relative_to(root))
                        except ValueError:
                            continue
                    else:
                        # The fallback is scoped to a single acquisition root.
                        key = filename
                    index[key] = {key: value for key, value in row.items() if value not in (None, "")}
    return index


def build_manifest(root: Path) -> list[dict[str, Any]]:
    metadata = metadata_index(root)
    records = []
    for path in sorted(root.rglob("*")):
        if not path.is_file() or not any(path.name.lower().endswith(ext) for ext in IMAGE_EXTENSIONS):
            continue
        record = hcsai_record(path, root) or generic_record(path, root)
        acquisition = metadata.get(record["path"]) or metadata.get(path.name)
        if acquisition:
            record["acquisition"] = acquisition
        records.append(record)
    return records


def manifest_summary(records: list[dict[str, Any]]) -> dict[str, Any]:
    values = lambda key: sorted({record[key] for record in records if record[key] is not None})
    return {
        "images": len(records), "adapters": dict(Counter(record["adapter"] for record in records)),
        "wells": values("well"), "channels": values("channel"), "sites": values("site"),
        "timepoints": values("timepoint"), "z_planes": values("z"),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--summary", type=Path)
    parser.add_argument("--discover-plates", action="store_true",
                        help="Write HCSai acquisition roots below --input, without creating a manifest")
    args = parser.parse_args()
    root = args.input.expanduser().resolve()
    if not root.is_dir():
        parser.error(f"input is not a directory: {root}")
    plates = discover_plates(root)
    if args.discover_plates:
        payload = {"batch_root": str(root), "plates": [str(path) for path in plates]}
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        print(json.dumps(payload, indent=2))
        return 0
    if len(plates) > 1:
        parser.error(
            f"input contains {len(plates)} HCSai acquisitions; run with --discover-plates, "
            "then create and analyze one manifest per acquisition"
        )
    records = build_manifest(root)
    if not records:
        parser.error("no TIFF images found")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record, sort_keys=True) + "\n")
    summary = manifest_summary(records)
    if args.summary:
        args.summary.parent.mkdir(parents=True, exist_ok=True)
        args.summary.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
