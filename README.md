# Pi High-Content Microscopy

A Pi-native, configuration-first skill for high-content microscopy analysis. It begins every analysis with a portable image manifest and validation report, then selects preprocessing, segmentation, measurement, profiling, and statistical methods that fit the assay instead of assuming a fixed stain, plate format, instrument, or model.

## Install

```sh
pi install github:ReidTPowell/pi-high-content-microscopy
```

The package includes both the assay-expert skill and a lightweight Pi router. When a user says `piHCA` with an existing path, the router runs the bounded HCS.ai intake before the model acts, injects the compact inventory, and blocks broad fallback searches for that intake turn.

Keep exactly one PiHCA package source active. Do not install the GitHub package globally while also listing a local checkout in a project `.pi/settings.json`; Pi will reject the duplicate tool registrations. Remove a local duplicate with `pi remove -l /absolute/path/to/pi-high-content-microscopy --approve`, then confirm `pi list` shows only the intended GitHub source. `pihca-doctor` also reports this condition.

Set `PIHCA_PYTHON` to the locked analysis interpreter used by Pi. Without it, intake still works and preconfiguration reports missing engines instead of silently using a different environment:

```sh
export PIHCA_PYTHON=/opt/pi-hca/envs/0.6.0/bin/python
```

Create a reproducible image-analysis runtime once per release:

```sh
skills/high-content-microscopy/scripts/setup_env.sh \
  --env .venv-pihca --extras all \
  --lock-file runtime-lock.json
```

The environment installs a non-editable PiHCA package by default so the captured runtime cannot drift with source edits. Use `--editable` only for development environments and do not approve production releases from them.

## Quick start

```sh
python3 skills/high-content-microscopy/scripts/hca_manifest.py \
  --input "/path/to/acquisition" --output manifest.jsonl --summary manifest-summary.json

python3 skills/high-content-microscopy/scripts/hca_validate.py \
  --manifest manifest.jsonl --config skills/high-content-microscopy/configs/hcsai-dapi-phalloidin.json

python3 skills/high-content-microscopy/scripts/hca_well_plan.py \
  --manifest manifest.jsonl --output-dir well-jobs --workers 4
```

Then ask Pi to inspect the manifest and design or execute the assay-specific pipeline.

For a batch directory containing several acquisitions, enumerate plate roots first and run the above workflow separately for every returned root:

```sh
python3 skills/high-content-microscopy/scripts/hca_manifest.py \
  --input /path/to/batch --discover-plates --output plates.json
```

The bundled HCSai profile is validated against the included directory structure used by MetaXpress-style exports, including the DAPI/TRITC data at `TAMU-IBT_Chetna Dureja`. It is an example, not a global default.

## Design

The package does not ship a monolithic analysis script. Microscopy assays vary materially in modality, dimensionality, channel semantics, segmentation targets, controls, and endpoint definitions. The common contract is an explicit manifest plus a versioned assay configuration. This makes the agent’s choices reviewable and lets specialized engines such as Cellpose, StarDist, ilastik, napari, scikit-image, or foundation models be selected only where appropriate.

## Optional engines

Install only the reader or analysis engine needed for the assay: `pip install '.[ome]'`, `'.[bioformats]'`, `'.[qc]'`, `'.[review]'`, `'.[cellpose]'`, or `'.[stardist]'`. Each segmentation engine writes a labeled TIFF; the downstream measurement layer retains manifest identifiers and model provenance.

`hca_runner.py` executes an approved single-plate release with atomic well outputs, transient-only retries, incremental journals, structured failures, deterministic GPU assignment, and GPU admission control. It constructs a fixed argument vector for `hca_pipeline.py`; arbitrary shell templates are not accepted.

## Assisted Workflow

Pi should coordinate the workflow: discover plates, capture the assay contract, build and validate manifests, prepare seeded QC samples, request human review of raw images and overlays, then run a reviewed pipeline. Batch plans process one plate at a time and parallelize wells only within that plate. `hca_share.py` produces a portable zip of results, review decisions, configurations, and provenance while excluding source TIFFs.

