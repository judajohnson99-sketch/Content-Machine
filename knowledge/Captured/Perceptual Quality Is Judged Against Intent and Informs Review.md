---
title: "Perceptual Quality Is Judged Against Intent and Informs Review"
type: decision
confidence: VERIFIED
captured_at: 2026-10-22T08:23:03.296913+00:00
contributor: "content-machine-agent"
evidence: "scripts/quality.py; tests/test_quality.py (40 good/bad evaluations); projects/dreamdrip-qa-01 vs dreamdrip-qa-bad 2026-10-22: old QC PASS 15/15 + storyboard QC 8/8 + qc.assess_image OK + audio.assess OK on the degraded variant, new assessment 12 to review (off-key plate, 4 jarring transitions, off-look scene, startle, dropout, 6s loop) vs 2 on the original"
tags:
  - knowledge/captured
  - knowledge/decision
  - qc
  - quality
  - review-gate
---

# Perceptual Quality Is Judged Against Intent and Informs Review

**scripts/quality.py assesses whether a technically valid render looks and sounds right for its own intent; taste-dependent findings inform the reviewer, only objective defects in the delivered video gate review, and an evaluation that could not run is never a pass.**

[[Technical Validity Is Not Production Quality]] separated two questions; this answers the second one with measurements instead of leaving it entirely to a listener. `scripts/quality.py` runs after QC on every render and writes `output/quality_report.json`.

## Intent first

Every threshold comes from an intent profile, not a global standard: the listening context (from the audio manifest) and the concept choose a visual profile (sleep / calm / narrative), and the visual direction supplies the palette and lighting key. Darkness, near-stillness and an unchanging bed are defects in an explainer and the point of a sleep video, so "dark" is never a finding for sleep, while brightening is judged more strictly than darkening.

## What reaches the gate

Only findings with no legitimate reading block, through `quality.blockers()` into the [[Publication Gate]]: the delivered soundtrack clipping, silent or unmeasurable, and a report that no longer describes the video on disk (stale). Distance from a loudness target, a vivid plate, a jarring dissolve, a loop - all inform review. This is the same asymmetry as [[Provenance and Production-Grade Claims]]: a machine may establish the negative, never certify that a video is good.

## Measured calibration

On the Dreamdrip production (sleep, procedural plates): raw chroma flagged every deep indigo plate as "saturated", so vividness weights chroma by lightness - a dark jewel tone is restful, a bright saturated field glares. Momentary loudness of the composed bed never rose more than 3.9 LU over its running median; an injected door slam rose 8.8, so the sleep spike threshold is 6 LU, in line with sound_design's role gain limits.

## Unavailable is a state

Scene imagery (depiction, composition, artifacts, consistency) needs a local vision model (QUALITY_VISION_URL + QUALITY_VISION_MODELS, Ollama API, no default). Without one it reports Not evaluated, and the report's coverage lists it - never as a pass.

## Evidence

- scripts/quality.py; tests/test_quality.py (40 good/bad evaluations); projects/dreamdrip-qa-01 vs dreamdrip-qa-bad 2026-10-22: old QC PASS 15/15 + storyboard QC 8/8 + qc.assess_image OK + audio.assess OK on the degraded variant, new assessment 12 to review (off-key plate, 4 jarring transitions, off-look scene, startle, dropout, 6s loop) vs 2 on the original

## Related

- [[Technical Validity Is Not Production Quality]]
- [[Publication Gate]]
- [[Provenance and Production-Grade Claims]]

*Captured 2026-10-22T08:23:03.296913+00:00 by content-machine-agent. Confidence: VERIFIED.*
