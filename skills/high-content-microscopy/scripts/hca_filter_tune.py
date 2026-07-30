#!/usr/bin/env python3
"""Generate review-required size and intensity filter candidates from a pilot filter audit."""
from __future__ import annotations

import argparse
import json
from pathlib import Path


def require(module: str):
    try:
        return __import__(module, fromlist=["*"])
    except ImportError as error:
        raise SystemExit("filter tuning requires: pip install '.[qc]'") from error


def quantile(values, fraction: float):
    numpy = require("numpy")
    return float(numpy.quantile(values, fraction)) if values else None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--audit", required=True, type=Path, help="nuclei-filter.json or cell-filter.json from a pilot")
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--lower-quantile", type=float, default=0.02, help="Candidate lower bound quantile")
    parser.add_argument("--upper-quantile", type=float, default=0.998, help="Candidate upper bound quantile")
    args = parser.parse_args()
    if not 0 <= args.lower_quantile < args.upper_quantile <= 1:
        parser.error("quantiles must satisfy 0 <= lower < upper <= 1")
    audit = json.loads(args.audit.read_text(encoding="utf-8"))
    objects = audit.get("objects", [])
    areas = [item["area_px"] for item in objects]
    intensities = [item["intensity_mean"] for item in objects if item.get("intensity_mean") is not None]
    candidate = {
        "min_area_px": int(quantile(areas, args.lower_quantile)) if areas else None,
        "max_area_px": int(quantile(areas, args.upper_quantile)) if areas else None,
        "min_intensity_mean": quantile(intensities, args.lower_quantile),
        "max_intensity_mean": quantile(intensities, args.upper_quantile),
    }
    def rejected(item):
        return ((candidate["min_area_px"] is not None and item["area_px"] < candidate["min_area_px"]) or
                (candidate["max_area_px"] is not None and item["area_px"] > candidate["max_area_px"]) or
                (item.get("intensity_mean") is not None and candidate["min_intensity_mean"] is not None and item["intensity_mean"] < candidate["min_intensity_mean"]) or
                (item.get("intensity_mean") is not None and candidate["max_intensity_mean"] is not None and item["intensity_mean"] > candidate["max_intensity_mean"]))
    proposed_rejections = [item for item in objects if rejected(item)]
    payload = {
        "status": "review_required", "audit": str(args.audit), "object_count": len(objects),
        "quantiles": {"lower": args.lower_quantile, "upper": args.upper_quantile}, "suggested_filter": candidate,
        "predicted_removed_object_count": len(proposed_rejections),
        "predicted_removed_source_labels": [item["source_label"] for item in proposed_rejections],
        "review_instruction": "Inspect the listed source labels and centroids in the pilot overlays. Copy only approved limits into the versioned assay config; do not use this file as an automatic batch approval.",
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": payload["status"], "suggested_filter": candidate, "predicted_removed_object_count": len(proposed_rejections), "output": str(args.output)}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
