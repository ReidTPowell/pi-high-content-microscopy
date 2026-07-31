#!/usr/bin/env python3
"""Execute one configured PiHCA well with optional object, feature, embedding, and measurement stages."""
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


def filter_command(script_dir: Path, raw_labels: Path, output_labels: Path, audit: Path,
                   image: Path | None, criteria: dict) -> list[str]:
    command = [sys.executable, str(script_dir / "hca_filter.py"), "--labels", str(raw_labels),
               "--output", str(output_labels), "--audit", str(audit)]
    if image is not None:
        command.extend(["--image", str(image)])
    flags = {"min_area_px": "--min-area-px", "max_area_px": "--max-area-px",
             "min_intensity_mean": "--min-intensity-mean", "max_intensity_mean": "--max-intensity-mean"}
    for key, flag in flags.items():
        if criteria.get(key) is not None:
            command.extend([flag, str(criteria[key])])
    return command


def measurement_contract(analysis: dict, enabled_regions: list[str]) -> tuple[list[str], list[str], list[str]]:
    configured = analysis.get("measurements", ["count", "area", "intensity"])
    if isinstance(configured, list):
        return configured, enabled_regions, []
    return (configured.get("metrics", ["count", "area", "intensity"]),
            configured.get("regions", enabled_regions), configured.get("channel_roles", []))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--well-manifest", required=True, type=Path)
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--output-dir", type=Path, help="Defaults to <Barcode>_piHCA/wells/<well>")
    parser.add_argument("--source-root", type=Path, help="Root used to resolve manifest-relative image paths")
    parser.add_argument("--allow-overwrite", action="store_true")
    parser.add_argument("--fail-on-qc", action="store_true")
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
    segmentation = analysis.get("segmentation", {})
    background = analysis.get("preprocessing", {}).get("background_subtraction", {})
    nucleus, cell = segmentation.get("nucleus", {}), segmentation.get("cell", {})
    relationship = segmentation.get("relationship", {})
    features = analysis.get("features", {})
    puncta_features = features.get("puncta", [])
    if isinstance(puncta_features, dict):
        puncta_features = [puncta_features]
    confluence = features.get("confluence", {})
    embedding = analysis.get("embedding", {})
    if not nucleus.get("enabled") and not cell.get("enabled") and not confluence.get("enabled") and not embedding.get("enabled"):
        parser.error("enable nucleus/cell segmentation, confluence, or embedding")

    stage_channels = {}
    if nucleus.get("enabled"):
        stage_channels["nucleus"] = channel_for_role(config, nucleus["channel_role"])
    if cell.get("enabled"):
        stage_channels["cell"] = channel_for_role(config, cell["channel_role"])
    feature_channels = {item["channel_role"]: channel_for_role(config, item["channel_role"])
                        for item in puncta_features if item.get("enabled", True)}
    if confluence.get("enabled"):
        feature_channels[confluence["channel_role"]] = channel_for_role(config, confluence["channel_role"])
    default_regions = [region for region, stage in (("nucleus", nucleus), ("cell", cell)) if stage.get("enabled")]
    if relationship.get("enabled"):
        default_regions.append("cytoplasm")
    configured_metrics, configured_regions, configured_roles = measurement_contract(analysis, default_regions)
    measurement_channels = {role: channel_for_role(config, role) for role in configured_roles}

    fields: dict[tuple, dict[int, dict]] = defaultdict(dict)
    for record in records:
        fields[(record["site"], record["timepoint"], record["z"])][record["channel"]] = record
    script_dir, results = Path(__file__).parent, []
    for (site, timepoint, z), by_channel in sorted(fields.items()):
        required_channels = set(stage_channels.values()) | set(feature_channels.values()) | set(measurement_channels.values())
        if missing := sorted(required_channels - set(by_channel)):
            raise RuntimeError(f"missing required channels {missing} in {site}, t{timepoint}, z{z}")
        field_dir = args.output_dir / f"{site}-t{timepoint}-z{z}"
        field_dir.mkdir(parents=True, exist_ok=True)
        log_path = field_dir / "pipeline.log"
        processed_images: dict[int, Path] = {}

        def source_image(channel: int) -> Path:
            return source_root / by_channel[channel]["path"]

        def image_for(channel: int) -> Path:
            source = source_image(channel)
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

        def measurement_image(channel: int) -> Path:
            return source_image(channel) if analysis.get("measurement_image") == "raw" else image_for(channel)

        field_result = {"site": site, "timepoint": timepoint, "z": z}
        labels_by_region: dict[str, Path] = {}
        default_role_by_region: dict[str, str] = {}

        if nucleus.get("enabled"):
            channel = stage_channels["nucleus"]
            image, raw_image = image_for(channel), source_image(channel)
            raw_labels, labels = field_dir / "nuclei-raw-labels.tif", field_dir / "nuclei-labels.tif"
            command = [sys.executable, str(script_dir / "hca_segment.py"), "--image", str(image),
                       "--engine", nucleus["engine"], "--model", nucleus.get("model", "nuclei"), "--output", str(raw_labels)]
            run(append_segmentation_options(command, nucleus), log_path)
            criteria = nucleus.get("filter", {})
            filter_channel = channel_for_role(config, criteria.get("intensity_channel_role", nucleus["channel_role"]))
            audit = field_dir / "nuclei-filter.json"
            run(filter_command(script_dir, raw_labels, labels, audit, image_for(filter_channel), criteria), log_path)
            labels_by_region["nucleus"] = labels
            default_role_by_region["nucleus"] = nucleus["channel_role"]
            field_result.update({"nuclear_source_image": str(raw_image), "nuclear_analysis_image": str(image),
                                 "nuclei_raw_labels": str(raw_labels), "nuclei_labels": str(labels),
                                 "nuclei_filter": str(audit)})
            if segmentation.get("overlays"):
                run([sys.executable, str(script_dir / "hca_overlay.py"), "--image", str(image), "--labels", str(labels),
                     "--output", str(field_dir / "nuclei-overlay.tif")], log_path)

        if cell.get("enabled"):
            channel = stage_channels["cell"]
            image, raw_image = image_for(channel), source_image(channel)
            raw_labels, labels = field_dir / "cell-raw-labels.tif", field_dir / "cell-labels.tif"
            command = [sys.executable, str(script_dir / "hca_segment.py"), "--image", str(image),
                       "--engine", cell["engine"], "--model", cell.get("model", "cyto3"), "--output", str(raw_labels)]
            if cell.get("use_nuclear_image") and nucleus.get("enabled"):
                command.extend(["--nuclear-image", str(image_for(stage_channels["nucleus"]))])
            run(append_segmentation_options(command, cell), log_path)
            criteria = cell.get("filter", {})
            filter_channel = channel_for_role(config, criteria.get("intensity_channel_role", cell["channel_role"]))
            audit = field_dir / "cell-filter.json"
            run(filter_command(script_dir, raw_labels, labels, audit, image_for(filter_channel), criteria), log_path)
            labels_by_region["cell"] = labels
            default_role_by_region["cell"] = cell["channel_role"]
            field_result.update({"cell_source_image": str(raw_image), "cell_analysis_image": str(image),
                                 "cell_labels": str(labels), "cell_raw_labels": str(raw_labels), "cell_filter": str(audit)})
            if segmentation.get("overlays"):
                run([sys.executable, str(script_dir / "hca_overlay.py"), "--image", str(image), "--labels", str(labels),
                     "--output", str(field_dir / "cell-overlay.tif")], log_path)

        if relationship.get("enabled"):
            if not {"nucleus", "cell"}.issubset(labels_by_region):
                raise RuntimeError("relationship segmentation requires both nucleus and cell labels")
            relation_dir = field_dir / "relationship"
            run([sys.executable, str(script_dir / "hca_relate.py"), "--nuclei", str(labels_by_region["nucleus"]),
                 "--cells", str(labels_by_region["cell"]), "--output-dir", str(relation_dir),
                 "--min-overlap", str(relationship.get("min_overlap", 0.5))], log_path)
            relation = json.loads((relation_dir / "relationships.json").read_text())
            denominator = max(relation["nuclei"], 1)
            field_result["relationship"] = {key: relation[key] for key in ("nuclei", "cells", "assigned", "orphan", "ambiguous")}
            field_result["relationship_artifact"] = str(relation_dir / "relationships.json")
            failed = (relation["orphan"] / denominator > relationship.get("max_orphan_fraction", 1.0)
                      or relation["ambiguous"] / denominator > relationship.get("max_ambiguous_fraction", 1.0))
            field_result["relationship_qc"] = "failed" if failed else "passed"
            labels_by_region["cytoplasm"] = relation_dir / "cytoplasm-by-cell.tif"
            default_role_by_region["cytoplasm"] = cell["channel_role"]
            if segmentation.get("overlays"):
                relationship_overlay = field_dir / "relationship-overlay.tif"
                run([sys.executable, str(script_dir / "hca_relational_overlay.py"),
                     "--image", str(image_for(stage_channels["cell"])), "--nuclei", str(labels_by_region["nucleus"]),
                     "--assigned-nuclei", str(relation_dir / "assigned-nuclei-by-cell.tif"),
                     "--cells", str(labels_by_region["cell"]), "--output", str(relationship_overlay)], log_path)
                field_result["relationship_overlay"] = str(relationship_overlay)

        metrics, regions, channel_roles = configured_metrics, configured_regions, configured_roles
        measurement_outputs = {}
        for region in regions:
            if region not in labels_by_region:
                continue
            roles = channel_roles or [default_role_by_region[region]]
            command = [sys.executable, str(script_dir / "hca_measure.py"), "--labels", str(labels_by_region[region]),
                       "--metrics", ",".join(metrics), "--output", str(field_dir / f"{region}-measurements.json")]
            if set(metrics).intersection({"intensity", "texture"}):
                for role in roles:
                    command.extend(["--image", f"{role}={measurement_image(channel_for_role(config, role))}"])
            run(command, log_path)
            measurement_outputs[region] = str(field_dir / f"{region}-measurements.json")
        field_result["measurements"] = measurement_outputs
        classification = analysis.get("classification", {})
        if classification.get("enabled"):
            region = classification.get("region")
            if region not in measurement_outputs:
                raise RuntimeError(f"classification region has no measurements: {region}")
            rules_path = field_dir / "classification-rules.json"
            rules_path.write_text(json.dumps({"default": classification.get("default", "unclassified"),
                                              "rules": classification.get("rules", [])}, indent=2) + "\n")
            classification_path = field_dir / "classification.json"
            run([sys.executable, str(script_dir / "hca_classify.py"), "--measurements", measurement_outputs[region],
                 "--rules", str(rules_path), "--output", str(classification_path)], log_path)
            field_result["classification"] = str(classification_path)
        if "nucleus_to_cytoplasm_ratio" in analysis.get("derived_metrics", []):
            if not {"nucleus", "cytoplasm"}.issubset(measurement_outputs) or not field_result.get("relationship_artifact"):
                raise RuntimeError("nucleus-to-cytoplasm ratio requires relational nucleus and cytoplasm measurements")
            derived = field_dir / "relational-measurements.json"
            run([sys.executable, str(script_dir / "hca_derive.py"), "--nucleus-measurements",
                 measurement_outputs["nucleus"], "--cytoplasm-measurements", measurement_outputs["cytoplasm"],
                 "--relationships", field_result["relationship_artifact"], "--output", str(derived)], log_path)
            field_result["derived_measurements"] = str(derived)

        puncta_outputs = []
        for index, feature in enumerate(puncta_features, start=1):
            if not feature.get("enabled", True):
                continue
            name = feature.get("name", f"puncta-{index}")
            role = feature["channel_role"]
            parent_region = feature.get("parent_region")
            command = [sys.executable, str(script_dir / "hca_puncta.py"), "--image",
                       str(measurement_image(channel_for_role(config, role))),
                       "--output-labels", str(field_dir / f"{name}-labels.tif"),
                       "--output", str(field_dir / f"{name}.json"),
                       "--sigma", str(feature.get("sigma", 1.0)),
                       "--background-sigma", str(feature.get("background_sigma", 4.0)),
                       "--threshold-percentile", str(feature.get("threshold_percentile", 99.5)),
                       "--min-area-px", str(feature.get("min_area_px", 2))]
            if feature.get("max_area_px") is not None:
                command.extend(["--max-area-px", str(feature["max_area_px"])])
            if parent_region:
                if parent_region not in labels_by_region:
                    raise RuntimeError(f"puncta parent region is unavailable: {parent_region}")
                command.extend(["--parent-labels", str(labels_by_region[parent_region])])
            run(command, log_path)
            puncta_outputs.append(str(field_dir / f"{name}.json"))
        if puncta_outputs:
            field_result["puncta"] = puncta_outputs

        if confluence.get("enabled"):
            role = confluence["channel_role"]
            command = [sys.executable, str(script_dir / "hca_confluence.py"), "--image",
                       str(measurement_image(channel_for_role(config, role))),
                       "--output-mask", str(field_dir / "confluence-mask.tif"),
                       "--output", str(field_dir / "confluence.json"),
                       "--polarity", confluence.get("polarity", "bright"),
                       "--smoothing-sigma", str(confluence.get("smoothing_sigma", 1.0)),
                       "--minimum-component-px", str(confluence.get("minimum_component_px", 25))]
            if confluence.get("threshold") is not None:
                command.extend(["--threshold", str(confluence["threshold"])])
            run(command, log_path)
            field_result["confluence"] = str(field_dir / "confluence.json")
            run([sys.executable, str(script_dir / "hca_overlay.py"), "--image",
                 str(measurement_image(channel_for_role(config, role))), "--labels", str(field_dir / "confluence-mask.tif"),
                 "--output", str(field_dir / "confluence-overlay.tif")], log_path)
        results.append(field_result)

    failed_fields = [entry for entry in results if entry.get("relationship_qc") == "failed"]
    args.output_dir.mkdir(parents=True, exist_ok=True)
    embedding_result = None
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
    summary = {"status": "qc_failed" if failed_fields else "complete", "fields": results,
               "relationship_qc_failed": len(failed_fields), "embedding": embedding_result,
               "background_subtraction": background if background.get("enabled") else None}
    (args.output_dir / "pipeline-summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    return 2 if failed_fields and args.fail_on_qc else 0


if __name__ == "__main__":
    raise SystemExit(main())
