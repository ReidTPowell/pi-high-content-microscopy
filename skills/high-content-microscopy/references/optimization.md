# Optimization Protocol

Optimize on representative wells and fields, including controls and hard morphology. Keep each round immutable. Record the source image, model, model/runtime version, normalization, diameter, flow threshold, cell-probability threshold, object filters, random seed, and reviewer.

For nuclei, assess missed nuclei, debris, merged touching nuclei, split nuclei, edge truncation, and boundary placement. For cells, also assess whether cell boundaries are biologically plausible and whether each reviewed nucleus maps to exactly one cell. Penalize orphan and ambiguous relationships; do not reward counts in isolation.

Tune model behavior before post-segmentation filters. Filters may remove clearly implausible objects by area or source-channel mean intensity, but must preserve raw labels and a label-level exclusion audit. Avoid filters that encode the expected treatment effect.

Human mode repeats a bounded sweep from explicit operator feedback and ends only with named approval. Automated mode uses the image-capable Pi session to complete a structured review, refines at most three rounds by default, and then stops for human approval or intervention. Validate the accepted configuration on held-out fields from multiple wells before plate submission.

Track both visual and quantitative guardrails: acceptable boundary score, object-count stability, orphan fraction, ambiguous fraction, failure rate, runtime, and memory. Stop and escalate when no candidate is defensible, the review images are unrepresentative, or optimization repeatedly pushes parameters to the search boundary.
