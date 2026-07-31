#!/usr/bin/env python3
"""Measure foreground coverage without forcing individual-object segmentation."""
from __future__ import annotations

import argparse
import json
from pathlib import Path


def otsu(image, numpy) -> float:
    counts, edges = numpy.histogram(image.ravel(), bins=256)
    centers = (edges[:-1] + edges[1:]) / 2
    weights_left = numpy.cumsum(counts)
    weights_right = counts.sum() - weights_left
    means_left = numpy.cumsum(counts * centers) / numpy.maximum(weights_left, 1)
    means_right = (numpy.sum(counts * centers) - numpy.cumsum(counts * centers)) / numpy.maximum(weights_right, 1)
    between = weights_left * weights_right * (means_left - means_right) ** 2
    return float(centers[int(numpy.argmax(between[:-1]))])


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", required=True, type=Path)
    parser.add_argument("--output-mask", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--threshold", type=float)
    parser.add_argument("--polarity", choices=("bright", "dark"), default="bright")
    parser.add_argument("--smoothing-sigma", type=float, default=1.0)
    parser.add_argument("--minimum-component-px", type=int, default=25)
    args = parser.parse_args()
    try:
        import numpy
        import tifffile
        from scipy import ndimage
    except ImportError as error:
        raise SystemExit("confluence analysis requires: pip install '.[qc]'") from error
    image = tifffile.imread(args.image).astype(numpy.float32)
    smoothed = ndimage.gaussian_filter(image, args.smoothing_sigma)
    threshold = args.threshold if args.threshold is not None else otsu(smoothed, numpy)
    if float(smoothed.max()) == float(smoothed.min()):
        mask = numpy.zeros_like(smoothed, dtype=bool)
    else:
        mask = smoothed >= threshold if args.polarity == "bright" else smoothed <= threshold
    labels, count = ndimage.label(mask)
    sizes = numpy.bincount(labels.ravel())
    keep = numpy.flatnonzero(sizes >= args.minimum_component_px)
    keep = keep[keep != 0]
    mask = numpy.isin(labels, keep)
    args.output_mask.parent.mkdir(parents=True, exist_ok=True)
    tifffile.imwrite(args.output_mask, mask.astype(numpy.uint8))
    payload = {"schema_version": 1, "image": str(args.image), "mask": str(args.output_mask),
               "threshold": float(threshold), "polarity": args.polarity,
               "foreground_pixels": int(mask.sum()), "total_pixels": int(mask.size),
               "confluence_fraction": float(mask.mean()), "components": int(len(keep))}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, indent=2) + "\n")
    print(json.dumps({"confluence_fraction": payload["confluence_fraction"], "output": str(args.output)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
