#!/usr/bin/env python3
"""Render same-ID cell and nuclear boundaries for relational segmentation QC."""
from __future__ import annotations

import argparse
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", required=True, type=Path)
    parser.add_argument("--nuclei", required=True, type=Path)
    parser.add_argument("--assigned-nuclei", required=True, type=Path)
    parser.add_argument("--cells", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    try:
        import numpy as np
        import tifffile
    except ImportError as error:
        raise SystemExit("relational overlays require: pip install '.[qc]'") from error
    image = tifffile.imread(args.image)
    nuclei = tifffile.imread(args.nuclei)
    assigned = tifffile.imread(args.assigned_nuclei)
    cells = tifffile.imread(args.cells)
    if any(array.shape != image.shape for array in (nuclei, assigned, cells)) or image.ndim != 2:
        raise SystemExit("image and relationship labels must have equal two-dimensional shapes")
    low, high = np.percentile(image, [1, 99.8])
    scaled = (np.clip((image.astype("float32") - low) / max(float(high - low), 1.0), 0, 1) * 180).astype("uint8")
    overlay = np.stack([scaled, scaled, scaled], axis=-1)
    palette = np.array([[230, 85, 60], [43, 166, 95], [44, 127, 184], [204, 151, 38],
                        [145, 92, 182], [42, 174, 177], [218, 95, 155]], dtype="uint8")

    def edge(labels):
        return ((labels > 0) & ((np.roll(labels, 1, 0) != labels)
                               | (np.roll(labels, 1, 1) != labels)))

    cell_edge = edge(cells)
    assigned_edge = edge(assigned)
    overlay[cell_edge] = palette[(cells[cell_edge] - 1) % len(palette)]
    overlay[assigned_edge] = palette[(assigned[assigned_edge] - 1) % len(palette)]
    orphan_edge = edge(nuclei) & (assigned == 0)
    overlay[orphan_edge] = [255, 255, 255]
    args.output.parent.mkdir(parents=True, exist_ok=True)
    tifffile.imwrite(args.output, overlay)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
