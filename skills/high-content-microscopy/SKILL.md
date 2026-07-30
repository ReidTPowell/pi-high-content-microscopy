---
name: high-content-microscopy
description: Plan, validate, and analyze high-content microscopy assays with Pi. Use for HCS, high-content imaging, plate-based microscopy, HCSai or MetaXpress exports, OME-TIFF, Cellpose or StarDist segmentation, image quality control, phenotypic profiling, dose response, and microscopy-derived assay statistics. Build an explicit image manifest and assay configuration before processing data; do not assume a plate size, channel role, segmentation model, or instrument export format.
---

# High-Content Microscopy

## Establish the analysis contract

1. If a supplied directory contains multiple acquisitions, discover plate roots first; never flatten a batch into one plate manifest.
2. Ask only for assay facts that cannot be recovered: biological unit, controls, plate map, channel roles, target objects, and endpoint.
3. Write a versioned JSON or YAML assay configuration. Keep source images immutable and direct all results to a new output directory.
4. Validate the manifest against configuration before choosing preprocessing or segmentation.

Run the bundled helpers from this skill directory:

```sh
python3 scripts/hca_manifest.py --input <acquisition-directory> --output <manifest.jsonl> --summary <summary.json>
python3 scripts/hca_validate.py --manifest <manifest.jsonl> --config <assay-config.json>
python3 scripts/hca_well_plan.py --manifest <manifest.jsonl> --output-dir <well-jobs> --workers <N>
```

## Choose analysis modules deliberately

Use QC for file completeness, metadata consistency, saturation, focus, and plate-position effects. Use flat-field or background correction only when controls and image inspection justify it. Preserve pixel type and record every transform.

For batch directories, first run `hca_manifest.py --input <batch> --discover-plates --output <plates.json>`, then process each returned acquisition root independently. Within one validated plate, dispatch only independent well jobs in parallel and recombine by stable identifiers. Do not parallelize across plates unless the batch-correction and statistical plan explicitly permits it.

Select segmentation based on the biological object and image evidence: nuclei, whole cells, organelles, colonies, or tissue. Do not infer DAPI, a cytoplasm channel, Cellpose, a two-channel layout, or a 96-well plate from filename order. Tune with representative images from controls and treatments, retain overlays, and quantify failure cases.

At measurement time, report object-level data, field-level summaries, and well-level summaries separately. Preserve identifiers for plate, well, field/site, timepoint, z-plane or projection, channel, and original file. Normalize only with stated reference populations and preserve raw measurements.

## Assay profiles

Use `configs/hcsai-dapi-phalloidin.json` only as a starting point for two-channel HCSai acquisitions. Read `references/analysis-contract.md` for the portable schema and decision rules before creating a new profile.

## Verification

Before claiming biological conclusions, verify that controls exist, replicate structure is clear, QC exclusions are recorded, segmentation overlays are reviewed, and the statistical unit matches the experimental design. Distinguish exploratory morphology or embedding results from validated assay endpoints.
