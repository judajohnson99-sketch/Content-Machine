---
title: "Dark Visual Luminance Floor"
type: constraint
confidence: VERIFIED
tags:
  - knowledge/constraint
---

# Dark Visual Luminance Floor

**MEASURED on this host: a frame whose perceived luma falls below roughly
26/255 trips the QC blackdetect check (`pic_th=0.98`) and blocks
`READY_FOR_REVIEW`.**

Dark sleep visuals must therefore sit at or above approximately `#1a1a2e`.

The constraint is doubly welcome. Mechanically it keeps the [[Publication Gate]]
from passing a video that is effectively a black screen. On policy grounds, a
frame indistinguishable from black is precisely the blank-screen pattern that
attracts the scrutiny described in [[YouTube Inauthentic Content Policy]].

This is the floor every dark concept in [[Adult Sleep]] is designed against,
and it is why the shipped brown-noise plates are a deep blue-black wash rather
than true black.

Source of truth: `scripts/qc.py`; per-project verdicts in
`projects/<id>/metadata.json`. Recorded operationally in
`experiments/concepts.json` under
`production_constraints.dark_visuals_luminance_floor`.

## Related
- [[Render Throughput]]
- [[Provenance and Production-Grade Claims]]
