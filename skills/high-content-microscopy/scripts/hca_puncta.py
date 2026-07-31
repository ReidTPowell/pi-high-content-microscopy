#!/usr/bin/env python3
"""Detect bright puncta and optionally assign each punctum to a parent label."""
from __future__ import annotations

import argparse
import json
from pathlib import Path


def require(module: str):
    try:
        return __import__(module, fromlist=["*"])
    except ImportError as error:
        raise SystemExit("puncta detection requires: pip install '.[qc]'") from error


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", required=True, type=Path)
    parser.add_argument("--output-labels", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--parent-labels", type=Path)
    parser.add_argument("--sigma", type=float, default=1.0)
    parser.add_argument("--background-sigma", type=float, default=4.0)
    parser.add_argument("--threshold-percentile", type=float, default=99.5)
    parser.add_argument("--min-area-px", type=int, default=2)
    parser.add_argument("--max-area-px", type=int)
    args = parser.parse_args()
    if args.sigma <= 0 or args.background_sigma <= args.sigma:
        parser.error("background sigma must be greater than positive spot sigma")
    if not 0 < args.threshold_percentile < 100 or args.min_area_px < 1:
        parser.error("threshold percentile must be 0..100 and minimum area must be positive")
    tifffile, numpy, scipy = require("tifffile"), require("numpy"), require("scipy.ndimage")
    image = tifffile.imread(args.image).astype(numpy.float32)
    foreground = scipy.gaussian_filter(image, args.sigma) - scipy.gaussian_filter(image, args.background_sigma)
    threshold = float(numpy.percentile(foreground, args.threshold_percentile))
    labels, count = scipy.label(foreground > threshold)
    accepted = numpy.zeros_like(labels, dtype=numpy.uint32)
    parent = tifffile.imread(args.parent_labels) if args.parent_labels else None
    if parent is not None and parent.shape != labels.shape:
        parser.error("parent labels must match puncta image dimensions")
    objects = []
    next_label = 1
    for label in range(1, count + 1):
        mask = labels == label
        area = int(mask.sum())
        if area < args.min_area_px or (args.max_area_px is not None and area > args.max_area_px):
            continue
        accepted[mask] = next_label
        values = image[mask]
        coordinates = numpy.argwhere(mask)
        parent_id = None
        if parent is not None:
            candidates = parent[mask]
            candidates = candidates[candidates > 0]
            if candidates.size:
                ids, counts = numpy.unique(candidates, return_counts=True)
                parent_id = int(ids[numpy.argmax(counts)])
        objects.append({"object_id": next_label, "parent_id": parent_id, "area_px": area,
                        "centroid_y": float(coordinates[:, 0].mean()), "centroid_x": float(coordinates[:, 1].mean()),
                        "intensity_mean": float(values.mean()), "intensity_sum": float(values.sum())})
        next_label += 1
    args.output_labels.parent.mkdir(parents=True, exist_ok=True)
    tifffile.imwrite(args.output_labels, accepted)
    payload = {"schema_version": 1, "image": str(args.image), "labels": str(args.output_labels),
               "parent_labels": str(args.parent_labels) if args.parent_labels else None,
               "threshold": threshold, "threshold_percentile": args.threshold_percentile,
               "object_count": len(objects), "assigned": sum(item["parent_id"] is not None for item in objects),
               "objects": objects}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, indent=2) + "\n")
    print(json.dumps({"object_count": len(objects), "assigned": payload["assigned"], "output": str(args.output)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
