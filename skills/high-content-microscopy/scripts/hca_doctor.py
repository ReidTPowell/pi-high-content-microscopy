#!/usr/bin/env python3
"""Report whether a Pi HCA workflow can be activated safely in the current environment."""
from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--source-root", type=Path)
    args = parser.parse_args()
    config = json.loads(args.config.read_text())
    segmentation = config.get("analysis", {}).get("segmentation", {})
    engines = {item.get("engine") for item in (segmentation.get("nucleus", {}), segmentation.get("cell", {})) if item.get("enabled")}
    modules = {"threshold": ("numpy", "tifffile", "scipy"), "cellpose": ("numpy", "tifffile", "cellpose"), "stardist": ("numpy", "tifffile", "stardist")}
    missing = sorted({module for engine in engines for module in modules.get(engine, ()) if importlib.util.find_spec(module) is None})
    source_exists = args.source_root is None or args.source_root.is_dir()
    payload = {"python": sys.executable, "config": str(args.config), "engines": sorted(engines),
               "missing_modules": missing, "source_root_ok": source_exists,
               "ready": not missing and source_exists,
               "next_action": "run hca_pipeline.py" if not missing and source_exists else "run setup_env.sh with the required extras"}
    print(json.dumps(payload, indent=2))
    return 0 if payload["ready"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
