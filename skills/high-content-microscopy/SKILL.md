---
name: high-content-microscopy
description: Plan, validate, deploy, and analyze high-content microscopy assays with Pi. Activate this skill whenever the user requests piHCA, Pi HCA, high-content imaging, HCS, plate-based microscopy, HCSai or MetaXpress exports, OME-TIFF, Cellpose or StarDist segmentation, image quality control, phenotypic profiling, dose response, or microscopy-derived assay statistics. On piHCA requests, use this skill before taking analysis actions. Build an explicit image manifest and assay configuration before processing data; do not assume a plate size, channel role, segmentation model, or instrument export format.
---

# High-Content Microscopy

## PiHCA expert intake

When a user says `piHCA`, `Pi HCA`, or asks to analyze microscopy data, begin as an assay expert, not a command runner:

1. Inspect the supplied root read-only. State whether it is a batch or an acquisition, enumerate acquisitions, image counts, wells/sites/channels/z/timepoints, and identify recoverable metadata.
2. Ask only the facts the files cannot establish: biological question and endpoint, controls/plate map, channel roles, primary object, secondary object, and whether the secondary boundary should be guided by the primary raw image.
3. Offer a preconfiguration packet for one acquisition. It creates the manifest, validation, well plan, seeded QC sample, and pending review in one command; it never launches a plate run.
4. Run a representative pilot well only after the draft config is confirmed. Preserve raw masks, inspect primary, secondary, and relationship overlays, then ask the reviewer to identify true objects and removable debris/dim objects.
5. Translate that review into explicit per-stage `filter` limits for area and mean intensity. Explain that limits are retained in the versioned config and apply before relational segmentation and measurements.
6. Require an approved review before publishing the config or submitting a plate. Do not reuse, overwrite, or aggregate a previous analysis root for a new parameter set.

Use the preconfiguration command after choosing a draft profile:

```sh
python3 scripts/hca_preconfigure.py --input <one-acquisition> --config <draft-assay.json>
```

The resulting `operator-questions.json` is the required intake and the `review.pending.json` is the decision record. For a multi-plate root, first discover plate roots; preconfigure one acquisition at a time.

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

For any request containing `piHCA` or `Pi HCA`, activate this skill and begin with the established contract or queue status rather than choosing an unrelated analysis tool. At full scale, Pi must submit only explicit operator requests through `hca_queue.py`; it should report `status` or `report` between requests and not auto-discover or auto-start acquisitions.

## Choose analysis modules deliberately

Use QC for file completeness, metadata consistency, saturation, focus, and plate-position effects. Use flat-field or background correction only when controls and image inspection justify it. Preserve pixel type and record every transform.

Create a seeded QC sample, inspect raw images and segmentation overlays, then save a review decision with `hca_review.py`. Do not scale a new assay configuration until a named reviewer approves the representative sample. Retain rejected cases and notes as training and tuning evidence, not as silently discarded failures.

For batch directories, first run `hca_manifest.py --input <batch> --discover-plates --output <plates.json>`, then process each returned acquisition root independently. Within one validated plate, dispatch only independent well jobs in parallel and recombine by stable identifiers. Do not parallelize across plates unless the batch-correction and statistical plan explicitly permits it.

Select segmentation based on the biological object and image evidence: nuclei, whole cells, organelles, colonies, or tissue. Run `hca_preflight.py` before dispatching jobs so unavailable model dependencies fail before consuming plate resources. Do not infer DAPI, a cytoplasm channel, Cellpose, a two-channel layout, or a 96-well plate from filename order. Tune with representative images from controls and treatments, retain overlays, and quantify failure cases.

Use `hca_segment.py` only after channel and z selection are explicit. It presents `threshold`, `cellpose`, and `stardist` through the same label-TIFF output contract; install the matching optional extra before invoking a model engine. Run `hca_runner.py` with the well plan for atomic retries, resumability, structured errors, provenance, and bounded CPU/GPU scheduling.

