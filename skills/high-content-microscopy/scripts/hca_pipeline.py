#!/usr/bin/env python3
"""Execute the configured segmentation, relational segmentation, overlay, and measurement stages for one well."""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from collections import defaultdict
from pathlib import Path

from hca_contract import default_output_dir, load_jsonl


def channel_for_role(config: dict, role: str) -> int:
    matches = [int(channel) for channel, details in config["channels"].items() if details.get("role") == role]
    if len(matches) != 1:
        raise ValueError(f"expected exactly one channel with role {role!r}, found {matches}")
    return matches[0]


def run(command: list[str], log_path: Path) -> None:
    """Run a child stage without flooding the agent transcript; retain its logs per field."""
    result = subprocess.run(command, check=False, capture_output=True, text=True)
    with log_path.open("a", encoding="utf-8") as handle:
        handle.write("$ " + " ".join(command) + "\n")
        handle.write(result.stdout)
        handle.write(result.stderr)
        handle.write("\n")
    if result.returncode:
        raise RuntimeError(f"stage failed ({result.returncode}); inspect {log_path}")


def append_segmentation_options(command: list[str], stage: dict) -> list[str]:
    if stage.get("diameter") is not None:
        command.extend(["--diameter", str(stage["diameter"])])
    if stage.get("gpu"):
        command.append("--gpu")
    if stage.get("engine") == "cellpose":
        options = stage.get("cellpose", {})
        flags = {"flow_threshold": "--flow-threshold", "cellprob_threshold": "--cellprob-threshold",
                 "min_size": "--min-size", "niter": "--niter", "tile_overlap": "--tile-overlap"}
        for key, flag in flags.items():
            if options.get(key) is not None:
                command.extend([flag, str(options[key])])
        if options.get("augment"):
            command.append("--augment")
        if options.get("normalize") is False:
            command.append("--no-normalize")
    return command


