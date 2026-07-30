#!/usr/bin/env python3
"""Create a simple inspection overlay for a raw image and a label mask."""
from __future__ import annotations

import argparse
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", required=True, type=Path)
    parser.add_argument("--labels", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    try:
        import numpy as np
        import tifffile
    except ImportError as error:
        raise SystemExit("overlays require: pip install '.[qc]'") from error
    image, labels = tifffile.imread(args.image), tifffile.imread(args.labels)
    if image.shape != labels.shape or image.ndim != 2:
        raise SystemExit("image and labels must have equal two-dimensional shapes")
    scaled = ((image - image.min()) / max(float(image.max() - image.min()), 1.0) * 255).astype(np.uint8)
    overlay = np.stack([scaled, scaled, scaled], axis=-1)
    edge = (labels > 0) & ((np.roll(labels, 1, 0) != labels) | (np.roll(labels, 1, 1) != labels))
    overlay[edge] = [255, 0, 0]
    args.output.parent.mkdir(parents=True, exist_ok=True)
    tifffile.imwrite(args.output, overlay)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
