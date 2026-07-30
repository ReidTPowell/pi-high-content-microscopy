# Format Adapters

The manifest contract is independent of the reader. `hcsai` is dependency-free and reads MetaXpress one-file-per-plane exports. Generic TIFF is retained without invented coordinates. OME-TIFF, CZI, ND2, and LIF records are discovered as `bioio-required`; install `pip install '.[bioformats]'`, read their acquisition metadata with BioIO, and emit the same fields before analysis.

Never infer plate coordinates from CZI, ND2, LIF, or arbitrary TIFF basenames. Join a plate map or an instrument-specific sidecar by a documented identifier. A reader must expose dimensions, physical pixel sizes, channel names, timestamps, and source identifiers, then emit one record per selected plane or a declared multidimensional image record.
