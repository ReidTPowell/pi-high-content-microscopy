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

For cell-level assays, nuclear and cell masks are separate products. `hca_relate.py` maps nuclei to cells by pixel overlap and only subtracts assigned nuclei from the corresponding cell to form cytoplasm. It reports orphan nuclei, low-overlap assignments, and ties instead of forcing a biological relationship.
