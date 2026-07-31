#!/usr/bin/env python3
"""Report whether a Pi HCA workflow can be activated safely in the current environment."""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

from hca_embed import resolve_environment_python
from hca_runtime import verify


def active_pihca_sources(cwd: Path) -> list[str]:
    settings = [Path(os.environ.get("PI_CODING_AGENT_DIR", "~/.pi/agent")).expanduser() / "settings.json"]
    settings.extend(parent / ".pi" / "settings.json" for parent in (cwd, *cwd.parents))
    sources = []
    for path in dict.fromkeys(settings):
        if not path.is_file():
            continue
        try:
            packages = json.loads(path.read_text(encoding="utf-8")).get("packages", [])
        except (OSError, json.JSONDecodeError):
            continue
        for package in packages:
            if not isinstance(package, str):
                continue
            resolved = (path.parent / package).resolve() if not package.startswith(("http:", "https:", "github:", "npm:")) else None
            package_file = resolved / "package.json" if resolved else None
            package_name = ""
            if package_file and package_file.is_file():
                try:
                    package_name = json.loads(package_file.read_text(encoding="utf-8")).get("name", "")
                except (OSError, json.JSONDecodeError):
                    pass
            if "pi-high-content-microscopy" in package.lower() or package_name == "pi-high-content-microscopy":
                sources.append(f"{path}: {package}")
    return sorted(set(sources))


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
    analysis = config.get("analysis", {})
    measurements = analysis.get("measurements", [])
    metrics = measurements.get("metrics", []) if isinstance(measurements, dict) else measurements
    features = analysis.get("features", {})
    if metrics or features.get("puncta") or features.get("confluence", {}).get("enabled"):
        missing = sorted(set(missing) | {module for module in ("numpy", "scipy", "tifffile")
                                        if importlib.util.find_spec(module) is None})
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
                check = subprocess.run(
                    [str(environment_python), "-c", "import PIL,numpy,torch,huggingface_hub,timm,safetensors,yaml"],
                                       capture_output=True, text=True)
                if check.returncode:
                    embedding_errors.append("OpenPhenom environment is incomplete: " + check.stderr.strip().splitlines()[-1])
            except (OSError, ValueError, json.JSONDecodeError) as error:
                embedding_errors.append(str(error))
    source_exists = args.source_root is None or args.source_root.is_dir()
    runtime_ready, runtime_errors = (True, []) if args.runtime_lock is None else verify(args.runtime_lock)
    pihca_sources = active_pihca_sources(Path.cwd())
    activation_errors = [] if len(pihca_sources) <= 1 else [
        "PiHCA is active from multiple Pi package sources; remove the project-local or global duplicate"
    ]
    ready = not missing and not embedding_errors and not activation_errors and source_exists and runtime_ready
    if activation_errors:
        next_action = "remove one duplicate PiHCA package source, then restart Pi"
    elif ready:
        next_action = "continue the guarded PiHCA workflow"
    else:
        next_action = "run setup_env.sh with required extras, configure the embedding adapter, or recreate the runtime lock"
    payload = {"python": sys.executable, "config": str(args.config), "engines": sorted(engines),
               "missing_modules": missing, "source_root_ok": source_exists,
               "runtime_lock": None if args.runtime_lock is None else str(args.runtime_lock), "runtime_errors": runtime_errors,
               "embedding_errors": embedding_errors, "pihca_package_sources": pihca_sources,
               "activation_errors": activation_errors, "ready": ready, "next_action": next_action}
    print(json.dumps(payload, indent=2))
    return 0 if payload["ready"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
