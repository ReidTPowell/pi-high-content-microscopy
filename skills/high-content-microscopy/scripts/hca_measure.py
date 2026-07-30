#!/usr/bin/env python3
"""Measure label images independently from the segmentation engine."""
from __future__ import annotations

import argparse
import json
from pathlib import Path


def require(module: str):
    try:
        return __import__(module, fromlist=["*"])
    except ImportError as error:
        raise SystemExit("measurements require: pip install '.[qc]'") from error


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--labels", required=True, type=Path)
    parser.add_argument("--image", type=Path, help="Optional matching single-channel intensity image")
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    tifffile, numpy = require("tifffile"), require("numpy")
    labels = tifffile.imread(args.labels)
    if labels.ndim != 2:
        raise SystemExit("labels must be a two-dimensional label image")
    image = tifffile.imread(args.image) if args.image else None
    rows = []
    for label in (int(value) for value in numpy.unique(labels) if value > 0):
        mask = labels == label
        row = {"object_id": label, "area_px": int(mask.sum())}
        if image is not None:
            values = image[mask]
            row.update({"intensity_mean": float(numpy.mean(values)), "intensity_sum": float(numpy.sum(values))})
        rows.append(row)
    payload = {"labels": str(args.labels), "image": str(args.image) if args.image else None,
               "object_count": len(rows), "objects": rows}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, indent=2) + "\n")
    print(json.dumps({"object_count": len(rows), "output": str(args.output)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
