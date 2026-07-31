#!/usr/bin/env python3
"""Build compact numeric and HTML figure reports from a Pi HCA analysis root."""
from __future__ import annotations

import argparse
import html
import json
from pathlib import Path


def relative_artifact(path: Path, root: Path) -> str:
    return str(path.resolve().relative_to(root.resolve()))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--analysis-root", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    args = parser.parse_args()
    summaries = [json.loads(path.read_text()) for path in sorted(args.analysis_root.rglob("pipeline-summary.json"))]
    fields = [field for summary in summaries for field in summary.get("fields", [])]
    relationship = [field.get("relationship", {}) for field in fields if field.get("relationship")]
    measurement_paths = [Path(path) for field in fields for path in field.get("measurements", {}).values()]
    object_counts = {}
    for field in fields:
        for region, path in field.get("measurements", {}).items():
            artifact = Path(path)
            if artifact.is_file():
                object_counts[region] = object_counts.get(region, 0) + json.loads(artifact.read_text())["object_count"]
    confluence = [json.loads(Path(field["confluence"]).read_text())["confluence_fraction"]
                  for field in fields if field.get("confluence") and Path(field["confluence"]).is_file()]
    puncta = [json.loads(Path(path).read_text())["object_count"] for field in fields for path in field.get("puncta", [])
              if Path(path).is_file()]
    class_counts = {}
    for field in fields:
        artifact = Path(field["classification"]) if field.get("classification") else None
        if artifact and artifact.is_file():
            for label, count in json.loads(artifact.read_text())["counts"].items():
                class_counts[label] = class_counts.get(label, 0) + count
    overlay_paths = sorted(args.analysis_root.rglob("*-overlay.tif"))
    figures = []
    for number, path in enumerate(overlay_paths[:48], start=1):
        destination = args.output_dir / "figures" / f"overlay-{number:03d}.png"
        try:
            from hca_review_ui import image_to_png
            image_to_png(path, destination)
            figures.append(str(destination.relative_to(args.output_dir)))
        except (OSError, ValueError, SystemExit):
            break
    payload = {"analysis_root": str(args.analysis_root.resolve()), "wells": len(summaries), "fields": len(fields),
               "relationship_qc_failed": sum(field.get("relationship_qc") == "failed" for field in fields),
               "nuclei": sum(item.get("nuclei", 0) for item in relationship),
               "cells": sum(item.get("cells", 0) for item in relationship),
               "assigned": sum(item.get("assigned", 0) for item in relationship),
               "orphan": sum(item.get("orphan", 0) for item in relationship),
               "object_counts": object_counts, "puncta": sum(puncta),
               "classification_counts": class_counts,
               "mean_confluence": sum(confluence) / len(confluence) if confluence else None,
               "overlays": [relative_artifact(path, args.analysis_root) for path in overlay_paths], "figures": figures,
               "measurement_tables": [relative_artifact(path, args.analysis_root) for path in measurement_paths],
               "embeddings": [relative_artifact(path, args.analysis_root)
                              for path in sorted(args.analysis_root.rglob("embedding*.json"))]}
    args.output_dir.mkdir(parents=True, exist_ok=True)
    (args.output_dir / "report.json").write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    rows = "".join(f"<tr><th>{html.escape(key.replace('_', ' ').title())}</th><td>{html.escape(str(value))}</td></tr>" for key, value in payload.items() if not isinstance(value, list))
    links = "".join(f'<figure><img src="{html.escape(path)}"><figcaption>{html.escape(path)}</figcaption></figure>' for path in figures)
    if not links:
        links = "".join(f'<li>{html.escape(path)}</li>' for path in payload["overlays"][:48]) or '<li>No overlays found</li>'
    document = f"""<!doctype html><html><head><meta charset="utf-8"><title>PiHCA report</title><style>
body{{font:14px system-ui;margin:24px;color:#172126;max-width:1000px}}h1{{font-size:24px}}table{{border-collapse:collapse;width:100%;max-width:640px}}th,td{{text-align:left;border-bottom:1px solid #d8dee2;padding:8px}}th{{width:52%;color:#45545c}}figure{{margin:16px 0}}img{{max-width:100%;background:#101719}}figcaption{{color:#53636b}}</style></head>
<body><h1>PiHCA analysis report</h1><table>{rows}</table><h2>Review overlays</h2>{links}</body></html>"""
    (args.output_dir / "report.html").write_text(document, encoding="utf-8")
    print(json.dumps({"report": str(args.output_dir / "report.json"), "html": str(args.output_dir / "report.html"), "fields": len(fields)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
