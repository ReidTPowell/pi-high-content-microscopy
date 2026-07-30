"""Shared, dependency-free contract and provenance helpers."""
from __future__ import annotations

import hashlib
import json
import platform
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

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
    for stage_name in ("nucleus", "cell"):
        stage = segmentation.get(stage_name, {}) if isinstance(segmentation, dict) else {}
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
    return errors


def gpu_inventory() -> list[dict[str, int]]:
    try:
        output = subprocess.check_output(
            ["nvidia-smi", "--query-gpu=index,memory.free", "--format=csv,noheader,nounits"], text=True
        )
    except (FileNotFoundError, subprocess.CalledProcessError):
        return []
    return [{"index": int(row.split(",")[0]), "free_mib": int(row.split(",")[1])} for row in output.splitlines() if row]


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
