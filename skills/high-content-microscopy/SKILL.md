---
name: high-content-microscopy
description: Activate whenever the user says piHCA, Pi HCA, high-content microscopy, HCS, HCSai, MetaXpress, microscopy segmentation, Cellpose, microscopy QC, or plate imaging analysis. Begin with the PiHCA intake command and follow the staged workflow below.
---

# PiHCA Workflow

## Mandatory Start

On every `piHCA` request with an input path, resolve this installed skill directory and immediately run:

```sh
python3 <skill-dir>/scripts/hca_intake.py --input <user-path>
```

Do not recursively search `/home`, enumerate files with repeated `ls`/`find` loops, inspect every metadata file manually, or load legacy project skills. The intake output is the source of truth for acquisitions, image counts, wells, sites, channels, timepoints, and z planes.

Report the compact intake result, then ask the four questions in `intake.json` in one message. If it reports multiple acquisitions, wait for the user to select one. Do not run segmentation, tuning, or a batch at intake.

## Preconfiguration And Pilot

After the user selects one acquisition and confirms the assay facts, choose or create a draft versioned config and run:

```sh
python3 <skill-dir>/scripts/hca_preconfigure.py --input <acquisition> --config <draft-config>
```

This creates a manifest, validation report, well plan, seeded QC, readiness report, and pending review. It does not start analysis. Run `hca_doctor.py` and `hca_preflight.py` before a pilot. Use one representative well and a new output directory for every config revision; never overwrite earlier pilot artifacts.

## Segmentation Optimization

Treat primary nuclei and secondary cell boundaries as separate segmentation decisions. The secondary Cellpose model may use the primary **raw image** as guidance via `use_nuclear_image`; filtered labels are then related by `hca_relate.py`.

1. Use `hca_cellpose_tune.py` for a bounded sweep of diameter, flow threshold, and cell-probability threshold on one pilot image. It writes overlay and measurement artifacts per candidate.
2. Use an image-capable model to inspect each overlay against its raw image. Create/finalize an `hca_vision_review.py` artifact. Do not choose candidates by object count alone.
3. Preserve raw labels. `hca_filter.py` applies reviewed size/intensity filters, writes label-level audits with centroids, then supplies filtered labels to relation and measurement.
4. Use `hca_filter_tune.py` only to propose filter limits from pilot distributions. Review its predicted excluded labels visually; copy only approved limits to a new config version.
5. Require named human approval in `hca_review.py` before config publication or batch submission.

Cellpose options belong in each segmentation stage: stage-level `diameter` and `gpu`; `cellpose.flow_threshold`, `cellprob_threshold`, `normalize`, `tile_overlap`, `niter`, `min_size`, and `augment`.

## Production

Use `hca_runner.py` for one validated plate with bounded parallel wells. Use `hca_queue.py` only after an explicit operator request, a published approved config, and a matching runtime lock. Plates remain sequential; wells may run in parallel. Results default to `<Barcode>_piHCA` beside raw input. Use `hca_share.py` for portable bundles without source TIFFs.

Return compact manifest/QC/review/relationship summaries to the user. Keep stage logs and large artifacts in their result directory. Never claim biological conclusions without controls, reviewed overlays, recorded exclusions, and an appropriate experimental unit.
