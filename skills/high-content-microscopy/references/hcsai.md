# HCS.ai Navigation

Molecular Devices HCS.ai exports are one-file-per-plane datasets. Acquisition roots are identified by `experiment/image_metadata_*.csv`; image folders and filenames are referenced by those tables. Typical image names encode `<prefix>_t<time>_<well>_s<site>_w<channel>_z<plane>.tif`.

Use `hca_intake.py` first. It walks directory names only far enough to locate acquisition metadata and counts rows from metadata CSVs, avoiding an expensive full TIFF crawl. A batch root may contain several acquisition roots; select exactly one for preconfiguration and execution.

Use `hca_manifest.py` only after plate selection. It emits one relative-path JSONL record per plane and attaches matching acquisition-table fields. The stable coordinates are well, site, timepoint, channel, and z. Do not parse treatment, control, concentration, channel biology, or replicate identity from filenames. Join these through `hca_metadata.py` and a plate map.

Treat rows from distinct fields/sites as technical observations. Preserve source-relative paths so manifests and shared reports remain portable. Never place generated `_piHCA` output under an input acquisition where discovery could ingest it.
