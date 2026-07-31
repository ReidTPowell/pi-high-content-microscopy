#!/usr/bin/env python3
"""Fail early when an assay configuration requests unavailable analysis engines."""
from __future__ import annotations

import argparse
import importlib.util
import json


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True)
    args = parser.parse_args()
    config = json.loads(open(args.config, encoding="utf-8").read())
    segmentation = config.get("analysis", {}).get("segmentation", {})
    engines = {stage.get("engine") for stage in (segmentation.get("nucleus", {}), segmentation.get("cell", {})) if stage.get("enabled")}
    required = {"threshold": ["numpy", "tifffile", "scipy"], "cellpose": ["numpy", "tifffile", "cellpose"], "stardist": ["numpy", "tifffile", "stardist"]}
    missing = {engine: [module for module in required[engine] if importlib.util.find_spec(module) is None] for engine in engines}
    missing = {engine: modules for engine, modules in missing.items() if modules}
    analysis = config.get("analysis", {})
    measurements = analysis.get("measurements", [])
    metrics = measurements.get("metrics", []) if isinstance(measurements, dict) else measurements
    features = analysis.get("features", {})
    analysis_modules = [module for module in ("numpy", "scipy", "tifffile")
                        if (metrics or features.get("puncta") or features.get("confluence", {}).get("enabled"))
                        and importlib.util.find_spec(module) is None]
    if analysis_modules:
        missing["analysis"] = analysis_modules
    payload = {"engines": sorted(engines), "missing": missing, "ok": not missing}
    print(json.dumps(payload, indent=2))
    return 0 if payload["ok"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
