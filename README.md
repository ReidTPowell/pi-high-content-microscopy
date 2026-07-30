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
```

Then ask Pi to inspect the manifest and design or execute the assay-specific pipeline.

The bundled HCSai profile is validated against the included directory structure used by MetaXpress-style exports, including the DAPI/TRITC data at `TAMU-IBT_Chetna Dureja`. It is an example, not a global default.

## Design

The package does not ship a monolithic analysis script. Microscopy assays vary materially in modality, dimensionality, channel semantics, segmentation targets, controls, and endpoint definitions. The common contract is an explicit manifest plus a versioned assay configuration. This makes the agent’s choices reviewable and lets specialized engines such as Cellpose, StarDist, ilastik, napari, scikit-image, or foundation models be selected only where appropriate.