def filter_command(script_dir: Path, raw_labels: Path, output_labels: Path, audit: Path, image: Path | None, criteria: dict) -> list[str]:
    command = [sys.executable, str(script_dir / "hca_filter.py"), "--labels", str(raw_labels), "--output", str(output_labels), "--audit", str(audit)]
    if image is not None:
        command.extend(["--image", str(image)])
    flags = {"min_area_px": "--min-area-px", "max_area_px": "--max-area-px", "min_intensity_mean": "--min-intensity-mean", "max_intensity_mean": "--max-intensity-mean"}
    for key, flag in flags.items():
        if criteria.get(key) is not None:
            command.extend([flag, str(criteria[key])])
    return command


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--well-manifest", required=True, type=Path)
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--output-dir", type=Path, help="Defaults to <Barcode>_piHCA/wells/<well>")
    parser.add_argument("--source-root", type=Path, help="Root used to resolve manifest-relative image paths")
    parser.add_argument("--allow-overwrite", action="store_true", help="Explicitly permit replacement of an existing direct pipeline output")
    parser.add_argument("--fail-on-qc", action="store_true", help="Return nonzero when relationship QC flags are present")
    args = parser.parse_args()
    config = json.loads(args.config.read_text())
    source_value = args.source_root or config.get("input", {}).get("source_root")
    if not source_value:
        parser.error("--source-root or input.source_root is required")
    source_root = Path(source_value)
    records = load_jsonl(args.well_manifest)
    well = records[0].get("well") if records else None
    if not well:
        parser.error("well manifest is empty or has no well identifier")
    if args.output_dir is None:
        args.output_dir = default_output_dir(source_root) / "wells" / well
    if args.output_dir.exists() and any(args.output_dir.iterdir()) and not args.allow_overwrite:
        parser.error(f"output directory is not empty: {args.output_dir}; select a new analysis root or pass --allow-overwrite deliberately")
    analysis = config["analysis"]
    segmentation = analysis["segmentation"]
    background = analysis.get("preprocessing", {}).get("background_subtraction", {})
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
        log_path = field_dir / "pipeline.log"
        processed_images: dict[int, Path] = {}

        def image_for(channel: int) -> Path:
            source = source_root / by_channel[channel]["path"]
            if not background.get("enabled"):
                return source
            if channel not in processed_images:
                corrected = field_dir / f"channel-{channel}-background-corrected.tif"
                report = field_dir / f"channel-{channel}-background.json"
                command = [sys.executable, str(script_dir / "hca_preprocess.py"), "--image", str(source),
                           "--output", str(corrected), "--report", str(report),
                           "--method", background.get("method", "percentile"),
                           "--percentile", str(background.get("percentile", 5.0)),
                           "--radius", str(background.get("radius", 25))]
                run(command, log_path)
                processed_images[channel] = corrected
            return processed_images[channel]

        nuclear_source_image = source_root / by_channel[nucleus_channel]["path"]
        nuclear_image = image_for(nucleus_channel)
        raw_nuclei_labels, nuclei_labels = field_dir / "nuclei-raw-labels.tif", field_dir / "nuclei-labels.tif"
        nucleus_command = [sys.executable, str(script_dir / "hca_segment.py"), "--image", str(nuclear_image), "--engine", nucleus["engine"], "--model", nucleus.get("model", "nuclei"), "--output", str(raw_nuclei_labels)]
        run(append_segmentation_options(nucleus_command, nucleus), log_path)
        nucleus_filter = nucleus.get("filter", {})
        nucleus_filter_image = image_for(channel_for_role(config, nucleus_filter.get("intensity_channel_role", nucleus["channel_role"])))
        nucleus_audit = field_dir / "nuclei-filter.json"
        run(filter_command(script_dir, raw_nuclei_labels, nuclei_labels, nucleus_audit, nucleus_filter_image, nucleus_filter), log_path)
        field_result = {"site": site, "timepoint": timepoint, "z": z, "nuclear_source_image": str(nuclear_source_image),
                        "nuclear_analysis_image": str(nuclear_image), "nuclei_raw_labels": str(raw_nuclei_labels),
                        "nuclei_labels": str(nuclei_labels), "nuclei_filter": str(nucleus_audit)}
        if cell.get("enabled"):
            if cell_channel not in by_channel:
                raise RuntimeError(f"missing cell channel in {site}, t{timepoint}, z{z}")
            cell_source_image = source_root / by_channel[cell_channel]["path"]
            cell_image = image_for(cell_channel)
            raw_cell_labels, cell_labels = field_dir / "cell-raw-labels.tif", field_dir / "cell-labels.tif"
            command = [sys.executable, str(script_dir / "hca_segment.py"), "--image", str(cell_image), "--engine", cell["engine"], "--model", cell.get("model", "cyto3"), "--output", str(raw_cell_labels)]
            if cell.get("use_nuclear_image"):
                command.extend(["--nuclear-image", str(nuclear_image)])
            run(append_segmentation_options(command, cell), log_path)
            cell_filter = cell.get("filter", {})
            cell_filter_image = image_for(channel_for_role(config, cell_filter.get("intensity_channel_role", cell["channel_role"])))
            cell_audit = field_dir / "cell-filter.json"
            run(filter_command(script_dir, raw_cell_labels, cell_labels, cell_audit, cell_filter_image, cell_filter), log_path)
            field_result.update({"cell_source_image": str(cell_source_image), "cell_analysis_image": str(cell_image),
                                 "cell_labels": str(cell_labels), "cell_raw_labels": str(raw_cell_labels),
                                 "cell_filter": str(cell_audit)})
            if relationship.get("enabled"):
                relation_dir = field_dir / "relationship"
                run([sys.executable, str(script_dir / "hca_relate.py"), "--nuclei", str(nuclei_labels), "--cells", str(cell_labels), "--output-dir", str(relation_dir), "--min-overlap", str(relationship.get("min_overlap", 0.5))], log_path)
                relation = json.loads((relation_dir / "relationships.json").read_text())
                denominator = max(relation["nuclei"], 1)
                field_result["relationship"] = {key: relation[key] for key in ("nuclei", "cells", "assigned", "orphan", "ambiguous")}
                field_result["relationship_artifact"] = str(relation_dir / "relationships.json")
                if relation["orphan"] / denominator > relationship.get("max_orphan_fraction", 1.0) or relation["ambiguous"] / denominator > relationship.get("max_ambiguous_fraction", 1.0):
                    field_result["relationship_qc"] = "failed"
                else:
                    field_result["relationship_qc"] = "passed"
                cytoplasm_image = cell_source_image if analysis.get("measurement_image") == "raw" else cell_image
                run([sys.executable, str(script_dir / "hca_measure.py"), "--labels", str(relation_dir / "cytoplasm-by-cell.tif"),
                     "--image", str(cytoplasm_image), "--output", str(field_dir / "cytoplasm-measurements.json")], log_path)
            cell_measurement_image = cell_source_image if analysis.get("measurement_image") == "raw" else cell_image
            run([sys.executable, str(script_dir / "hca_measure.py"), "--labels", str(cell_labels), "--image", str(cell_measurement_image),
                 "--output", str(field_dir / "cell-measurements.json")], log_path)
            if segmentation.get("overlays"):
                run([sys.executable, str(script_dir / "hca_overlay.py"), "--image", str(cell_image), "--labels", str(cell_labels),
                     "--output", str(field_dir / "cell-overlay.tif")], log_path)
        nuclear_measurement_image = nuclear_source_image if analysis.get("measurement_image") == "raw" else nuclear_image
        run([sys.executable, str(script_dir / "hca_measure.py"), "--labels", str(nuclei_labels), "--image", str(nuclear_measurement_image),
             "--output", str(field_dir / "nuclei-measurements.json")], log_path)
        if segmentation.get("overlays"):
            run([sys.executable, str(script_dir / "hca_overlay.py"), "--image", str(nuclear_image), "--labels", str(nuclei_labels),
                 "--output", str(field_dir / "nuclei-overlay.tif")], log_path)
        results.append(field_result)
    failed = [entry for entry in results if entry.get("relationship_qc") == "failed"]
    args.output_dir.mkdir(parents=True, exist_ok=True)
    embedding_result = None
    embedding = analysis.get("embedding", {})
    if embedding.get("enabled"):
        embedding_path = args.output_dir / "embedding.json"
        embedding_log = args.output_dir / "embedding.log"
        adapter = script_dir / "hca_openphenom_adapter.py" if embedding["adapter_script"] == "bundled" else Path(embedding["adapter_script"])
        if not adapter.is_absolute():
            adapter = args.config.parent / adapter
        command = [sys.executable, str(script_dir / "hca_embed.py"), "--source-root", str(source_root), "--well", well,
                   "--output", str(embedding_path), "--adapter-script", str(adapter),
                   "--environment", embedding.get("environment", "openphenom"),
                   "--conda", embedding.get("conda", "/opt/anaconda3/bin/conda")]
        if embedding.get("model_revision"):
            command.extend(["--model-revision", embedding["model_revision"]])
        if embedding.get("channelwise"):
            command.append("--channelwise")
        run(command, embedding_log)
        embedding_result = str(embedding_path)
    (args.output_dir / "pipeline-summary.json").write_text(json.dumps({"status": "qc_failed" if failed else "complete", "fields": results,
        "relationship_qc_failed": len(failed), "embedding": embedding_result,
        "background_subtraction": background if background.get("enabled") else None}, indent=2) + "\n")
    return 2 if failed and args.fail_on_qc else 0


if __name__ == "__main__":
    raise SystemExit(main())
