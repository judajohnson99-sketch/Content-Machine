---
title: "Loop Detection Needs Gain-Invariant Spectral Shape and Outlier Masking"
type: insight
confidence: VERIFIED
captured_at: 2026-10-22T08:23:03.391985+00:00
contributor: "content-machine-agent"
evidence: "projects/dreamdrip-qa-bad 2026-10-22: 6s loop of the composed bed with a 3s dropout scored 0.04 (3-band energy), 0.15 (detrended), 0.40 (6-band shape), 0.97 (shape + masking); original track 0.60, generated brown noise 0.10; tests/test_quality.py::test_a_dropout_does_not_hide_a_loop"
tags:
  - knowledge/captured
  - knowledge/insight
  - audio
  - quality
---

# Loop Detection Needs Gain-Invariant Spectral Shape and Outlier Masking

**Autocorrelating band energies misses a real loop when level changes or a single dropout dominate the variance; per-window spectral shape with far-from-programme windows masked finds it.**

A short audible loop is one of the clearest ways a long-form bed fails, and the first detector built for it in [[Perceptual Quality Is Judged Against Intent and Informs Review]] could not see one in a real production.

The cause was not the feature resolution. A plain loop of the Dreamdrip bed scored 1.0 with or without loudness normalisation; the same loop with one 3-second dropout scored 0.04. The dropout's 20-40 dB excursion dominated the variance of every band, so the repeating micro-structure everywhere else contributed almost nothing to the correlation.

Two changes fixed it: features are each window's band levels relative to that window's own mean (spectral shape), so fades, ducking and normalisation - broadband gain - cancel out; and windows more than 12 dB under or 9 dB over the programme level are masked to the series mean before correlating. The generative pad from [[Music-Branded Ambient Concepts Lead With Pad, Not Raw Noise]] still scores well under the 0.85 threshold (0.60), so the change does not turn slow musical structure into false loops.

Detrending the energy envelope was tried and rejected: it lifted the loop only to 0.15 and created a false 0.80 peak on the original track.

## Evidence

- projects/dreamdrip-qa-bad 2026-10-22: 6s loop of the composed bed with a 3s dropout scored 0.04 (3-band energy), 0.15 (detrended), 0.40 (6-band shape), 0.97 (shape + masking); original track 0.60, generated brown noise 0.10; tests/test_quality.py::test_a_dropout_does_not_hide_a_loop

## Related

- [[Perceptual Quality Is Judged Against Intent and Informs Review]]
- [[Music-Branded Ambient Concepts Lead With Pad, Not Raw Noise]]

*Captured 2026-10-22T08:23:03.391985+00:00 by content-machine-agent. Confidence: VERIFIED.*
