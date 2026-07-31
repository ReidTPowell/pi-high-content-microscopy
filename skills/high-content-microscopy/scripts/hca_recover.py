#!/usr/bin/env python3
"""Archive stale PiHCA staging directories without deleting evidence."""
from __future__ import annotations

import argparse
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path


def archive(run_dir: Path) -> list[dict]:
    wells = run_dir / "wells"
    staging = sorted(path for path in wells.glob(".*.staging") if path.is_dir()) if wells.is_dir() else []
    if not staging:
        return []
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    recovery = run_dir / "recovery" / stamp
    recovery.mkdir(parents=True, exist_ok=False)
    records = []
    for source in staging:
        destination = recovery / source.name
        shutil.move(str(source), destination)
        records.append({"source": str(source), "archived": str(destination)})
    (recovery / "recovery.json").write_text(json.dumps({"archived": records}, indent=2) + "\n", encoding="utf-8")
    return records


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", required=True, type=Path)
    args = parser.parse_args()
    records = archive(args.run_dir.expanduser().resolve())
    print(json.dumps({"status": "archived" if records else "clean", "count": len(records), "artifacts": records}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