After `pi install`, start a new Pi session so package skills are discovered. In the session, resolve the installed `SKILL.md` directory and run `hca_doctor.py` before the workflow. The agent should keep stage logs in the well output directory and present the compact `pipeline-summary.json`, QC report, review decision, and relationship QC counts rather than streaming image-processing logs.

For cell-level assays, nuclear and cell masks are separate products. `hca_relate.py` maps nuclei to cells by pixel overlap and only subtracts assigned nuclei from the corresponding cell to form cytoplasm. It reports orphan nuclei, low-overlap assignments, and ties instead of forcing a biological relationship.

The assay configuration can activate this graph directly: `nucleus` segmentation, `cell` segmentation using an optional nuclear channel, `relationship` QC and cytoplasm derivation, overlays, then independent measurements. `hca_pipeline.py` executes it per well field; the Pi agent should use the configured pipeline rather than choose unrecorded defaults.

## Guided Pilot And Filtering

Say `piHCA` to begin expert intake. Pi first inspects the file structure and then asks for the biological endpoint, controls, channel roles, primary/secondary objects, and the expected morphology. It should offer a non-destructive preconfiguration packet rather than starting a batch run:

The router persists the workflow from intake through nuclei, cells, filter evidence, held-out validation, immutable release, canary, explicit batch approval, production status, and plate QC. A request such as “these images” uses Pi's current directory, ordinal plate choices are resolved from intake, and `continue` advances the current safe phase instead of restarting discovery.

```sh
python3 skills/high-content-microscopy/scripts/hca_preconfigure.py \
  --input /path/to/one-acquisition --config draft-assay.json
```

The packet includes a manifest, validation report, well plan, seeded QC sample, pending review, and operator questions. Run one pilot well into a new analysis root. The supported pipeline retains `nuclei-raw-labels.tif` and `cell-raw-labels.tif`, filters them into final labels, then performs relationship assignment and measurement. Configure human-reviewed size and mean-intensity limits per object type:

```json
"filter": {
  "min_area_px": 50,
  "max_area_px": null,
  "min_intensity_mean": 400,
  "max_intensity_mean": null
}
```

Every run writes `nuclei-filter.json` and `cell-filter.json`, preserving the source label, measured area/intensity, accept/reject decision, and exclusion reason. A filter is part of the reviewed assay configuration, not an unrecorded per-well adjustment.

Direct `hca_pipeline.py` runs reject a nonempty output directory by default. Start every pilot/configuration revision in a new result root; `--allow-overwrite` is an explicit recovery-only override. Queue and runner execution retain their existing atomic staging behavior.

For a faster first tuning pass, generate conservative, review-required candidates from a pilot audit:

```sh
python3 skills/high-content-microscopy/scripts/hca_filter_tune.py \
  --audit pilot/wells/A01/s0-t0-z0/nuclei-filter.json \
  --output pilot/nuclei-filter-candidates.json
```

Candidates list predicted removals and label centroids. They are not auto-approved or applied to a batch; Pi must review them with the operator and write accepted values into a new assay-config version.

Cellpose model settings are tuned before filtering. A stage can declare `diameter`, `gpu`, and a `cellpose` block containing `flow_threshold`, `cellprob_threshold`, `normalize`, `tile_overlap`, `niter`, `min_size`, and `augment`. Compare a bounded set of segmentation candidates on a pilot image:

```sh
python3 skills/high-content-microscopy/scripts/hca_cellpose_tune.py \
  --image /path/to/dapi.tif --model nuclei --gpu \
  --diameters auto,18,22 --flow-thresholds 0.3,0.4 \
  --cellprob-thresholds -1,0 --output-dir pilot/nuclei-cellpose-candidates
```

For cells, pass `--nuclear-image /path/to/dapi.tif` and the configured boundary image. The tool writes overlays, measurements, and `candidates.json`; it does not choose or publish a winner.

