---
title: "Technical Validity Is Not Production Quality"
type: decision
confidence: VERIFIED
captured_at: 2026-09-19T12:48:02.770058+00:00
contributor: "content-machine-agent"
evidence: "scripts/qc.py visual_diversity_warnings + _strip_scene_suffix; scripts/project.py _creative_quality_blockers wired into gate_blockers; scripts/audio.py _quality_check (has_musical_layer removed); tests/test_qc.py, tests/test_project.py::CreativeQualityBlockersTest; validated read-only against projects/ambient-dogs-home-alone-20260919"
tags:
  - knowledge/captured
  - knowledge/decision
  - qc
  - review-gate
  - quality
---

# Technical Validity Is Not Production Quality

**QC's PASS/FAIL answers whether a render is technically valid; whether it is worth publishing is a separate signal that blocks review through gate_blockers, and no machine-checkable field may stand in for a human quality judgement.**

The ambient-dogs-home-alone-20260919 render passed QC 15/15 and was still
judged unpublishable by a human: near-identical beige rooms, audio that read
as static. The lesson is not "make QC stricter". It is that two different
questions were being answered by one word.

**Technical validity** is what `scripts/qc.py` decides: does the file decode,
is the codec right, does the duration match, is every scene asset present.
Its `status` stays PASS for a storyboard whose scenes all depict the same
room, and that is correct — nothing about it is broken. Widening PASS/FAIL to
cover taste would make a technically-sound render fail and would give the
existing `qc_status` consumers a meaning they were not written for.

**Production quality** is what [[Publication Gate]] decides, through
`gate_blockers()` in `scripts/project.py`. A new `_creative_quality_blockers`
helper folds detected creative defects — unplanned scene repetition, persisted
audio quality warnings — into the same list that already carries the
`production_grade` requirement. An obvious defect can therefore no longer sit
quietly beside an unqualified PASS: both reasons surface together, and neither
alone lets a project reach READY_FOR_REVIEW.

## A proxy is not the thing it proxies

Three fields were removed or rewritten because each was an easy machine-checkable
stand-in being read as the quality claim itself:

- A fixed 4-8 environment range stood in for "enough unique visual content".
  The right amount follows from duration, format and viewing behaviour, so
  that is what the model is now asked to reason about.
- `has_musical_layer: true` (a pad provider is present) stood in for "the
  audio is pleasant". Removed; presence of a musical layer is not evidence
  anyone enjoys it.
- A non-blocking warning stood in for "someone will notice this". It now
  blocks.

The same rule already governs `production_grade`: a machine may establish the
negative, never the positive. Extending it, **a signal may only assert what it
actually measures.** `_quality_check` reports layer count, providers and
whether the signal varies — facts — and states in the code that no field in it
claims the result sounds good, because only a person listening can say that.

## Measure the thing, not its formatting

While validating this, the repetition check was found silently passing on the
exact project it was written for. `storyboard.py::apply_scene_motifs` appends
`", scene N, <section>"` to every prompt, so ten scenes depicting one identical
room were never byte-identical strings. An exact-string comparison had been
standing in for "same setting". Stripping that bookkeeping suffix before
comparing (`qc.py::_strip_scene_suffix`) made it fire correctly on the real
evidence. A quality check that never fires on the case that motivated it is
worse than none — it reads as reassurance.

## Resolved

Whether "genuinely pleasant audio" deserves its own human-confirmed
`provenance.audio.production_grade`, mirroring the images gate in
[[Provenance and Production-Grade Claims]], was left open here as a product
decision. It was answered yes, and narrowly: the claim is demanded only where
the chosen source cannot establish its own quality, and it is void once the
audio plan changes. See [[Audio Production Grade Is a Human Verdict Bound to the Plan]].

## Evidence

- scripts/qc.py visual_diversity_warnings + _strip_scene_suffix; scripts/project.py _creative_quality_blockers wired into gate_blockers; scripts/audio.py _quality_check (has_musical_layer removed); tests/test_qc.py, tests/test_project.py::CreativeQualityBlockersTest; validated read-only against projects/ambient-dogs-home-alone-20260919

## Related

- [[Publication Gate]]
- [[Provenance and Production-Grade Claims]]
- [[Non-Documentary Concepts Get Distinct Environments Not One Reused Prompt]]
- [[Music-Branded Ambient Concepts Lead With Pad Not Raw Noise]]

*Captured 2026-09-19T12:48:02.770058+00:00 by content-machine-agent. Confidence: VERIFIED.*
