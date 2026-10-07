---
title: "Research Findings Set Pacing, Movement and Sound, Not Just Words"
type: decision
confidence: VERIFIED
captured_at: 2026-10-03T03:19:12.858093+00:00
contributor: "content-machine-agent"
evidence: "scripts/research.py::production_directives; projects/<id>/research_influence.json; tests/test_longform_production.py::ProductionDirectiveTest,StoryboardDirectiveTest"
tags:
  - knowledge/captured
  - knowledge/decision
---

# Research Findings Set Pacing, Movement and Sound, Not Just Words

**Sourced findings are turned deterministically into production directives (shot length, dissolve length, movement style, audio layers) that the storyboard and sound design apply, with the evidence behind each one recorded and shown; an unresearched production with a brief is a gate blocker.**

Research that only reaches the title and the image prompt is research the
finished video does not really carry. `research.production_directives`
turns sourced findings into the small set of numbers the build actually
works from - how long a shot is held, how long a dissolve runs, how the
camera moves, how long videos in this niche run, which sound layers lead -
and keeps, for each one, the finding ids and source URLs that produced it.

Three properties make it honest rather than decorative:

**Deterministic, never an LLM.** "Three sourced statements about pacing say
thirty seconds" is a fact about what was found. Asking a model to pick a
pacing from them would be a creative act wearing a citation. The same
reasoning as the frequency-counted interpretations research already
produced.

**A parameter with no evidence gets no directive.** An empty decision list
is a legitimate outcome, and the stage defaults then stand. Research that
said nothing about pacing must not invent a pacing.

**What was suggested and what was applied are recorded separately.**
`projects/<id>/research_influence.json` carries every directive, what the
build applied (smaller - a directive can be bounded, e.g. a dissolve can
never be longer than the shot it leaves), and what it could not reach. The
dashboard shows all three, so "research-driven" is a claim a person can
check rather than one the pipeline makes about itself.

Because every production derived from a goal carries a research brief, a
brief with no findings is now a review-gate blocker. "Nobody could research
it" is not "it needed no research": the video still produces and previews,
but it cannot pass a gate that says this is a researched production. This
extends the fail-closed discipline in [[Research Brief Is the Project's
Creative Input]] from the writing to the edit itself.

## Evidence

- scripts/research.py::production_directives; projects/<id>/research_influence.json; tests/test_longform_production.py::ProductionDirectiveTest,StoryboardDirectiveTest

## Related

- [[Research Brief Is the Project's Creative Input]]
- [[Long-Form Renders Are Assembled Piece by Piece]]
- [[Scene Prompts Compiled From Structured Direction, Not Written]]

*Captured 2026-10-03T03:19:12.858093+00:00 by content-machine-agent. Confidence: VERIFIED.*
