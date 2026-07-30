#!/usr/bin/env python3
"""Execute the configured segmentation, relational segmentation, overlay, and measurement stages for one well."""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from collections import defaultdict
from pathlib import Path

from hca_contract import load_jsonl


def channel_for_role(config: dict, role: str) -> int:
    matches = [int(channel) for channel, details in config["channels"].items() if details.get("role") == role]
    if len(matches) != 1:
        raise ValueError(f"expected exactly one channel with role {role!r}, found {matches}")
    return matches[0]


def run(command: list[str]) -> None:
    subprocess.run(command, check=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--well-manifest", required=True, type=Path)
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--source-root", type=Path, help="Root used to resolve manifest-relative image paths")
    args = parser.parse_args()
    config = json.loads(args.config.read_text())
    source_value = args.source_root or config.get("input", {}).get("source_root")
    if not source_value:
        parser.error("--source-root or input.source_root is required")
    source_root = Path(source_value)
    records = load_jsonl(args.well_manifest)
    segmentation = config["analysis"]["segmentation"]
    nucleus, cell, relationship = segmentation.get("nucleus", {}), segmentation.get("cell", {}), segmentation.get("relationship", {})
    if not nucleus.get("enabled"):
        parser.error("configured pipeline requires segmentation.nucleus.enabled")
    nucleus_channel = channel_for_role(config, nucleus["channel_role"])
    cell_channel = channel_for_role(config, cell["channel_role"]) if cell.get("enabled") else None
    fields: dict[tuple, dict[int, dict]] = defaultdict(dict)
    for record in records:
        fields[(record["site"], record["timepoint"], record["z"])][record["channel"]] = record
    script_dir, results = Path(__file__).parent, []
    for (site, timepoint, z), by_channel in sorted(fields.items()):
        if nucleus_channel not in by_channel:
            raise RuntimeError(f"missing nuclear channel in {site}, t{timepoint}, z{z}")
        field_dir = args.output_dir / f"{site}-t{timepoint}-z{z}"
        field_dir.mkdir(parents=True, exist_ok=True)
        nuclear_image = source_root / by_channel[nucleus_channel]["path"]
        nuclei_labels = field_dir / "nuclei-labels.tif"
        run([sys.executable, str(script_dir / "hca_segment.py"), "--image", str(nuclear_image), "--engine", nucleus["engine"], "--model", nucleus.get("model", "nuclei"), "--output", str(nuclei_labels)])
        field_result = {"site": site, "timepoint": timepoint, "z": z, "nuclei_labels": str(nuclei_labels)}
        if cell.get("enabled"):
            if cell_channel not in by_channel:
                raise RuntimeError(f"missing cell channel in {site}, t{timepoint}, z{z}")
            cell_image, cell_labels = source_root / by_channel[cell_channel]["path"], field_dir / "cell-labels.tif"
            command = [sys.executable, str(script_dir / "hca_segment.py"), "--image", str(cell_image), "--engine", cell["engine"], "--model", cell.get("model", "cyto3"), "--output", str(cell_labels)]
            if cell.get("use_nuclear_image"):
                command.extend(["--nuclear-image", str(nuclear_image)])
            run(command)
            field_result["cell_labels"] = str(cell_labels)
            if relationship.get("enabled"):
                relation_dir = field_dir / "relationship"
                run([sys.executable, str(script_dir / "hca_relate.py"), "--nuclei", str(nuclei_labels), "--cells", str(cell_labels), "--output-dir", str(relation_dir), "--min-overlap", str(relationship.get("min_overlap", 0.5))])
                relation = json.loads((relation_dir / "relationships.json").read_text())
                denominator = max(relation["nuclei"], 1)
                field_result["relationship"] = relation
                if relation["orphan"] / denominator > relationship.get("max_orphan_fraction", 1.0) or relation["ambiguous"] / denominator > relationship.get("max_ambiguous_fraction", 1.0):
                    field_result["relationship_qc"] = "failed"
                else:
                    field_result["relationship_qc"] = "passed"
                for label, image, output in ((nuclei_labels, nuclear_image, field_dir / "nuclei-measurements.json"), (cell_labels, cell_image, field_dir / "cell-measurements.json"), (relation_dir / "cytoplasm-by-cell.tif", cell_image, field_dir / "cytoplasm-measurements.json")):
                    run([sys.executable, str(script_dir / "hca_measure.py"), "--labels", str(label), "--image", str(image), "--output", str(output)])
                if segmentation.get("overlays"):
                    run([sys.executable, str(script_dir / "hca_overlay.py"), "--image", str(nuclear_image), "--labels", str(nuclei_labels), "--output", str(field_dir / "nuclei-overlay.tif")])
        results.append(field_result)
    failed = [entry for entry in results if entry.get("relationship_qc") == "failed"]
    args.output_dir.mkdir(parents=True, exist_ok=True)
    (args.output_dir / "pipeline-summary.json").write_text(json.dumps({"fields": results, "relationship_qc_failed": len(failed)}, indent=2) + "\n")
    return 2 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
