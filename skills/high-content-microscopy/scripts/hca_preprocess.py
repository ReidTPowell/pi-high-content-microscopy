#!/usr/bin/env python3
"""Subtract a recorded image background while preserving source images and pixel type."""
from __future__ import annotations

import argparse
import json
from pathlib import Path


def require(module: str):
    try:
        return __import__(module, fromlist=["*"])
    except ImportError as error:
        raise SystemExit("preprocessing requires: pip install '.[qc]'") from error


def subtract_background(image, method: str, percentile: float, radius: int):
    numpy = require("numpy")
    if method == "percentile":
        background = numpy.percentile(image, percentile)
    else:
        scipy = require("scipy.ndimage")
        size = max(3, radius * 2 + 1)
        background = scipy.grey_opening(image, size=(size, size))
    corrected = numpy.asarray(image, dtype=numpy.float32) - background
    corrected = numpy.clip(corrected, 0, numpy.iinfo(image.dtype).max if numpy.issubdtype(image.dtype, numpy.integer) else None)
    return corrected.astype(image.dtype), background


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--report", required=True, type=Path)
    parser.add_argument("--method", choices=["percentile", "opening"], default="percentile")
    parser.add_argument("--percentile", type=float, default=5.0)
    parser.add_argument("--radius", type=int, default=25)
    args = parser.parse_args()
    if not 0 <= args.percentile <= 100 or args.radius < 1:
        parser.error("percentile must be 0..100 and radius must be positive")
    tifffile, numpy = require("tifffile"), require("numpy")
    image = tifffile.imread(args.image)
    if image.ndim != 2:
        raise SystemExit("background subtraction expects one selected two-dimensional plane")
    corrected, background = subtract_background(image, args.method, args.percentile, args.radius)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    tifffile.imwrite(args.output, corrected)
    payload = {"source": str(args.image), "output": str(args.output), "method": args.method,
               "percentile": args.percentile if args.method == "percentile" else None,
               "radius": args.radius if args.method == "opening" else None,
               "background_summary": float(background) if numpy.ndim(background) == 0 else {
                   "min": float(numpy.min(background)), "median": float(numpy.median(background)), "max": float(numpy.max(background))},
               "source_min": float(numpy.min(image)), "source_max": float(numpy.max(image)),
               "corrected_min": float(numpy.min(corrected)), "corrected_max": float(numpy.max(corrected))}
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(payload))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
