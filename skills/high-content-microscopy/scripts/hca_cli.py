"""Installed command dispatcher for PiHCA workflow utilities."""
from __future__ import annotations

import importlib
import sys
from importlib.metadata import PackageNotFoundError, version


COMMANDS = {
    "doctor": "hca_doctor",
    "intake": "hca_intake",
    "prepare": "hca_prepare",
    "workflow": "hca_workflow",
    "release": "hca_release",
    "runner": "hca_runner",
    "queue": "hca_queue",
    "production": "hca_production",
    "recover": "hca_recover",
    "report": "hca_report",
    "share": "hca_share",
}


def main() -> int:
    available = ", ".join(sorted(COMMANDS))
    usage = f"Usage: pihca <command> [args]\nCommands: {available}"
    if len(sys.argv) >= 2 and sys.argv[1] in {"-h", "--help"}:
        print(usage)
        return 0
    if len(sys.argv) >= 2 and sys.argv[1] in {"-V", "--version"}:
        try:
            installed_version = version("pi-high-content-microscopy")
        except PackageNotFoundError:
            installed_version = "development"
        print(f"pihca {installed_version}")
        return 0
    if len(sys.argv) < 2 or sys.argv[1] not in COMMANDS:
        print(usage, file=sys.stderr)
        return 2
    command = sys.argv.pop(1)
    return int(importlib.import_module(COMMANDS[command]).main())


if __name__ == "__main__":
    raise SystemExit(main())
