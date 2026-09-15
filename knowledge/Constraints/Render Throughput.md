---
title: "Render Throughput"
type: constraint
confidence: VERIFIED
tags:
  - knowledge/constraint
---

# Render Throughput

**MEASURED on this host (4 cores, no GPU).**

- Render only, 1080p at 24fps with no Ken Burns: **3.33x realtime**.
- Full `validate → render → thumbnail → QC → package` cycle: **1.4x realtime**.
  A 30-minute 1080p project took 1287s wall.

The gap between the two figures is the whole point: thumbnails and QC each
decode the entire file again. **Plan capacity from 1.4x, not 3.33x.**

## Consequence

A 3-hour upload costs roughly 2 hours of wall time, so **2-3 long videos per
day is the realistic ceiling** on this hardware.

That ceiling is what makes the 8h formats in [[Adult Sleep]] expensive to
iterate on and makes the shorter narrated formats in [[Bedtime Stories]]
cheaper to learn from per unit of wall time. It also reinforces
[[Autonomy Boundaries]]: this box is an orchestration and reliability layer,
not a generation host.

Recorded operationally in `experiments/concepts.json` under
`production_constraints.render_throughput_measured`.

## Related
- [[Dark Visual Luminance Floor]]
- [[Experiment Log]]
