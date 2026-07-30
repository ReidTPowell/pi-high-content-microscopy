#!/usr/bin/env python3
"""Align nuclear and cell labels, derive cytoplasm, and report relationship QC."""
from __future__ import annotations

import argparse
import json
from pathlib import Path


def require(module: str):
    try:
        return __import__(module, fromlist=["*"])
    except ImportError as error:
        raise SystemExit("relational segmentation requires: pip install '.[qc]'") from error


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--nuclei", required=True, type=Path, help="Independent nuclear label TIFF")
    parser.add_argument("--cells", required=True, type=Path, help="Independent cell label TIFF")
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--min-overlap", type=float, default=0.5,
                        help="Minimum fraction of nuclear pixels belonging to the assigned cell")
    args = parser.parse_args()
    tifffile, numpy = require("tifffile"), require("numpy")
    nuclei, cells = tifffile.imread(args.nuclei), tifffile.imread(args.cells)
    if nuclei.shape != cells.shape or nuclei.ndim != 2:
        raise SystemExit("nuclei and cell labels must have equal two-dimensional shapes")
    cytoplasm = cells.astype(numpy.uint32).copy()
    relationships, assigned_nuclei = [], numpy.zeros_like(nuclei, dtype=numpy.uint32)
    for nucleus_id in range(1, int(nuclei.max()) + 1):
        nucleus_mask = nuclei == nucleus_id
        overlapping = cells[nucleus_mask]
        candidates, counts = numpy.unique(overlapping[overlapping > 0], return_counts=True)
        if not len(candidates):
            relationships.append({"nucleus_id": nucleus_id, "cell_id": None, "overlap_fraction": 0.0, "status": "orphan"})
            continue
        winner_index = int(numpy.argmax(counts))
        cell_id, overlap = int(candidates[winner_index]), int(counts[winner_index])
        fraction = overlap / int(nucleus_mask.sum())
        tied = int((counts == counts[winner_index]).sum()) > 1
        status = "ambiguous" if tied else ("assigned" if fraction >= args.min_overlap else "low_overlap")
        relationships.append({"nucleus_id": nucleus_id, "cell_id": cell_id, "overlap_fraction": fraction, "status": status})
        if status == "assigned":
            assigned_nuclei[nucleus_mask] = cell_id
            cytoplasm[nucleus_mask & (cells == cell_id)] = 0
    cell_counts = {
        str(cell_id): sum(item["cell_id"] == cell_id and item["status"] == "assigned" for item in relationships)
        for cell_id in range(1, int(cells.max()) + 1)
    }
    output = args.output_dir
    output.mkdir(parents=True, exist_ok=True)
    tifffile.imwrite(output / "assigned-nuclei-by-cell.tif", assigned_nuclei)
    tifffile.imwrite(output / "cytoplasm-by-cell.tif", cytoplasm)
    summary = {"nuclei": int(nuclei.max()), "cells": int(cells.max()), "relationships": relationships,
               "assigned": sum(item["status"] == "assigned" for item in relationships),
               "orphan": sum(item["status"] == "orphan" for item in relationships),
               "ambiguous": sum(item["status"] == "ambiguous" for item in relationships),
               "cell_has_assigned_nucleus": cell_counts}
    (output / "relationships.json").write_text(json.dumps(summary, indent=2) + "\n")
    print(json.dumps({key: summary[key] for key in ("nuclei", "cells", "assigned", "orphan", "ambiguous")}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
