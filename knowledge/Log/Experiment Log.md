---
title: "Experiment Log"
type: insight
confidence: VERIFIED
tags:
  - knowledge/log
---

# Experiment Log

What has actually been run, and what it taught us. Per-project detail lives in
`projects/<id>/metadata.json`; this note carries only the transferable part.

## Runs

| Project | Concept | Niche | Gate verdict |
|---|---|---|---|
| `demo-001` | — | — | NEEDS_ATTENTION |
| `exp-a-rain-focus` | focus-library-rain-study | [[Focus Ambience]] | NEEDS_ATTENTION |
| `exp-b-sleep-brown` | sleep-brown-noise-dark | [[Adult Sleep]] | **READY_FOR_REVIEW** |
| `exp-brown-noise-001` | sleep-brown-noise-dark | [[Adult Sleep]] | NEEDS_ATTENTION |
| `exp-brown-noise-002` | sleep-brown-noise-dark | [[Adult Sleep]] | NEEDS_ATTENTION |
| `exp-c-narrated-sleep` | story-sleepy-history-adult | [[Bedtime Stories]] | NEEDS_ATTENTION |
| `exp-sleepy-history-001` | story-sleepy-history-adult | [[Bedtime Stories]] | NEEDS_ATTENTION |

## What the runs established

**Every render passed QC; almost nothing passed the gate.** Six of seven sit at
`NEEDS_ATTENTION` with `render: OK` and `qc: PASS`. The blocker is not the
renderer — it is the undeclared production-grade claim described in
[[Provenance and Production-Grade Claims]]. That is the
[[Publication Gate]] behaving exactly as designed: fail-closed, and unmoved by
a clean technical result.

**`exp-b-sleep-brown` is the one that cleared it**, because a human declared
the dark plate to be the intended deliverable for the format rather than a
stand-in for photography, and the luma was verified above the floor in
[[Dark Visual Luminance Floor]].

**The synthesis-only path works end to end.** Brown noise composed locally, no
external dependency, no licensing question, no spend — the reason
[[Adult Sleep]] was the right place to start even though it is the niche most
exposed to [[YouTube Inauthentic Content Policy]].

**Narrated long-form is real, not theoretical.** Two sleepy-history projects
rendered with local narration, confirming [[Local Narration Capability]] in
practice rather than on paper.

## Related
- [[Open Research Questions]]
- [[Render Throughput]]
