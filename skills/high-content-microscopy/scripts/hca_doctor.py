#!/usr/bin/env python3
"""Report whether a Pi HCA workflow can be activated safely in the current environment."""
from __future__ import annotations

import argparse
import importlib.util
import json
import shutil
import subprocess
import sys
from pathlib import Path

from hca_embed import resolve_environment_python
from hca_runtime import verify


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--source-root", type=Path)
    parser.add_argument("--runtime-lock", type=Path, help="Runtime lock created by hca_runtime.py capture")
    args = parser.parse_args()
    config = json.loads(args.config.read_text())
    segmentation = config.get("analysis", {}).get("segmentation", {})
    engines = {item.get("engine") for item in (segmentation.get("nucleus", {}), segmentation.get("cell", {})) if item.get("enabled")}
    modules = {"threshold": ("numpy", "tifffile", "scipy"), "cellpose": ("numpy", "tifffile", "cellpose"), "stardist": ("numpy", "tifffile", "stardist")}
    missing = sorted({module for engine in engines for module in modules.get(engine, ()) if importlib.util.find_spec(module) is None})
    background = config.get("analysis", {}).get("preprocessing", {}).get("background_subtraction", {})
    if background.get("enabled"):
        required = {"numpy", "tifffile"} | ({"scipy"} if background.get("method") == "opening" else set())
        missing = sorted(set(missing) | {module for module in required if importlib.util.find_spec(module) is None})
    embedding = config.get("analysis", {}).get("embedding", {})
    embedding_errors = []
    if embedding.get("enabled"):
        adapter_value = embedding.get("adapter_script", "")
        adapter = Path(__file__).parent / "hca_openphenom_adapter.py" if adapter_value == "bundled" else Path(adapter_value)
        if adapter_value != "bundled" and not adapter.is_absolute():
            adapter = args.config.parent / adapter
        conda = embedding.get("conda", "/opt/anaconda3/bin/conda")
        if not adapter.is_file():
            embedding_errors.append(f"OpenPhenom adapter not found: {adapter}")
        if not Path(conda).is_file() and shutil.which(conda) is None:
            embedding_errors.append(f"Conda executable not found: {conda}")
        else:
            try:
                environment_python = resolve_environment_python(Path(conda), embedding.get("environment", "openphenom"))
                check = subprocess.run([str(environment_python), "-c", "import PIL,numpy,torch,huggingface_hub"],
                                       capture_output=True, text=True)
                if check.returncode:
                    embedding_errors.append("OpenPhenom environment is incomplete: " + check.stderr.strip().splitlines()[-1])
            except (OSError, ValueError, json.JSONDecodeError) as error:
                embedding_errors.append(str(error))
    source_exists = args.source_root is None or args.source_root.is_dir()
    runtime_ready, runtime_errors = (True, []) if args.runtime_lock is None else verify(args.runtime_lock)
    payload = {"python": sys.executable, "config": str(args.config), "engines": sorted(engines),
               "missing_modules": missing, "source_root_ok": source_exists,
               "runtime_lock": None if args.runtime_lock is None else str(args.runtime_lock), "runtime_errors": runtime_errors,
               "embedding_errors": embedding_errors,
               "ready": not missing and not embedding_errors and source_exists and runtime_ready,
               "next_action": "run hca_pipeline.py" if not missing and not embedding_errors and source_exists and runtime_ready else "run setup_env.sh with required extras, configure the embedding adapter, or recreate the runtime lock"}
    print(json.dumps(payload, indent=2))
    return 0 if payload["ready"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
