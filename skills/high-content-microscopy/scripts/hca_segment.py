#!/usr/bin/env python3
"""Run a selected segmentation engine under a common label-image contract."""
from __future__ import annotations

import argparse
import fcntl
import os
from pathlib import Path


def require(module: str, extra: str):
    try:
        return __import__(module, fromlist=["*"])
    except ImportError as error:
        raise SystemExit(f"Could not import {module}: {error}. Install or repair: pip install '.[{extra}]'") from error


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--engine", choices=["threshold", "cellpose", "stardist"], required=True)
    parser.add_argument("--model", default="nuclei")
    parser.add_argument("--diameter", type=float)
    parser.add_argument("--flow-threshold", type=float, help="Cellpose flow-error threshold")
    parser.add_argument("--cellprob-threshold", type=float, help="Cellpose cell-probability threshold")
    parser.add_argument("--min-size", type=int, help="Cellpose native minimum object area")
    parser.add_argument("--niter", type=int, help="Cellpose dynamics iterations")
    parser.add_argument("--tile-overlap", type=float, help="Cellpose tile overlap fraction")
    parser.add_argument("--augment", action="store_true", help="Enable Cellpose test-time augmentation")
    parser.add_argument("--no-normalize", action="store_true", help="Disable Cellpose image normalization")
    parser.add_argument("--nuclear-image", type=Path,
                        help="Nuclear channel used as the second Cellpose input for cell-boundary segmentation")
    parser.add_argument("--gpu", action="store_true", default=False, help="Use GPU for segmentation (cellpose/stardist)")
    parser.add_argument("--model-cache-lock", type=Path,
                        help="Lock file used to serialize first-time model downloads")
    args = parser.parse_args()
    tifffile = require("tifffile", "qc")
    numpy = require("numpy", "qc")
    image = tifffile.imread(args.image)
    if image.ndim != 2:
        raise SystemExit("segmentation expects one 2D channel image; make z/channel selection explicit upstream")
    if args.engine == "threshold":
        scipy = require("scipy.ndimage", "qc")
        threshold = numpy.percentile(image, 99)
        labels, _ = scipy.label(image > threshold)
    elif args.engine == "cellpose":
        models = require("cellpose.models", "cellpose")
        lock_path = args.model_cache_lock or Path(os.environ.get("HCA_MODEL_CACHE_LOCK", "/tmp/pi-hca-model-download.lock"))
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        with lock_path.open("w") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            import torch
            use_gpu = args.gpu and torch.cuda.is_available()
            model = models.CellposeModel(gpu=use_gpu, model_type=args.model)
        if args.nuclear_image:
            nuclear_image = tifffile.imread(args.nuclear_image)
            if nuclear_image.shape != image.shape:
                raise SystemExit("--nuclear-image must match --image dimensions")
            model_input, channels = numpy.stack([image, nuclear_image], axis=-1), [1, 2]
        else:
            model_input, channels = image, [0, 0]
        eval_options = {"diameter": args.diameter, "channels": channels, "normalize": not args.no_normalize,
                        "augment": args.augment}
        for argument, option in (("flow_threshold", "flow_threshold"), ("cellprob_threshold", "cellprob_threshold"),
                                 ("min_size", "min_size"), ("niter", "niter"), ("tile_overlap", "tile_overlap")):
            value = getattr(args, argument)
            if value is not None:
                eval_options[option] = value
        labels = model.eval([model_input], **eval_options)[0][0]
    else:
        models = require("stardist.models", "stardist")
        lock_path = args.model_cache_lock or Path(os.environ.get("HCA_MODEL_CACHE_LOCK", "/tmp/pi-hca-model-download.lock"))
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        with lock_path.open("w") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            model = models.StarDist2D.from_pretrained(args.model)
        labels, _ = model.predict_instances(image)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    tifffile.imwrite(args.output, labels.astype(numpy.uint32))
    print({"engine": args.engine, "image": str(args.image), "labels": int(labels.max()), "output": str(args.output),
           "cellpose_options": eval_options if args.engine == "cellpose" else None})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
