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

Resolve this skill's installed directory from the loaded `SKILL.md`, then invoke its helpers by absolute path. Do not assume `scripts/` is relative to the user's project directory. Start every execution with `hca_doctor.py` and `hca_preflight.py`; report missing dependencies or unavailable source paths before writing output.

Run the bundled helpers from this skill directory:

```sh
python3 scripts/hca_manifest.py --input <acquisition-directory> --output <manifest.jsonl> --summary <summary.json>
python3 scripts/hca_validate.py --manifest <manifest.jsonl> --config <assay-config.json>
python3 scripts/hca_well_plan.py --manifest <manifest.jsonl> --output-dir <well-jobs> --workers <N>
python3 scripts/hca_qc.py --manifest <manifest.jsonl> --output <qc.json> --seed 7
python3 scripts/hca_preflight.py --config <assay.json>
python3 scripts/hca_doctor.py --config <assay.json> --source-root <plate-root>
scripts/setup_env.sh --env .venv-hca --extras qc,cellpose
```

## Choose analysis modules deliberately

Use QC for file completeness, metadata consistency, saturation, focus, and plate-position effects. Use flat-field or background correction only when controls and image inspection justify it. Preserve pixel type and record every transform.

Create a seeded QC sample, inspect raw images and segmentation overlays, then save a review decision with `hca_review.py`. Do not scale a new assay configuration until a named reviewer approves the representative sample. Retain rejected cases and notes as training and tuning evidence, not as silently discarded failures.

For batch directories, first run `hca_manifest.py --input <batch> --discover-plates --output <plates.json>`, then process each returned acquisition root independently. Within one validated plate, dispatch only independent well jobs in parallel and recombine by stable identifiers. Do not parallelize across plates unless the batch-correction and statistical plan explicitly permits it.

Select segmentation based on the biological object and image evidence: nuclei, whole cells, organelles, colonies, or tissue. Run `hca_preflight.py` before dispatching jobs so unavailable model dependencies fail before consuming plate resources. Do not infer DAPI, a cytoplasm channel, Cellpose, a two-channel layout, or a 96-well plate from filename order. Tune with representative images from controls and treatments, retain overlays, and quantify failure cases.

Use `hca_segment.py` only after channel and z selection are explicit. It presents `threshold`, `cellpose`, and `stardist` through the same label-TIFF output contract; install the matching optional extra before invoking a model engine. Run `hca_runner.py` with the well plan for atomic retries, resumability, structured errors, provenance, and bounded CPU/GPU scheduling.

Use `hca_measure.py` after segmentation. It takes label TIFFs and optional intensity images, so morphology and intensity measurements do not depend on how labels were generated. Join its output to plate and treatment metadata only through manifest identifiers.

For cell assays, make relational segmentation explicit. Generate nuclei from the nuclear channel and cells from the boundary/cytoplasm channel, preferably supplying the nuclear image as `--nuclear-image` to a cell-boundary model. Run `hca_relate.py --nuclei <nuclear-labels> --cells <cell-labels> --output-dir <relationship-output>`. It assigns each nucleus by maximal overlap, rejects low-overlap and tied assignments, creates an assigned-nuclei label image, and derives cytoplasm per cell by subtracting assigned nuclei. Review orphan and ambiguous counts before interpreting cytoplasmic measurements.

Use `hca_batch.py` for batch roots: it processes plates sequentially and creates bounded parallel-well plans within each plate. Use `hca_share.py` to package manifests, configurations, QC/review reports, provenance, tabular outputs, and overlays without raw microscopy TIFFs.

Run the configured pipeline with `hca_pipeline.py --well-manifest <well.jsonl> --config <assay.json> --source-root <plate-root> --output-dir <well-output>`. For plate-scale parallel execution, pass the same config to `hca_runner.py --config <assay.json>` and use a command template containing `{manifest}`, `{output}`, and `{config}`. The pipeline returns nonzero if relational QC exceeds configured orphan or ambiguity thresholds, preventing invalid fields from being aggregated silently.

At measurement time, report object-level data, field-level summaries, and well-level summaries separately. Preserve identifiers for plate, well, field/site, timepoint, z-plane or projection, channel, and original file. Normalize only with stated reference populations and preserve raw measurements.

## Assay profiles

Use `configs/hcsai-dapi-phalloidin.json` only as a starting point for two-channel HCSai acquisitions. Read `references/analysis-contract.md` for the portable schema and decision rules before creating a new profile.

## Verification

Before claiming biological conclusions, verify that controls exist, replicate structure is clear, QC exclusions are recorded, segmentation overlays are reviewed, and the statistical unit matches the experimental design. Distinguish exploratory morphology or embedding results from validated assay endpoints.
