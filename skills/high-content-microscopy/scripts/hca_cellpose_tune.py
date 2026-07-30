#!/usr/bin/env python3
"""Run a bounded, review-required Cellpose parameter sweep for one pilot image."""
from __future__ import annotations

import argparse
import itertools
import json
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


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--model", required=True)
    parser.add_argument("--nuclear-image", type=Path, help="Required when tuning a Cellpose cell-boundary model with nuclear guidance")
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
    except ValueError as error:
        parser.error(str(error))
    script_dir = Path(__file__).parent
    results = []
    for number, candidate in enumerate(grid, start=1):
        output = args.output_dir / f"candidate-{number:02d}" / "labels.tif"
        command = [sys.executable, str(script_dir / "hca_segment.py"), "--image", str(args.image), "--output", str(output),
                   "--engine", "cellpose", "--model", args.model, "--flow-threshold", str(candidate["flow_threshold"]),
                   "--cellprob-threshold", str(candidate["cellprob_threshold"])]
        if candidate["diameter"] is not None:
            command.extend(["--diameter", str(candidate["diameter"])])
        if args.nuclear_image:
            command.extend(["--nuclear-image", str(args.nuclear_image)])
        if args.gpu:
            command.append("--gpu")
        if args.no_normalize:
            command.append("--no-normalize")
        process = subprocess.run(command, capture_output=True, text=True)
        result = {"id": f"candidate-{number:02d}", "parameters": candidate, "labels": str(output), "returncode": process.returncode}
        if process.returncode == 0:
            measure = output.with_name("measurements.json")
            subprocess.run([sys.executable, str(script_dir / "hca_measure.py"), "--labels", str(output), "--image", str(args.image), "--output", str(measure)], check=True, capture_output=True, text=True)
            overlay = output.with_name("overlay.tif")
            subprocess.run([sys.executable, str(script_dir / "hca_overlay.py"), "--image", str(args.image), "--labels", str(output), "--output", str(overlay)], check=True, capture_output=True, text=True)
            result.update({"measurements": str(measure), "overlay": str(overlay), "object_count": json.loads(measure.read_text())["object_count"]})
        else:
            result["error"] = process.stderr[-4000:]
        results.append(result)
    payload = {"status": "review_required", "image": str(args.image), "model": args.model, "candidates": results,
               "review_instruction": "Compare overlays against the raw image and select a candidate by biological boundary quality, not object count alone. Copy the selected values into the stage.cellpose configuration and rerun a pilot before approval."}
    args.output_dir.mkdir(parents=True, exist_ok=True)
    (args.output_dir / "candidates.json").write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": payload["status"], "candidates": len(results), "successful": sum(item["returncode"] == 0 for item in results), "output": str(args.output_dir)}, indent=2))
    return 0 if all(item["returncode"] == 0 for item in results) else 2


if __name__ == "__main__":
    raise SystemExit(main())
