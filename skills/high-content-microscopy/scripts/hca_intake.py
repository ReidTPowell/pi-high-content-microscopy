#!/usr/bin/env python3
"""Perform the fast, bounded first step of a Pi HCA conversation."""
from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path

from hca_contract import default_output_dir
from hca_manifest import build_manifest, discover_plates, manifest_summary


def describe(plate: Path) -> dict:
    metadata_files = sorted(plate.rglob("image_metadata_*.csv"))
    if not metadata_files:
        summary = manifest_summary(build_manifest(plate))
        return {"acquisition": str(plate), "images": summary["images"], "wells": len(summary["wells"]),
                "sites": summary["sites"], "channels": summary["channels"], "timepoints": summary["timepoints"],
                "z_planes": summary["z_planes"], "adapters": summary["adapters"], "inventory_source": "full_manifest"}
    images, wells, sites, channels, timepoints, z_planes = 0, set(), set(), set(), set(), set()
    for metadata in metadata_files:
        with metadata.open(newline="", encoding="utf-8-sig") as handle:
            for row in csv.DictReader(handle):
                images += 1
                row_name, column = row.get("Row"), row.get("Column")
                wells.add(f"{row_name}{int(column):02d}" if row_name and column and column.isdigit() else row.get("Well"))
                sites.add(row.get("Field"))
                channels.add(int(row["Wavelength"]) if row.get("Wavelength", "").isdigit() else row.get("Wavelength"))
                timepoints.add(int(row["Timepoint"]) if row.get("Timepoint", "").isdigit() else row.get("Timepoint"))
                z_planes.add(int(row["ZIndex"]) if row.get("ZIndex", "").isdigit() else row.get("ZIndex"))
    clean = lambda values: sorted(value for value in values if value not in (None, ""))
    return {"acquisition": str(plate), "images": images, "wells": len(clean(wells)), "sites": clean(sites),
            "channels": clean(channels), "timepoints": clean(timepoints), "z_planes": clean(z_planes),
            "adapters": {"hcsai": images}, "inventory_source": "image_metadata_csv"}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--output-dir", type=Path, help="Defaults to <Barcode>_piHCA/intake")
    args = parser.parse_args()
    root = args.input.expanduser().resolve()
    if not root.is_dir():
        parser.error(f"input is not a directory: {root}")
    plates = discover_plates(root)
    if not plates:
        plates = [root]
    output = args.output_dir or default_output_dir(root) / "intake"
    output.mkdir(parents=True, exist_ok=True)
    acquisitions = [describe(plate) for plate in plates]
    payload = {
        "schema_version": 1, "input": str(root), "status": "plate_selection_required" if len(acquisitions) > 1 else "assay_questions_required",
        "acquisitions": acquisitions,
        "questions": [
            "Which acquisition should be piloted, if more than one is listed?",
            "What is the biological endpoint and which wells are controls?",
            "Confirm the channel roles and the primary and secondary objects to segment.",
            "Should the secondary cell-boundary model use the primary nuclear raw image as guidance?",
        ],
        "next_action": "After answers, run hca_preconfigure.py for exactly one acquisition. Do not run segmentation or a batch from intake.",
    }
    destination = output / "intake.json"
    destination.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": payload["status"], "acquisitions": acquisitions, "questions": payload["questions"], "output": str(destination)}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
