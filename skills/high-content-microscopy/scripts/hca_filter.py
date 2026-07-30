#!/usr/bin/env python3
"""Filter label objects by reviewed size and intensity criteria while retaining an audit trail."""
from __future__ import annotations

import argparse
import json
from pathlib import Path


def require(module: str):
    try:
        return __import__(module, fromlist=["*"])
    except ImportError as error:
        raise SystemExit("filtering requires: pip install '.[qc]'") from error


def filter_labels(labels, image, criteria: dict):
    """Return relabeled accepted objects and every acceptance/rejection decision."""
    numpy = require("numpy")
    filtered = numpy.zeros_like(labels)
    decisions, next_label = [], 1
    for label in (int(value) for value in numpy.unique(labels) if value > 0):
        mask = labels == label
        area = int(mask.sum())
        intensity_mean = float(numpy.mean(image[mask])) if image is not None else None
        coordinates = numpy.argwhere(mask)
        centroid_yx = [float(value) for value in numpy.mean(coordinates, axis=0)]
        reasons = []
        if criteria.get("min_area_px") is not None and area < criteria["min_area_px"]:
            reasons.append("area_below_minimum")
        if criteria.get("max_area_px") is not None and area > criteria["max_area_px"]:
            reasons.append("area_above_maximum")
        if intensity_mean is not None and criteria.get("min_intensity_mean") is not None and intensity_mean < criteria["min_intensity_mean"]:
            reasons.append("intensity_below_minimum")
        if intensity_mean is not None and criteria.get("max_intensity_mean") is not None and intensity_mean > criteria["max_intensity_mean"]:
            reasons.append("intensity_above_maximum")
        accepted = not reasons
        decisions.append({"source_label": label, "output_label": next_label if accepted else None, "accepted": accepted,
                          "reasons": reasons, "area_px": area, "intensity_mean": intensity_mean})
        decisions[-1]["centroid_yx"] = centroid_yx
        if accepted:
            filtered[mask] = next_label
            next_label += 1
    return filtered, decisions


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--labels", required=True, type=Path, help="Raw label TIFF to filter")
    parser.add_argument("--image", type=Path, help="Matching image used for intensity criteria")
    parser.add_argument("--output", required=True, type=Path, help="Filtered, relabeled TIFF")
    parser.add_argument("--audit", required=True, type=Path, help="Object-level filtering decisions JSON")
    parser.add_argument("--min-area-px", type=int)
    parser.add_argument("--max-area-px", type=int)
    parser.add_argument("--min-intensity-mean", type=float)
    parser.add_argument("--max-intensity-mean", type=float)
    args = parser.parse_args()
    if args.min_area_px is not None and args.max_area_px is not None and args.min_area_px > args.max_area_px:
        parser.error("--min-area-px cannot exceed --max-area-px")
    if args.min_intensity_mean is not None and args.max_intensity_mean is not None and args.min_intensity_mean > args.max_intensity_mean:
        parser.error("--min-intensity-mean cannot exceed --max-intensity-mean")
    tifffile = require("tifffile")
    labels = tifffile.imread(args.labels)
    image = tifffile.imread(args.image) if args.image else None
    if labels.ndim != 2 or (image is not None and image.shape != labels.shape):
        raise SystemExit("labels and optional image must be matching two-dimensional arrays")
    criteria = {key: getattr(args, key) for key in ("min_area_px", "max_area_px", "min_intensity_mean", "max_intensity_mean")}
    filtered, decisions = filter_labels(labels, image, criteria)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.audit.parent.mkdir(parents=True, exist_ok=True)
    tifffile.imwrite(args.output, filtered.astype("uint32"))
    payload = {"labels": str(args.labels), "image": str(args.image) if args.image else None, "criteria": criteria,
               "input_object_count": len(decisions), "output_object_count": int(filtered.max()),
               "removed_object_count": sum(not item["accepted"] for item in decisions), "objects": decisions}
    args.audit.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"input_object_count": payload["input_object_count"], "output_object_count": payload["output_object_count"],
                      "removed_object_count": payload["removed_object_count"], "audit": str(args.audit)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
