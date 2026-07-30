# Analysis Contract

An assay configuration has three layers:

1. `input`: adapter name, allowed extensions, expected dimensions, and optional plate map.
2. `channels`: stable channel IDs with names and roles. Roles can be `nucleus`, `cell_boundary`, `organelle`, `reporter`, `brightfield`, `transmitted`, or `measurement_only`.
3. `analysis`: QC thresholds, transforms, segmentation targets, measurements, aggregation level, controls, and statistical comparisons.

The manifest is the interoperability boundary. Each JSONL record contains `path`, `format`, `plate`, `well`, `row`, `column`, `site`, `timepoint`, `channel`, `z`, and `adapter`. Unknown values are null, never invented.

Use `hcsai` for one-file-per-plane MetaXpress/HCSai exports named like `<prefix>_t0_A01_s0_w0_z0.tif`. Use `generic-tiff` where file names do not encode coordinates. For OME-TIFF, read OME metadata with a suitable library and emit the same manifest fields. Add a format adapter rather than duplicating processing code.

Do not combine technical image fields as biological replicates. Aggregate image to well, then well to experimental replicate only when the plate map and experimental design support it. Carry control labels from the plate map, not filename heuristics.
