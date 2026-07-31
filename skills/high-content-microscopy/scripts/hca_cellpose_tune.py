#!/usr/bin/env python3
"""Run a bounded, review-required Cellpose parameter sweep for one pilot image."""
from __future__ import annotations

import argparse
import fcntl
import itertools
import json
import os
import subprocess
import sys
from pathlib import Path


def values(raw: str, allow_auto: bool = False) -> list[float | None]:
    result = []
    for value in raw.split(","):
        value = value.strip().lower()
        if allow_auto and value == "auto":
            result.append(None)
        else:
            result.append(float(value))
    return result


def candidates(diameters, flows, cellprobs, limit: int) -> list[dict]:
    grid = [{"diameter": diameter, "flow_threshold": flow, "cellprob_threshold": cellprob}
            for diameter, flow, cellprob in itertools.product(diameters, flows, cellprobs)]
    if len(grid) > limit:
        raise ValueError(f"parameter grid has {len(grid)} candidates; limit is {limit}")
    return grid


def load_cellpose(args):
    try:
        import numpy
        import tifffile
        import torch
        from cellpose import models
    except ImportError as error:
        raise RuntimeError(f"Cellpose runtime is incomplete: {error}") from error
    image = tifffile.imread(args.image)
    if image.ndim != 2:
        raise RuntimeError("Cellpose tuning expects one 2D channel image")
    if args.nuclear_image:
        nuclear = tifffile.imread(args.nuclear_image)
        if nuclear.shape != image.shape:
            raise RuntimeError("--nuclear-image must match --image dimensions")
        model_input, channels = numpy.stack([image, nuclear], axis=-1), [1, 2]
    else:
        model_input, channels = image, [0, 0]
    lock_path = Path(os.environ.get("HCA_MODEL_CACHE_LOCK", "/tmp/pi-hca-model-download.lock"))
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        model = models.CellposeModel(gpu=args.gpu and torch.cuda.is_available(), model_type=args.model)
    return model, model_input, channels, tifffile, numpy


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--model", required=True)
    parser.add_argument("--nuclear-image", type=Path, help="Required when tuning a Cellpose cell-boundary model with nuclear guidance")
    parser.add_argument("--reference-nuclei", type=Path,
                        help="Reviewed nuclear labels used to score nucleus-to-cell relationships for secondary candidates")
    parser.add_argument("--min-overlap", type=float, default=0.5)
    parser.add_argument("--diameters", default="auto")
    parser.add_argument("--flow-thresholds", default="0.4")
    parser.add_argument("--cellprob-thresholds", default="0.0")
    parser.add_argument("--gpu", action="store_true")
    parser.add_argument("--no-normalize", action="store_true")
    parser.add_argument("--max-candidates", type=int, default=12)
    args = parser.parse_args()
    if args.output_dir.exists() and any(args.output_dir.iterdir()):
        parser.error("output directory is not empty; choose a new pilot directory")
    try:
        grid = candidates(values(args.diameters, True), values(args.flow_thresholds), values(args.cellprob_thresholds), args.max_candidates)
        model, model_input, channels, tifffile, numpy = load_cellpose(args)
    except ValueError as error:
        parser.error(str(error))
    except RuntimeError as error:
        parser.error(str(error))
    script_dir = Path(__file__).parent
    results = []
    for number, candidate in enumerate(grid, start=1):
        output = args.output_dir / f"candidate-{number:02d}" / "labels.tif"
        result = {"id": f"candidate-{number:02d}", "parameters": candidate, "labels": str(output), "returncode": 0}
        try:
            options = {"diameter": candidate["diameter"], "channels": channels, "normalize": not args.no_normalize,
                       "flow_threshold": candidate["flow_threshold"], "cellprob_threshold": candidate["cellprob_threshold"]}
            labels = model.eval([model_input], **options)[0][0]
            output.parent.mkdir(parents=True, exist_ok=True)
            tifffile.imwrite(output, labels.astype(numpy.uint32))
            measure = output.with_name("measurements.json")
            subprocess.run([sys.executable, str(script_dir / "hca_measure.py"), "--labels", str(output), "--image", str(args.image), "--output", str(measure)], check=True, capture_output=True, text=True)
            overlay = output.with_name("overlay.tif")
            subprocess.run([sys.executable, str(script_dir / "hca_overlay.py"), "--image", str(args.image), "--labels", str(output), "--output", str(overlay)], check=True, capture_output=True, text=True)
            result.update({"measurements": str(measure), "overlay": str(overlay), "object_count": json.loads(measure.read_text())["object_count"]})
            if args.reference_nuclei:
                relationship_dir = output.with_name("relationship")
                relation_process = subprocess.run(
                    [sys.executable, str(script_dir / "hca_relate.py"), "--nuclei", str(args.reference_nuclei),
                     "--cells", str(output), "--output-dir", str(relationship_dir), "--min-overlap", str(args.min_overlap)],
                    capture_output=True, text=True,
                )
                if relation_process.returncode:
                    result.update({"returncode": relation_process.returncode, "error": relation_process.stderr[-4000:]})
                else:
                    relationship = json.loads((relationship_dir / "relationships.json").read_text())
                    result["relationship"] = {key: relationship[key] for key in ("nuclei", "cells", "assigned", "orphan", "ambiguous")}
        except Exception as error:
            result.update({"returncode": 2, "error": str(error)[-4000:]})
        results.append(result)
    payload = {"status": "review_required", "image": str(args.image), "model": args.model,
               "reference_nuclei": str(args.reference_nuclei) if args.reference_nuclei else None, "candidates": results,
               "review_instruction": "Compare overlays against the raw image and select a candidate by biological boundary quality, not object count alone. Copy the selected values into the stage.cellpose configuration and rerun a pilot before approval."}
    args.output_dir.mkdir(parents=True, exist_ok=True)
    (args.output_dir / "candidates.json").write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": payload["status"], "candidates": len(results), "successful": sum(item["returncode"] == 0 for item in results), "output": str(args.output_dir)}, indent=2))
    return 0 if all(item["returncode"] == 0 for item in results) else 2


if __name__ == "__main__":
    raise SystemExit(main())