The configured pipeline always preserves `*-raw-labels.tif` and writes filtered `*-labels.tif`. Configure `analysis.segmentation.nucleus.filter` and `analysis.segmentation.cell.filter` with reviewed `min_area_px`, `max_area_px`, `min_intensity_mean`, and `max_intensity_mean` values. `hca_filter.py` writes object-level audits naming every excluded label and reason. Filtered nuclei and cells, not raw masks, are related and measured. The primary nuclear raw image may guide Cellpose secondary-cell segmentation with `use_nuclear_image`; this is distinct from mask-to-mask relationship assignment.

For pilot optimization, run `hca_filter_tune.py --audit <nuclei-or-cell-filter.json> --output <candidate.json>`. It calculates conservative distribution-based candidates and identifies every predicted exclusion by source label and centroid. Pi must present this as a review aid, inspect the corresponding overlays with the operator, and update a new versioned config only after confirmation.

Tune Cellpose boundary decisions separately from post-segmentation filtering. The stage `cellpose` block supports `flow_threshold`, `cellprob_threshold`, `normalize`, `tile_overlap`, `niter`, `min_size`, and `augment`; `diameter` and `gpu` remain stage-level values. Use `hca_cellpose_tune.py` on one pilot image with a bounded grid of diameter, flow, and cell-probability values. For cell boundaries, pass the same raw nuclear image used by the configured `use_nuclear_image` workflow. Review candidate overlays before placing selected parameters in the versioned config. Never select a candidate solely because it maximizes the object count.

When an image-capable model is available, Pi should perform structured vision QC, not merely describe images informally. Run `hca_vision_review.py template --candidates <candidates.json> --filter-audit <audit.json> --output <vision-review.json>`, inspect every referenced overlay against its raw image, complete scores and biological acceptability in the JSON, then run `finalize`. The final artifact ranks acceptable candidates and preserves the model/reviewer identity, observations, proposed filters, and uncertainty. It remains a proposal until a named human approves the versioned assay config.

Use `hca_measure.py` after segmentation. It takes label TIFFs and optional intensity images, so morphology and intensity measurements do not depend on how labels were generated. Join its output to plate and treatment metadata only through manifest identifiers.

For cell assays, make relational segmentation explicit. Generate nuclei from the nuclear channel and cells from the boundary/cytoplasm channel, preferably supplying the nuclear image as `--nuclear-image` to a cell-boundary model. Run `hca_relate.py --nuclei <nuclear-labels> --cells <cell-labels> --output-dir <relationship-output>`. It assigns each nucleus by maximal overlap, rejects low-overlap and tied assignments, creates an assigned-nuclei label image, and derives cytoplasm per cell by subtracting assigned nuclei. Review orphan and ambiguous counts before interpreting cytoplasmic measurements.

Use `hca_batch.py` for batch roots: it processes plates sequentially and creates bounded parallel-well plans within each plate. Use `hca_share.py` to package manifests, configurations, QC/review reports, provenance, tabular outputs, and overlays without raw microscopy TIFFs.

Run the configured pipeline with `hca_pipeline.py --well-manifest <well.jsonl> --config <assay.json> --source-root <plate-root>`. By default it writes `<Barcode>_piHCA/wells/<well>` beside the barcode-level raw input folder; use `--output-dir` only to select a new analysis root. Direct pipeline runs reject nonempty outputs unless `--allow-overwrite` is stated deliberately. For plate-scale parallel execution, pass the same config to `hca_runner.py --config <assay.json>` and use a command template containing `{manifest}`, `{output}`, and `{config}`. The pipeline returns nonzero if relational QC exceeds configured orphan or ambiguity thresholds, preventing invalid fields from being aggregated silently.

At measurement time, report object-level data, field-level summaries, and well-level summaries separately. Preserve identifiers for plate, well, field/site, timepoint, z-plane or projection, channel, and original file. Normalize only with stated reference populations and preserve raw measurements.

## Assay profiles

Use `configs/hcsai-dapi-phalloidin.json` only as a starting point for two-channel HCSai acquisitions. Read `references/analysis-contract.md` for the portable schema and decision rules before creating a new profile.

## Verification

Before claiming biological conclusions, verify that controls exist, replicate structure is clear, QC exclusions are recorded, segmentation overlays are reviewed, and the statistical unit matches the experimental design. Distinguish exploratory morphology or embedding results from validated assay endpoints.