Pass reviewed nuclear labels as `--reference-nuclei nuclei-labels.tif` when tuning secondary cell masks. Candidate metadata then includes nucleus-to-cell assignment counts, and automated ranking penalizes orphan and ambiguous relationships.

For a browser-based human review, run `hca_review_ui.py start --candidates ... --output-dir ... --open-browser`. The local-only UI records the selected candidate, split/merge/missed-object feedback, proposed area/intensity filters, and a bounded next sweep. Automated review uses the same PNG assets with `hca_vision_review.py` and advances a maximum-round state with `hca_optimize.py`.

OpenPhenom is optional and isolated because its model/runtime and license differ from the core MIT package. The bundled HCS.ai adapter supports arbitrary plate rows and can be selected with `"adapter_script": "bundled"`. `hca_embed.py` invokes it with the exact environment interpreter, records the adapter checksum and model snapshot, and rejects empty results. Configure and lock the separate runtime before enabling embeddings:

```sh
skills/high-content-microscopy/scripts/setup_openphenom_env.sh \
  --env /opt/pi-hca/envs/openphenom \
  --lock-file /opt/pi-hca/envs/openphenom-requirements.lock
```

After the first validated pilot, copy the recorded `model_revision` into the assay config to pin future runs.

When Pi is connected to an image-capable model, make the visual assessment reproducible instead of relying on narrative chat history:

```sh
python3 skills/high-content-microscopy/scripts/hca_vision_review.py template \
  --candidates pilot/nuclei-cellpose-candidates/candidates.json \
  --filter-audit pilot/wells/A01/s0-t0-z0/nuclei-filter.json \
  --output pilot/vision-review.pending.json
```

Pi completes the template after inspecting each overlay against its raw image, then runs `finalize` to produce ranked, acceptable candidates. The artifact records the vision model/reviewer, scores, observed split/merge/false-object errors, and filter recommendations. It is intentionally not sufficient to publish a config without named human approval.

For a plate acquisition with barcode `70126`, the default analysis root is `70126_piHCA` beside the barcode-level raw directory. Pilots, validation, approved releases, and production runs use separate immutable namespaces below that root.

## Workstation deployment

For managed GPU workstations, Pi is the operator-facing control plane: it prepares a reviewed plan, explicitly submits a plate job, monitors the queue, and reports compact results. It does not silently discover or start analyses. Each workstation uses the same locked environment, created once per release:

```sh
skills/high-content-microscopy/scripts/setup_env.sh \
  --env /opt/pi-hca/envs/0.6.0 --extras ome,qc,review,cellpose \
  --lock-file /opt/pi-hca/envs/0.6.0/runtime-lock.json
```

Initialize a shared queue directory once. Its SQLite file is an audit index; job requests and worker results are separately published as atomic JSON artifacts. Keep all queue administration on a filesystem with reliable locking. Do not put the SQLite file on an unreliable network mount.

```sh
QUEUE=/shared/pi-hca-queue
python3 skills/high-content-microscopy/scripts/hca_queue.py --queue-dir "$QUEUE" init
python3 skills/high-content-microscopy/scripts/hca_queue.py --queue-dir "$QUEUE" register-worker --worker-id gpu-ws-01
pihca queue --queue-dir "$QUEUE" publish-release \
  --release 70126_piHCA/releases/release-.../release.json --operator trained-operator
```

Publishing verifies the release and every bound artifact hash. Submit each prepared plate explicitly after its canary, then run the dispatcher from a registered workstation. Each queue job delegates bounded parallel wells to the structured runner.

```sh
pihca queue --queue-dir "$QUEUE" submit --plan well-jobs/plan.json \
  --run-dir /data/70126_piHCA/runs/run-001 --release-id release-... \
  --operator trained-operator --workers 2
pihca queue --queue-dir "$QUEUE" dispatch --worker-id gpu-ws-01 --max-jobs 1
```

Use `status`, `report`, `cancel --job-id`, and `retry --job-id` for operations. All queue results, copied published configurations, run provenance, QC/review decisions, and analysis artifacts remain shareable with `hca_share.py` while raw TIFFs remain excluded.
