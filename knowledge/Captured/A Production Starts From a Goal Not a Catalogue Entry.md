---
title: "A Production Starts From a Goal, Not a Catalogue Entry"
type: decision
confidence: VERIFIED
captured_at: 2026-10-03T03:19:34.312292+00:00
contributor: "content-machine-agent"
evidence: "scripts/goal.py; POST /api/v1/projects/from-goal/; tests/test_longform_production.py::GoalDerivationTest; dreamdrip-rain-psychedelic-sleep-experie-202610030305"
tags:
  - knowledge/captured
  - knowledge/decision
---

# A Production Starts From a Goal, Not a Catalogue Entry

**scripts/goal.py derives a concept, project and research brief from a plain-language goal; the derived concept lives beside the curated catalogue rather than in it, length is parsed from the words, and every capability-bearing field is clamped to what this build actually implements.**

A production now starts from a sentence - "a 2-hour psychedelic Dreamdrip
sleep experience with rain and ambient music" - and `scripts/goal.py` turns
it into a concept, a project and a research brief in one call. The concept
is written to `experiments/derived/<id>.json`, never into
`experiments/concepts.json`: that file is the curated, human-authored
catalogue, and a per-production concept would pollute it. `find_concept`
and `project._load_concept` look in both, so everything downstream reads a
derived concept without knowing it was derived.

Two things are deliberately not left to the model:

**Length is read from the words.** "2 hours" is a fact about the request,
not a creative decision, so it is parsed before the model is asked anything
and the longest stated length wins ("a 2 hour video with 30 second scenes"
is a two-hour video).

**Capability is clamped, not proposed.** Every enumerated field the pipeline
branches on - audio requirement, spec template, whether procedural plates
are acceptable, whether narration happens - is forced to a value this build
implements. A model that asks for licensed music this host cannot licence
gets the honest requirement recorded instead, and the gate blocks exactly as
it would for a hand-written concept. This is the same refusal as
[[Never Claim Production Grade on a Human's Behalf]]: a confident reply must
not be able to talk the studio into a capability it has not got.

An excerpt is a view of a full-length production, not a different one: the
concept records the full length while the spec is built short, so the
workspace can offer "produce at full length" rather than making the operator
describe the video twice.

## Evidence

- scripts/goal.py; POST /api/v1/projects/from-goal/; tests/test_longform_production.py::GoalDerivationTest; dreamdrip-rain-psychedelic-sleep-experie-202610030305

## Related

- [[Research Findings Set Pacing, Movement and Sound, Not Just Words]]
- [[Research Brief Is the Project's Creative Input]]

*Captured 2026-10-03T03:19:34.312292+00:00 by content-machine-agent. Confidence: VERIFIED.*
