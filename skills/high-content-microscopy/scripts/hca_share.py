#!/usr/bin/env python3
"""Create a portable share bundle containing configuration, provenance, and results, never source images."""
from __future__ import annotations

import argparse
import shutil
import tempfile
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--analysis-dir", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path, help="Archive path without .zip")
    args = parser.parse_args()
    source = args.analysis_dir.resolve()
    if not source.is_dir():
        parser.error("analysis directory does not exist")
    bundle = args.output.resolve()
    bundle.parent.mkdir(parents=True, exist_ok=True)
    allowed = {".json", ".jsonl", ".csv", ".html", ".md", ".txt", ".png", ".yaml", ".yml"}
    derived_tiff_suffixes = ("labels.tif", "overlay.tif", "by-cell.tif")
    with tempfile.TemporaryDirectory() as temporary:
        staging = Path(temporary) / "analysis"
        for path in source.rglob("*"):
            if path.is_file() and (path.suffix.lower() in allowed or path.name.lower().endswith(derived_tiff_suffixes)):
                target = staging / path.relative_to(source)
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(path, target)
        archive = shutil.make_archive(str(bundle), "zip", root_dir=staging)
    print(archive)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
