#!/usr/bin/env python3
"""Measure labeled objects across one or more channels under an explicit metric pack."""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path


SUPPORTED_METRICS = {"count", "area", "shape", "intensity", "texture"}


def require(module: str):
    try:
        return __import__(module, fromlist=["*"])
    except ImportError as error:
        raise SystemExit("measurements require: pip install '.[qc]'") from error


def parse_images(values: list[str] | None) -> dict[str, Path]:
    images = {}
    for index, value in enumerate(values or []):
        if "=" in value:
            name, path = value.split("=", 1)
        else:
            name, path = ("intensity" if index == 0 else f"intensity_{index + 1}"), value
        if not name.strip() or name in images:
            raise ValueError(f"invalid or duplicate image name: {name!r}")
        images[name.strip()] = Path(path)
    return images


def shape_features(mask, numpy, scipy_ndimage, scipy_spatial) -> dict:
    coordinates = numpy.argwhere(mask)
    y_min, x_min = coordinates.min(axis=0)
    y_max, x_max = coordinates.max(axis=0)
    height, width = int(y_max - y_min + 1), int(x_max - x_min + 1)
    eroded = scipy_ndimage.binary_erosion(mask)
    perimeter = int(numpy.count_nonzero(mask & ~eroded))
    area = int(mask.sum())
    covariance = numpy.cov(coordinates, rowvar=False) if len(coordinates) > 2 else numpy.zeros((2, 2))
    eigenvalues = numpy.sort(numpy.maximum(numpy.linalg.eigvalsh(covariance), 0))[::-1]
    eccentricity = math.sqrt(max(0.0, 1.0 - eigenvalues[1] / eigenvalues[0])) if eigenvalues[0] > 0 else 0.0
    convex_area = float(area)
    if len(coordinates) >= 3:
        try:
            xy = coordinates[:, ::-1].astype(float)
            corners = numpy.concatenate([xy + offset for offset in ((-0.5, -0.5), (-0.5, 0.5),
                                                                     (0.5, -0.5), (0.5, 0.5))])
            convex_area = max(float(scipy_spatial.ConvexHull(corners).volume), float(area))
        except scipy_spatial.QhullError:
            pass
    return {
        "centroid_y": float(coordinates[:, 0].mean()), "centroid_x": float(coordinates[:, 1].mean()),
        "bbox_height_px": height, "bbox_width_px": width,
        "perimeter_px": perimeter,
        "circularity": float(4 * math.pi * area / max(perimeter * perimeter, 1)),
        "eccentricity": float(eccentricity),
        "aspect_ratio": float(max(height, width) / max(min(height, width), 1)),
        "extent": float(area / (height * width)),
        "solidity": float(area / convex_area),
    }


def intensity_features(values, numpy, include_texture: bool) -> dict:
    result = {
        "mean": float(numpy.mean(values)), "median": float(numpy.median(values)),
        "sum": float(numpy.sum(values)), "min": float(numpy.min(values)), "max": float(numpy.max(values)),
        "p10": float(numpy.percentile(values, 10)), "p90": float(numpy.percentile(values, 90)),
    }
    if include_texture:
        standard_deviation = float(numpy.std(values))
        histogram, _ = numpy.histogram(values, bins=32)
        probabilities = histogram[histogram > 0] / max(histogram.sum(), 1)
        result.update({"std": standard_deviation,
                       "cv": float(standard_deviation / max(abs(result["mean"]), 1e-12)),
                       "entropy": float(-(probabilities * numpy.log2(probabilities)).sum())})
    return result


def measure(labels, images: dict, metrics: set[str], numpy, scipy_ndimage, scipy_spatial) -> dict:
    rows = []
    for label in (int(value) for value in numpy.unique(labels) if value > 0):
        mask = labels == label
        row = {"object_id": label}
        if "area" in metrics or "shape" in metrics:
            row["area_px"] = int(mask.sum())
        if "shape" in metrics:
            row.update(shape_features(mask, numpy, scipy_ndimage, scipy_spatial))
        if "intensity" in metrics or "texture" in metrics:
            row["channels"] = {name: intensity_features(image[mask], numpy, "texture" in metrics)
                               for name, image in images.items()}
            if len(images) == 1:
                only = next(iter(row["channels"].values()))
                row.update({"intensity_mean": only["mean"], "intensity_sum": only["sum"]})
        rows.append(row)
    return {"object_count": len(rows), "objects": rows}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--labels", required=True, type=Path)
    parser.add_argument("--image", action="append",
                        help="Repeatable image path or role=/path/to/image.tif")
    parser.add_argument("--metrics", default="count,area,intensity",
                        help="Comma-separated count,area,shape,intensity,texture")
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    tifffile, numpy = require("tifffile"), require("numpy")
    scipy_ndimage, scipy_spatial = require("scipy.ndimage"), require("scipy.spatial")
    labels = tifffile.imread(args.labels)
    if labels.ndim != 2:
        raise SystemExit("labels must be a two-dimensional label image")
    try:
        image_paths = parse_images(args.image)
    except ValueError as error:
        parser.error(str(error))
    images = {name: tifffile.imread(path) for name, path in image_paths.items()}
    if any(image.shape != labels.shape for image in images.values()):
        parser.error("every measurement image must match the label image dimensions")
    metrics = {value.strip() for value in args.metrics.split(",") if value.strip()}
    if unknown := sorted(metrics - SUPPORTED_METRICS):
        parser.error("unsupported metrics: " + ", ".join(unknown))
    if metrics.intersection({"intensity", "texture"}) and not images:
        parser.error("intensity and texture metrics require at least one --image")
    payload = {"schema_version": 2, "labels": str(args.labels),
               "images": {name: str(path) for name, path in image_paths.items()},
               "metrics": sorted(metrics), **measure(labels, images, metrics, numpy, scipy_ndimage, scipy_spatial)}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, indent=2) + "\n")
    print(json.dumps({"object_count": payload["object_count"], "output": str(args.output)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
