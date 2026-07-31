#!/usr/bin/env python3
"""Build numeric and visual QC reports from a PiHCA analysis root."""
from __future__ import annotations

import argparse
import html
import json
from pathlib import Path


VIEW_KEYS = (
    ("nuclear_raw", "nuclear_source_image"), ("cell_raw", "cell_source_image"),
    ("nuclei_raw_mask", "nuclei_raw_labels"), ("nuclei_filtered_mask", "nuclei_labels"),
    ("cell_raw_mask", "cell_raw_labels"), ("cell_filtered_mask", "cell_labels"),
    ("relationship", "relationship_overlay"),
)


def relative_artifact(path: Path, root: Path) -> str:
    return str(path.resolve().relative_to(root.resolve()))


def spread(items: list, count: int) -> list:
    if len(items) <= count:
        return items
    if count == 1:
        return [items[len(items) // 2]]
    return [items[round(index * (len(items) - 1) / (count - 1))] for index in range(count)]


def image_qc(path: Path) -> dict | None:
    try:
        import numpy
        import tifffile
        image = tifffile.imread(path).astype("float32")
        if image.ndim != 2:
            return None
        low, high = numpy.percentile(image, [1, 99.8])
        gy, gx = numpy.gradient(image)
        block_means = [float(block.mean()) for row in numpy.array_split(image, 4, axis=0)
                       for block in numpy.array_split(row, 4, axis=1)]
        mean = max(float(numpy.mean(block_means)), 1e-12)
        return {"path": str(path), "p01": float(low), "p998": float(high),
                "saturation_fraction": float(numpy.mean(image >= image.max())) if image.size else None,
                "focus_gradient_mean": float(numpy.mean(gx * gx + gy * gy)),
                "illumination_block_cv": float(numpy.std(block_means) / mean)}
    except (ImportError, OSError, ValueError):
        return None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--analysis-root", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--sample-fields", type=int, default=12)
    args = parser.parse_args()
    summary_records = [(path.parent.name, json.loads(path.read_text()))
                       for path in sorted(args.analysis_root.rglob("pipeline-summary.json"))]
    fields = [{"well": well, **field} for well, summary in summary_records
              for field in summary.get("fields", [])]
    relationship = [field.get("relationship", {}) for field in fields if field.get("relationship")]
    measurement_paths = [Path(path) for field in fields for path in field.get("measurements", {}).values()]
    object_counts, well_summary = {}, {}
    for field in fields:
        well = well_summary.setdefault(field["well"], {"fields": 0, "object_counts": {},
                                                        "relationship_qc_failed": 0})
        well["fields"] += 1
        well["relationship_qc_failed"] += field.get("relationship_qc") == "failed"
        for region, path in field.get("measurements", {}).items():
            artifact = Path(path)
            if artifact.is_file():
                count = json.loads(artifact.read_text())["object_count"]
                object_counts[region] = object_counts.get(region, 0) + count
                well["object_counts"][region] = well["object_counts"].get(region, 0) + count
    confluence = [json.loads(Path(field["confluence"]).read_text())["confluence_fraction"]
                  for field in fields if field.get("confluence") and Path(field["confluence"]).is_file()]
    puncta = [json.loads(Path(path).read_text())["object_count"] for field in fields
              for path in field.get("puncta", []) if Path(path).is_file()]
    class_counts = {}
    for field in fields:
        artifact = Path(field["classification"]) if field.get("classification") else None
        if artifact and artifact.is_file():
            for label, count in json.loads(artifact.read_text())["counts"].items():
                class_counts[label] = class_counts.get(label, 0) + count

    args.output_dir.mkdir(parents=True, exist_ok=True)
    figures, image_metrics = [], []
    for field_number, field in enumerate(spread(fields, max(1, args.sample_fields)), start=1):
        views = []
        for kind, key in VIEW_KEYS:
            source = Path(field[key]) if field.get(key) else None
            if source and source.is_file():
                views.append((kind, source))
        field_artifact = next(iter(field.get("measurements", {}).values()),
                              field.get("nuclei_labels") or field.get("cell_labels") or "/nonexistent/field")
        field_dir = Path(field_artifact).parent
        for kind, filename in (("nuclei_overlay", "nuclei-overlay.tif"),
                               ("cell_overlay", "cell-overlay.tif"),
                               ("relationship", "relationship-overlay.tif"),
                               ("confluence", "confluence-overlay.tif")):
            source = field_dir / filename
            if source.is_file() and all(source != path for _, path in views):
                views.append((kind, source))
        for view_number, (kind, source) in enumerate(views, start=1):
            destination = args.output_dir / "figures" / f"field-{field_number:03d}-{view_number:02d}-{kind}.png"
            try:
                from hca_review_ui import image_to_png
                image_to_png(source, destination)
                figures.append({"well": field["well"], "site": field.get("site"), "kind": kind,
                                "source": str(source), "path": str(destination.relative_to(args.output_dir))})
                if kind.endswith("raw"):
                    metric = image_qc(source)
                    if metric:
                        image_metrics.append({"well": field["well"], "site": field.get("site"),
                                              "kind": kind, **metric})
            except (OSError, ValueError, SystemExit):
                continue

    overlay_paths = sorted(args.analysis_root.rglob("*-overlay.tif"))
    payload = {"analysis_root": str(args.analysis_root.resolve()), "wells": len(summary_records),
               "fields": len(fields), "sampled_fields": len({(item["well"], item["site"]) for item in figures}),
               "relationship_qc_failed": sum(field.get("relationship_qc") == "failed" for field in fields),
               "nuclei": sum(item.get("nuclei", 0) for item in relationship),
               "cells": sum(item.get("cells", 0) for item in relationship),
               "assigned": sum(item.get("assigned", 0) for item in relationship),
               "orphan": sum(item.get("orphan", 0) for item in relationship),
               "ambiguous": sum(item.get("ambiguous", 0) for item in relationship),
               "object_counts": object_counts, "puncta": sum(puncta),
               "classification_counts": class_counts,
               "mean_confluence": sum(confluence) / len(confluence) if confluence else None,
               "well_summary": well_summary, "image_qc": image_metrics,
               "overlays": [relative_artifact(path, args.analysis_root) for path in overlay_paths],
               "figures": figures,
               "measurement_tables": [relative_artifact(path, args.analysis_root) for path in measurement_paths],
               "embeddings": [relative_artifact(path, args.analysis_root)
                              for path in sorted(args.analysis_root.rglob("embedding*.json"))]}
    (args.output_dir / "report.json").write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    rows = "".join(f"<tr><th>{html.escape(key.replace('_', ' ').title())}</th><td>{html.escape(str(value))}</td></tr>"
                   for key, value in payload.items() if not isinstance(value, (list, dict)))
    well_rows = "".join(f"<tr><th>{html.escape(well)}</th><td>{data['fields']}</td><td>{html.escape(json.dumps(data['object_counts'], sort_keys=True))}</td><td>{data['relationship_qc_failed']}</td></tr>"
                        for well, data in sorted(well_summary.items()))
    figure_groups = []
    for well in sorted({item["well"] for item in figures}):
        cards = "".join(f'<figure><img src="{html.escape(item["path"])}"><figcaption>{html.escape(str(item["site"]))}: {html.escape(item["kind"])}</figcaption></figure>'
                        for item in figures if item["well"] == well)
        figure_groups.append(f"<section><h3>{html.escape(well)}</h3><div class=figures>{cards}</div></section>")
    document = f"""<!doctype html><html><head><meta charset="utf-8"><title>PiHCA report</title><style>
body{{font:14px system-ui;margin:0;color:#172126;background:#f4f6f6}}main{{max-width:1280px;margin:auto;background:white;min-height:100vh;padding:24px}}h1{{font-size:24px}}table{{border-collapse:collapse;width:100%}}th,td{{text-align:left;border-bottom:1px solid #d8dee2;padding:8px}}.figures{{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:10px}}figure{{margin:0}}img{{width:100%;height:260px;object-fit:contain;background:#101719}}figcaption{{color:#53636b}}section{{border-top:1px solid #d8dee2;padding-top:12px}}@media(max-width:850px){{.figures{{grid-template-columns:1fr}}}}</style></head>
<body><main><h1>PiHCA analysis report</h1><h2>Plate summary</h2><table>{rows}</table>
<h2>Well summary</h2><table><tr><th>Well</th><th>Fields</th><th>Object counts</th><th>Relationship failures</th></tr>{well_rows}</table>
<h2>Stratified field review</h2>{''.join(figure_groups) or '<p>No review figures found.</p>'}</main></body></html>"""
    (args.output_dir / "report.html").write_text(document, encoding="utf-8")
    print(json.dumps({"report": str(args.output_dir / "report.json"),
                      "html": str(args.output_dir / "report.html"), "fields": len(fields)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
