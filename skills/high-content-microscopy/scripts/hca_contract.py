"""Shared, dependency-free contract and provenance helpers."""
from __future__ import annotations

import hashlib
import json
import platform
import re
import sys
from pathlib import Path
from typing import Any

from hca_resources import gpu_inventory

REQUIRED_RECORD_FIELDS = {"path", "format", "adapter", "plate", "well", "row", "column", "site", "timepoint", "channel", "z", "prefix"}
BARCODE_PATTERN = re.compile(r'"(?:barcode|plateid)"\s*:\s*"([^"]+)"', re.IGNORECASE)


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    """Publish JSON only after the complete artifact has been written."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(path)


def barcode_for_input(input_root: Path) -> str | None:
    """Read a barcode from the acquisition metadata without relying on folder names."""
    for path in sorted(input_root.rglob("*.jdce")) + sorted(input_root.glob("*.mxprotocol")):
        match = BARCODE_PATTERN.search(path.read_text(encoding="utf-8", errors="ignore"))
        if match:
            return match.group(1).strip()
    return None


def default_output_dir(input_root: Path) -> Path:
    """Place barcode_piHCA beside the barcode-level raw input folder."""
    root = input_root.resolve()
    barcode = barcode_for_input(root)
    if barcode:
        for candidate in (root, *root.parents):
            if candidate.name == barcode:
                return candidate.parent / f"{barcode}_piHCA"
        return root.parent / f"{barcode}_piHCA"
    return root.parent / f"{root.name}_piHCA"


def validate_record(record: dict[str, Any]) -> list[str]:
    errors = [f"missing {field}" for field in sorted(REQUIRED_RECORD_FIELDS - record.keys())]
    if not isinstance(record.get("path"), str) or not record.get("path"):
        errors.append("path must be a non-empty string")
    for field in ("column", "timepoint", "channel", "z"):
        value = record.get(field)
        if value is not None and (not isinstance(value, int) or value < (1 if field == "column" else 0)):
            errors.append(f"{field} must be null or a valid non-negative integer")
    return errors


def validate_config(config: dict[str, Any]) -> list[str]:
    errors = []
    for field in ("name", "input", "channels", "analysis"):
        if field not in config:
            errors.append(f"configuration missing {field}")
    if isinstance(config.get("input"), dict) and not config["input"].get("adapter"):
        errors.append("input.adapter is required")
    if isinstance(config.get("analysis"), dict) and not config["analysis"].get("unit_of_analysis"):
        errors.append("analysis.unit_of_analysis is required")
    segmentation = config.get("analysis", {}).get("segmentation", {}) if isinstance(config.get("analysis"), dict) else {}
    channels = config.get("channels", {}) if isinstance(config.get("channels"), dict) else {}
    role_counts = {role: sum(details.get("role") == role for details in channels.values() if isinstance(details, dict))
                   for role in {details.get("role") for details in channels.values() if isinstance(details, dict) and details.get("role")}}
    preprocessing = config.get("analysis", {}).get("preprocessing", {}) if isinstance(config.get("analysis"), dict) else {}
    background = preprocessing.get("background_subtraction", {}) if isinstance(preprocessing, dict) else {}
    if background.get("enabled"):
        if background.get("method", "percentile") not in {"percentile", "opening"}:
            errors.append("analysis.preprocessing.background_subtraction.method must be percentile or opening")
        percentile = background.get("percentile", 5.0)
        if not isinstance(percentile, (int, float)) or not 0 <= percentile <= 100:
            errors.append("analysis.preprocessing.background_subtraction.percentile must be 0..100")
        radius = background.get("radius", 25)
        if not isinstance(radius, int) or radius < 1:
            errors.append("analysis.preprocessing.background_subtraction.radius must be a positive integer")
    optimization = config.get("analysis", {}).get("optimization", {}) if isinstance(config.get("analysis"), dict) else {}
    if optimization and optimization.get("mode", "human") not in {"human", "automated"}:
        errors.append("analysis.optimization.mode must be human or automated")
    if optimization and (not isinstance(optimization.get("max_rounds", 3), int) or optimization.get("max_rounds", 3) < 1):
        errors.append("analysis.optimization.max_rounds must be a positive integer")
    score = optimization.get("vision_acceptance_score", 90) if isinstance(optimization, dict) else 90
    if not isinstance(score, (int, float)) or not 0 <= score <= 100:
        errors.append("analysis.optimization.vision_acceptance_score must be 0..100")
    for key, default in (("minimum_heldout_wells", 3), ("minimum_heldout_fields", 9)):
        value = optimization.get(key, default) if isinstance(optimization, dict) else default
        if not isinstance(value, int) or value < 1:
            errors.append(f"analysis.optimization.{key} must be a positive integer")
    embedding = config.get("analysis", {}).get("embedding", {}) if isinstance(config.get("analysis"), dict) else {}
    if embedding.get("enabled") and not embedding.get("adapter_script"):
        errors.append("analysis.embedding.adapter_script is required when embedding is enabled")
    if embedding.get("enabled") and not embedding.get("model_revision"):
        errors.append("analysis.embedding.model_revision must pin an immutable model revision when embedding is enabled")
    if embedding and embedding.get("provider", "openphenom") != "openphenom":
        errors.append("analysis.embedding.provider must be openphenom")
    measurement_image = config.get("analysis", {}).get("measurement_image", "corrected") if isinstance(config.get("analysis"), dict) else "corrected"
    if measurement_image not in {"raw", "corrected"}:
        errors.append("analysis.measurement_image must be raw or corrected")
    for stage_name in ("nucleus", "cell"):
        stage = segmentation.get(stage_name, {}) if isinstance(segmentation, dict) else {}
        if stage.get("enabled"):
            if stage.get("engine") not in {"threshold", "cellpose", "stardist"}:
                errors.append(f"analysis.segmentation.{stage_name}.engine is unsupported")
            role = stage.get("channel_role")
            if not role or role_counts.get(role) != 1:
                errors.append(f"analysis.segmentation.{stage_name}.channel_role must identify exactly one channel")
        criteria = stage.get("filter", {}) if isinstance(stage, dict) else {}
        if criteria and not isinstance(criteria, dict):
            errors.append(f"analysis.segmentation.{stage_name}.filter must be an object")
            continue
        for key in ("min_area_px", "max_area_px", "min_intensity_mean", "max_intensity_mean"):
            value = criteria.get(key) if isinstance(criteria, dict) else None
            if value is not None and (not isinstance(value, (int, float)) or value < 0):
                errors.append(f"analysis.segmentation.{stage_name}.filter.{key} must be null or a non-negative number")
        if criteria.get("min_area_px") is not None and criteria.get("max_area_px") is not None and criteria["min_area_px"] > criteria["max_area_px"]:
            errors.append(f"analysis.segmentation.{stage_name}.filter minimum area exceeds maximum area")
        if criteria.get("min_intensity_mean") is not None and criteria.get("max_intensity_mean") is not None and criteria["min_intensity_mean"] > criteria["max_intensity_mean"]:
            errors.append(f"analysis.segmentation.{stage_name}.filter minimum intensity exceeds maximum intensity")
    relationship = segmentation.get("relationship", {}) if isinstance(segmentation, dict) else {}
    if relationship.get("enabled") and not all(segmentation.get(stage, {}).get("enabled") for stage in ("nucleus", "cell")):
        errors.append("analysis.segmentation.relationship requires enabled nucleus and cell stages")
    measurements = config.get("analysis", {}).get("measurements", []) if isinstance(config.get("analysis"), dict) else []
    if not isinstance(measurements, (list, dict)):
        errors.append("analysis.measurements must be an array or measurement-pack object")
    if isinstance(measurements, dict):
        allowed_metrics = {"count", "area", "shape", "intensity", "texture"}
        if unknown := sorted(set(measurements.get("metrics", [])) - allowed_metrics):
            errors.append("analysis.measurements contains unsupported metrics: " + ", ".join(unknown))
        available_regions = {stage for stage in ("nucleus", "cell") if segmentation.get(stage, {}).get("enabled")}
        if relationship.get("enabled"):
            available_regions.add("cytoplasm")
        if missing := sorted(set(measurements.get("regions", [])) - available_regions):
            errors.append("analysis.measurements regions require unavailable masks: " + ", ".join(missing))
        for role in measurements.get("channel_roles", []):
            if role_counts.get(role) != 1:
                errors.append(f"analysis.measurements channel role must identify exactly one channel: {role}")
    features = config.get("analysis", {}).get("features", {}) if isinstance(config.get("analysis"), dict) else {}
    puncta = features.get("puncta", []) if isinstance(features, dict) else []
    if isinstance(puncta, dict):
        puncta = [puncta]
    for index, feature in enumerate(puncta):
        if not feature.get("enabled", True):
            continue
        role = feature.get("channel_role")
        if role_counts.get(role) != 1:
            errors.append(f"analysis.features.puncta[{index}] channel_role must identify exactly one channel")
        parent = feature.get("parent_region")
        available = {stage for stage in ("nucleus", "cell") if segmentation.get(stage, {}).get("enabled")}
        if relationship.get("enabled"):
            available.add("cytoplasm")
        if parent and parent not in available:
            errors.append(f"analysis.features.puncta[{index}] parent_region is unavailable: {parent}")
        percentile = feature.get("threshold_percentile", 99.5)
        if not isinstance(percentile, (int, float)) or not 0 < percentile < 100:
            errors.append(f"analysis.features.puncta[{index}] threshold_percentile must be between 0 and 100")
        minimum, maximum = feature.get("min_area_px", 2), feature.get("max_area_px")
        if not isinstance(minimum, int) or minimum < 1 or (maximum is not None and (not isinstance(maximum, int) or maximum < minimum)):
            errors.append(f"analysis.features.puncta[{index}] area limits are invalid")
    confluence = features.get("confluence", {}) if isinstance(features, dict) else {}
    if confluence.get("enabled"):
        if role_counts.get(confluence.get("channel_role")) != 1:
            errors.append("analysis.features.confluence.channel_role must identify exactly one channel")
        if confluence.get("polarity", "bright") not in {"bright", "dark"}:
            errors.append("analysis.features.confluence.polarity must be bright or dark")
    classification = config.get("analysis", {}).get("classification", {}) if isinstance(config.get("analysis"), dict) else {}
    if classification.get("enabled"):
        region = classification.get("region")
        measured_roles = set(measurements.get("channel_roles", [])) if isinstance(measurements, dict) else set()
        channel_metrics = {"mean", "median", "sum", "min", "max", "p10", "p90", "std", "cv", "entropy"}
        object_metrics = {"area_px", "perimeter_px", "circularity", "eccentricity", "aspect_ratio", "extent", "solidity"}
        if not isinstance(measurements, dict) or region not in measurements.get("regions", []):
            errors.append("analysis.classification.region must be included in measurement regions")
        if not classification.get("rules"):
            errors.append("analysis.classification.rules are required when classification is enabled")
        for index, rule in enumerate(classification.get("rules", [])):
            if not rule.get("label") or rule.get("match", "all") not in {"all", "any"} or not rule.get("conditions"):
                errors.append(f"analysis.classification.rules[{index}] is incomplete")
            for condition in rule.get("conditions", []):
                if condition.get("operator") not in {">", ">=", "<", "<=", "==", "!="}:
                    errors.append(f"analysis.classification.rules[{index}] has an invalid operator")
                if not isinstance(condition.get("threshold"), (int, float)):
                    errors.append(f"analysis.classification.rules[{index}] threshold must be numeric")
                role = condition.get("channel_role")
                if role and role_counts.get(role) != 1:
                    errors.append(f"analysis.classification.rules[{index}] channel role is unavailable: {role}")
                if role and role not in measured_roles:
                    errors.append(f"analysis.classification.rules[{index}] channel role is not measured: {role}")
                metric = condition.get("metric")
                if metric not in (channel_metrics if role else object_metrics):
                    errors.append(f"analysis.classification.rules[{index}] metric is unsupported: {metric}")
    derived = config.get("analysis", {}).get("derived_metrics", []) if isinstance(config.get("analysis"), dict) else []
    if unknown := sorted(set(derived) - {"nucleus_to_cytoplasm_ratio"}):
        errors.append("analysis.derived_metrics contains unsupported values: " + ", ".join(unknown))
    if "nucleus_to_cytoplasm_ratio" in derived:
        regions = measurements.get("regions", []) if isinstance(measurements, dict) else []
        if not relationship.get("enabled") or not {"nucleus", "cytoplasm"}.issubset(regions):
            errors.append("nucleus_to_cytoplasm_ratio requires relational nucleus and cytoplasm measurements")
    return errors


def provenance(manifest: Path, config: Path | None = None, runtime_lock: Path | None = None) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "manifest": str(manifest.resolve()), "manifest_sha256": sha256(manifest),
        "python": sys.version, "platform": platform.platform(), "gpus": gpu_inventory(),
    }
    if config:
        payload.update({"config": str(config.resolve()), "config_sha256": sha256(config)})
    if runtime_lock:
        payload.update({"runtime_lock": str(runtime_lock.resolve()), "runtime_lock_sha256": sha256(runtime_lock)})
    return payload
