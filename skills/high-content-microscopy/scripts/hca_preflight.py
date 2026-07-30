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
    payload = {"engines": sorted(engines), "missing": missing, "ok": not missing}
    print(json.dumps(payload, indent=2))
    return 0 if payload["ok"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
