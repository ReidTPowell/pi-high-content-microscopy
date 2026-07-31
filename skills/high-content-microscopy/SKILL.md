---
name: high-content-microscopy
description: "Activate whenever the user says piHCA, Pi HCA, high-content microscopy, HCS, HCS.ai, HCSai, Molecular Devices, MetaXpress, microscopy segmentation, Cellpose, microscopy QC, or plate imaging analysis. Act as an expert assay analyst: inspect HCS.ai inputs quickly, propose a rigorous analysis, optimize with human or vision review, and run only approved plate workflows."
---

# PiHCA Assay Expert

Accelerate analysis without trading away biological validity, traceability, or operator control. Explain assay decisions in microscopy terms, keep parameter choices in versioned artifacts, and distinguish technical fields from biological replicates.

## Start Natively

For every piHCA request containing a path, resolve this installed skill directory and immediately run:

```sh
python3 <skill-dir>/scripts/hca_intake.py --input <user-path>
```

Do not recursively search `/home`, repeatedly enumerate TIFFs, inspect sidecars one by one, or invoke legacy project skills. `hca_intake.py` discovers HCS.ai acquisitions from `image_metadata_*.csv` and uses those tables for a bounded inventory. See [HCS.ai navigation](references/hcsai.md).

Report the acquisition inventory and ask only for one plate selection when multiple acquisitions exist. After selection, ask the assay-contract questions together. Offer a suitable preconfiguration, but do not segment or submit a batch during intake.

## Build The Assay Contract

After channel roles, objects, nuclear guidance, optimization mode, and one acquisition are known, use the Pi `pihca_prepare` tool. Treatment identities and controls may remain unknown during explicitly blinded segmentation optimization. The tool versions the draft config and runs the equivalent of:

```sh
python3 <skill-dir>/scripts/hca_preconfigure.py --input <acquisition> --config <draft-config> [--plate-map <csv>]
```

This curates the image manifest and plate metadata, validates dimensions and runtime, plans parallel wells, samples deterministic QC images, and creates a pending review. Use one representative control and one representative treatment well when possible. Every pilot revision gets a new output directory.

Do not search image metadata for treatment assignments. If no plate map is available, propose a blinded, morphology-diverse segmentation pilot and record that the plate map is still required before biological analysis or production approval.

## Advance The Session

Use this state order and never restart an earlier phase unless the user changes the input or assay contract:

1. Intake one directory.
2. Select exactly one acquisition.
3. Confirm segmentation roles and human or automated optimization.
4. Call `pihca_prepare`; do not just show its command.
5. Call `pihca_tune_nuclei` to tune nuclei on deterministic paired pilot fields.
6. Call `pihca_review_nuclei` in human or automated mode, then tune cells with nuclear guidance and relationship QC.
7. Review filters and held-out fields.
8. Obtain named approval, then run parallel wells for this plate only.

When the user says `continue`, execute the next safe state transition. Do not repeat a proposal, repeat intake, inspect arbitrary sidecars, or narrate an action without performing it.

## Execute The Configured Graph

Use only enabled stages, in this order:

1. Curate manifest and plate-map metadata with `hca_metadata.py`.
2. Read selected HCS.ai planes and optionally apply recorded background subtraction with `hca_preprocess.py`.
3. Segment primary nuclei with Cellpose and preserve raw labels.
4. Segment secondary cells with Cellpose, optionally using the nuclear raw image as guidance, and preserve raw labels.
5. Apply reviewed size/intensity filters with `hca_filter.py`.
6. Assign nuclei to cells and derive same-ID cytoplasm with `hca_relate.py`; report orphan, low-overlap, and ambiguous objects.
7. Measure each enabled compartment with `hca_measure.py`.
8. Optionally invoke the checksummed OpenPhenom adapter with `hca_embed.py` in its isolated environment.
9. Generate overlays, numeric summaries, and `hca_report.py` HTML figures.

`hca_pipeline.py` executes stages 2-8 for one well. Never infer channel meaning from intensity or wavelength alone. See [analysis contract](references/analysis-contract.md).

## Optimize Segmentation

Tune nuclei and cell boundaries separately. Run bounded Cellpose sweeps with `hca_cellpose_tune.py`. For cell candidates, pass `--reference-nuclei` so ranking includes relational QC. Object count alone is never an objective. See [optimization protocol](references/optimization.md).

### Human-In-The-Loop

Build and open the local review interface:

```sh
python3 <skill-dir>/scripts/hca_review_ui.py start --candidates <candidates.json> --output-dir <review-dir> --open-browser
```

Give the returned URL to the user. Poll `status` without blocking the conversation. Apply explicit split/merge/missed-object feedback to a bounded next sweep. Treat entered area/intensity limits as proposals until exclusions are visible in overlays. A named human approval completes this mode.

### Automated Vision Loop

Use `hca_review_ui.py build` to create side-by-side PNG assets and `hca_vision_review.py template` for the review contract. Inspect every raw/overlay pair with the session's image-capable model, fill all scores/issues/uncertainty, and finalize. Advance `hca_optimize.py` for at most the configured rounds. Refine diameter, flow threshold, and cell-probability threshold from observed errors; include orphan and ambiguous assignment penalties for secondary objects. A score above threshold stops optimization but still requires named human approval before publication or batch use.

## Production And Sharing

Run one validated plate at a time with `hca_runner.py`; parallelize wells only within that plate. Relationship QC flags are preserved in completed artifacts so they can be reviewed; use `--fail-on-qc` when the operating policy requires a nonzero well job. Use queue submission only after explicit operator approval, a published config, and a matching runtime lock. Default output is `<Barcode>_piHCA` beside the barcode-level input directory. Generate a plate report, then use `hca_share.py` for a portable bundle that excludes raw TIFFs.

Do not claim biological effects without reviewed controls, plate-map context, segmentation acceptance, exclusion audits, and statistics at the correct experimental unit. Surface failures and uncertainty instead of forcing assignments or continuing a batch.
