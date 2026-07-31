#!/usr/bin/env python3
"""List and resolve the bundled executable PiHCA assay templates."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


CONFIG_DIR = Path(__file__).parent.parent / "configs"


def catalog() -> list[dict]:
    templates = []
    for path in sorted(CONFIG_DIR.glob("*.json")):
        config = json.loads(path.read_text(encoding="utf-8"))
        metadata = config.get("template")
        if metadata:
            templates.append({**metadata, "path": str(path.resolve()),
                              "channels": config.get("channels", {}),
                              "unit_of_analysis": config.get("analysis", {}).get("unit_of_analysis")})
    return templates


def resolve_template(template_id: str) -> tuple[Path, dict]:
    matches = [(Path(item["path"]), item) for item in catalog() if item["id"] == template_id]
    if len(matches) != 1:
        raise ValueError(f"unknown or ambiguous template: {template_id}")
    return matches[0]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="action", required=True)
    commands.add_parser("list")
    for action in ("show", "path"):
        item = commands.add_parser(action)
        item.add_argument("template_id")
    args = parser.parse_args()
    try:
        if args.action == "list":
            payload = {"schema_version": 1, "templates": catalog()}
        else:
            path, metadata = resolve_template(args.template_id)
            payload = json.loads(path.read_text(encoding="utf-8")) if args.action == "show" else {"id": metadata["id"], "path": str(path)}
        print(json.dumps(payload, indent=2))
        return 0
    except (OSError, ValueError, json.JSONDecodeError) as error:
        print(json.dumps({"error": str(error)}), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
