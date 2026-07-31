#!/usr/bin/env python3
"""Version an assay draft and build one reproducible preconfiguration packet."""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from copy import deepcopy
from pathlib import Path

from hca_contract import atomic_write_json, default_output_dir, sha256, validate_config


def next_version(directory: Path, prefix: str) -> int:
    versions = []
    for path in directory.glob(f"{prefix}-*.json") if directory.exists() else []:
        try:
            versions.append(int(path.stem.rsplit("-", 1)[1]))
        except (IndexError, ValueError):
            continue
    return max(versions, default=0) + 1


def build_config(template: dict, source: Path, mode: str, endpoint: str | None,
                 blinded: bool, plate_map: Path | None) -> dict:
    config = deepcopy(template)
    config.setdefault("input", {})["source_root"] = str(source.resolve())
    config.setdefault("analysis", {}).setdefault("optimization", {})["mode"] = mode
    config["assay_contract"] = {
        "biological_endpoint": endpoint or "pending",
        "plate_map": str(plate_map.resolve()) if plate_map else None,
        "segmentation_optimization_blinded": blinded,
    }
    return config


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, type=Path, help="Exactly one acquisition root")
    parser.add_argument("--config-template", required=True, type=Path,
                        help="Confirmed assay profile to copy; the template is never modified")
    parser.add_argument("--optimization-mode", choices=("human", "automated"), default="human")
    parser.add_argument("--endpoint", help="Biological endpoint; may remain pending during blinded segmentation tuning")
    parser.add_argument("--blinded", action="store_true",
                        help="Permit segmentation tuning before treatment/control identities are supplied")
    parser.add_argument("--plate-map", type=Path)
    parser.add_argument("--output-dir", type=Path, help="Defaults to <Barcode>_piHCA")
    parser.add_argument("--workers", type=int, default=1)
    parser.add_argument("--sample-size", type=int, default=48)
    parser.add_argument("--seed", type=int, default=7)
    args = parser.parse_args()

    source = args.input.expanduser().resolve()
    template_path = args.config_template.expanduser().resolve()
    if not source.is_dir():
        parser.error(f"input is not a directory: {source}")
    if not template_path.is_file():
        parser.error(f"config template does not exist: {template_path}")
    if args.plate_map and not args.plate_map.expanduser().is_file():
        parser.error(f"plate map does not exist: {args.plate_map}")
    if args.workers < 1:
        parser.error("--workers must be at least 1")

    template = json.loads(template_path.read_text(encoding="utf-8"))
    config = build_config(template, source, args.optimization_mode, args.endpoint, args.blinded, args.plate_map)
    errors = validate_config(config)
    if errors:
        parser.error("invalid assay template: " + "; ".join(errors))

    output = (args.output_dir or default_output_dir(source)).expanduser().resolve()
    config_dir = output / "configs"
    config_version = next_version(config_dir, "assay")
    config_path = config_dir / f"assay-{config_version:03d}.json"
    atomic_write_json(config_path, config)

    packets = output / "preconfigurations"
    packet_version = max(
        (int(path.name.rsplit("-", 1)[1]) for path in packets.glob("preconfiguration-*")
         if path.name.rsplit("-", 1)[-1].isdigit()),
        default=0,
    ) + 1
    packet = packets / f"preconfiguration-{packet_version:03d}"
    command = [
        sys.executable, str(Path(__file__).parent / "hca_preconfigure.py"),
        "--input", str(source), "--config", str(config_path), "--output-dir", str(packet),
        "--workers", str(args.workers), "--sample-size", str(args.sample_size), "--seed", str(args.seed),
    ]
    if args.plate_map:
        command.extend(["--plate-map", str(args.plate_map.expanduser().resolve())])
    result = subprocess.run(command, capture_output=True, text=True)
    if result.returncode:
        raise RuntimeError(result.stderr.strip() or result.stdout.strip() or "preconfiguration failed")
    prepared = json.loads(result.stdout)
    preconfiguration_path = packet / "preconfiguration.json"
    preconfiguration = json.loads(preconfiguration_path.read_text(encoding="utf-8"))
    pilot_plan = packet / "pilot-fields.json"
    pilot_result = subprocess.run([
        sys.executable, str(Path(__file__).parent / "hca_pilot_plan.py"),
        "--manifest", preconfiguration["manifest"], "--config", str(config_path),
        "--output", str(pilot_plan), "--count", "3",
    ], capture_output=True, text=True)
    if pilot_result.returncode:
        raise RuntimeError(pilot_result.stderr.strip() or pilot_result.stdout.strip() or "pilot field selection failed")

    runtime_ready = prepared["status"] == "pending_human_review"
    runtime_lock = output / "runtime" / "runtime-lock.json"
    runtime_result = subprocess.run([
        sys.executable, str(Path(__file__).parent / "hca_runtime.py"), "capture", "--output", str(runtime_lock),
    ], capture_output=True, text=True)
    if runtime_result.returncode:
        raise RuntimeError(runtime_result.stderr.strip() or runtime_result.stdout.strip() or "runtime capture failed")
    phase = "pilot_segmentation_required" if runtime_ready else "runtime_setup_required"
    next_action = (
        "Run a bounded nuclei Cellpose sweep on the paired fields in pilot-fields.json, then review nuclei "
        "before tuning cell boundaries and relational assignment."
        if runtime_ready else
        "Create or select the locked PiHCA Cellpose runtime, then rerun pihca_prepare with PIHCA_PYTHON set to that interpreter."
    )

    state = {
        "schema_version": 1,
        "phase": phase,
        "input": str(source),
        "output": str(output),
        "config": str(config_path),
        "config_sha256": sha256(config_path),
        "preconfiguration": str(preconfiguration_path),
        "manifest": preconfiguration["manifest"],
        "well_plan": preconfiguration["well_plan"],
        "runtime_lock": str(runtime_lock),
        "pilot_fields": str(pilot_plan),
        "review_history": [],
        "optimization_mode": args.optimization_mode,
        "blinded": args.blinded,
        "plate_map_required_before_biological_analysis": args.plate_map is None,
        "next_action": next_action,
    }
    atomic_write_json(output / "workflow-state.json", state)
    print(json.dumps({
        "status": prepared["status"],
        "phase": phase,
        "config": str(config_path),
        "config_sha256": state["config_sha256"],
        "preconfiguration": state["preconfiguration"],
        "runtime_lock": str(runtime_lock),
        "pilot_fields": str(pilot_plan),
        "workflow_state": str(output / "workflow-state.json"),
        "blinded": args.blinded,
        "next_action": next_action,
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
