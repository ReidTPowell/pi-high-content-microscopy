# Pi High-Content Microscopy

A Pi-native, configuration-first skill for high-content microscopy analysis. It begins every analysis with a portable image manifest and validation report, then selects preprocessing, segmentation, measurement, profiling, and statistical methods that fit the assay instead of assuming a fixed stain, plate format, instrument, or model.

## Install

```sh
pi install github:ReidTPowell/pi-high-content-microscopy
```

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

Install only the reader or analysis engine needed for the assay: `pip install '.[ome]'`, `'.[bioformats]'`, `'.[qc]'`, `'.[cellpose]'`, or `'.[stardist]'`. Each segmentation engine writes a labeled TIFF; the downstream measurement layer must retain the manifest identifiers and model provenance.

`hca_runner.py` executes a validated single-plate work plan with atomic well outputs, retries, resume markers, structured failures, provenance hashes, and GPU admission control. Its command template receives `{well}`, `{manifest}`, `{output}`, and `{gpu}`.

## Assisted Workflow

Pi should coordinate the workflow: discover plates, capture the assay contract, build and validate manifests, prepare seeded QC samples, request human review of raw images and overlays, then run a reviewed pipeline. Batch plans process one plate at a time and parallelize wells only within that plate. `hca_share.py` produces a portable zip of results, review decisions, configurations, and provenance while excluding source TIFFs.

After `pi install`, start a new Pi session so package skills are discovered. In the session, resolve the installed `SKILL.md` directory and run `hca_doctor.py` before the workflow. The agent should keep stage logs in the well output directory and present the compact `pipeline-summary.json`, QC report, review decision, and relationship QC counts rather than streaming image-processing logs.

For cell-level assays, nuclear and cell masks are separate products. `hca_relate.py` maps nuclei to cells by pixel overlap and only subtracts assigned nuclei from the corresponding cell to form cytoplasm. It reports orphan nuclei, low-overlap assignments, and ties instead of forcing a biological relationship.

The assay configuration can activate this graph directly: `nucleus` segmentation, `cell` segmentation using an optional nuclear channel, `relationship` QC and cytoplasm derivation, overlays, then independent measurements. `hca_pipeline.py` executes it per well field; the Pi agent should use the configured pipeline rather than choose unrecorded defaults.

For a plate acquisition with barcode `70126`, the default analysis root is `70126_piHCA` beside the barcode-level raw directory. A single-well pipeline writes to `70126_piHCA/wells/A01`; explicit `--output-dir` values override this behavior.

## Workstation deployment

For managed GPU workstations, Pi is the operator-facing control plane: it prepares a reviewed plan, explicitly submits a plate job, monitors the queue, and reports compact results. It does not silently discover or start analyses. Each workstation uses the same locked environment, created once per release:

```sh
skills/high-content-microscopy/scripts/setup_env.sh \
  --env /opt/pi-hca/envs/0.1.0 --extras qc,cellpose \
  --lock-file /opt/pi-hca/envs/0.1.0/runtime-lock.json
```

Initialize a shared queue directory once. Its SQLite file is an audit index; job requests and worker results are separately published as atomic JSON artifacts. Keep all queue administration on a filesystem with reliable locking. Do not put the SQLite file on an unreliable network mount.

```sh
QUEUE=/shared/pi-hca-queue
python3 skills/high-content-microscopy/scripts/hca_queue.py --queue-dir "$QUEUE" init
python3 skills/high-content-microscopy/scripts/hca_queue.py --queue-dir "$QUEUE" register-worker --worker-id gpu-ws-01
python3 skills/high-content-microscopy/scripts/hca_queue.py --queue-dir "$QUEUE" publish-config \
  --config assay.json --review approved-review.json --operator trained-operator
```

Publishing requires an approved review with a named reviewer. Submit each prepared plate explicitly, then run the dispatcher from a registered workstation. It claims at most the requested number of whole-plate jobs; each job delegates bounded parallel wells to `hca_runner.py`.

```sh
python3 skills/high-content-microscopy/scripts/hca_queue.py --queue-dir "$QUEUE" submit \
  --plan well-jobs/plan.json --output-dir /data/70126_piHCA --config-id cfg-... \
  --runtime-lock /opt/pi-hca/envs/0.1.0/runtime-lock.json --operator trained-operator --workers 2
python3 skills/high-content-microscopy/scripts/hca_queue.py --queue-dir "$QUEUE" dispatch \
  --worker-id gpu-ws-01 --max-jobs 1 \
  --command 'python3 /absolute/path/hca_pipeline.py --well-manifest {manifest} --config {config} --source-root /data/70126 --output-dir {output}'
```

Use `status`, `report`, `cancel --job-id`, and `retry --job-id` for operations. All queue results, copied published configurations, run provenance, QC/review decisions, and analysis artifacts remain shareable with `hca_share.py` while raw TIFFs remain excluded.
