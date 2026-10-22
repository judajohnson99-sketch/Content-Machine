---
title: "Tiny Calibration Sets Reward Rule Search"
type: insight
confidence: VERIFIED
captured_at: 2026-10-22T09:07:48.278102+00:00
contributor: "content-machine-agent"
evidence: "scripts/vision_calibration.py consistency evaluation; tests/test_vision_calibration.py (random model across 5 seeds calibrates no dimension)"
tags:
  - knowledge/captured
  - knowledge/insight
  - vision
  - calibration
---

# Tiny Calibration Sets Reward Rule Search

**Searching many candidate rules over a handful of labelled examples finds one that passes by chance; fix the rule before measuring.**

While building the vision calibration harness, a grid search over 12 candidate consistency rules on three odd-one-out images produced a "calibrated" result for a model that was guessing. With so few positives, some rule always fits. The harness now uses one fixed rule per dimension, more sets (6 odd, 30 uniform), and a test that a randomly answering model calibrates nothing across five seeds.

The dataset is also constructed: labels are known by construction from the repo's procedural plates, so passing it shows the harness works and a model can read obvious cases, not that it judges real renders. This keeps [[Perceptual Quality Is Judged Against Intent and Informs Review]] honest, and is the same gap as [[Technical Validity Is Not Production Quality]]: a pass on a proxy is not a pass on the thing.

Duplicate (image, prompt) pairs were a second trap: they are served from the cache and count as independent evidence, so the builder now samples without replacement and a test asserts no two cases share image bytes.

## Evidence

- scripts/vision_calibration.py consistency evaluation; tests/test_vision_calibration.py (random model across 5 seeds calibrates no dimension)

## Related

- [[Perceptual Quality Is Judged Against Intent and Informs Review]]
- [[Technical Validity Is Not Production Quality]]

*Captured 2026-10-22T09:07:48.278102+00:00 by content-machine-agent. Confidence: VERIFIED.*
