#!/usr/bin/env python3
"""Capture and verify a reproducible Pi HCA Python runtime lock."""
from __future__ import annotations

import argparse
import importlib.metadata
import json
import platform
import sys
from datetime import datetime, timezone
from pathlib import Path

from hca_contract import atomic_write_json, gpu_inventory, sha256


def installed_packages() -> dict[str, str]:
    return {dist.metadata["Name"].lower(): dist.version for dist in importlib.metadata.distributions() if dist.metadata.get("Name")}


def capture(output: Path) -> dict:
    payload = {
        "schema_version": 1,
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "python_executable": sys.executable,
        "python_version": platform.python_version(),
        "platform": platform.platform(),
        "packages": installed_packages(),
        "gpus": gpu_inventory(),
    }
    atomic_write_json(output, payload)
    return payload


def verify(lock: Path) -> tuple[bool, list[str]]:
    if not lock.is_file():
        return False, [f"runtime lock does not exist: {lock}"]
    expected = json.loads(lock.read_text(encoding="utf-8"))
    actual = installed_packages()
    errors = []
    if expected.get("python_version") != platform.python_version():
        errors.append("python version differs from runtime lock")
    for name, version in expected.get("packages", {}).items():
        if actual.get(name) != version:
            errors.append(f"package mismatch: {name} expected {version}, found {actual.get(name, 'missing')}")
    return not errors, errors


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="action", required=True)
    capture_parser = commands.add_parser("capture")
    capture_parser.add_argument("--output", required=True, type=Path)
    verify_parser = commands.add_parser("verify")
    verify_parser.add_argument("--lock", required=True, type=Path)
    args = parser.parse_args()
    if args.action == "capture":
        payload = capture(args.output)
        print(json.dumps({"output": str(args.output), "sha256": sha256(args.output), "packages": len(payload["packages"])}, indent=2))
        return 0
    ready, errors = verify(args.lock)
    print(json.dumps({"lock": str(args.lock), "ready": ready, "errors": errors}, indent=2))
    return 0 if ready else 2


if __name__ == "__main__":
    raise SystemExit(main())
