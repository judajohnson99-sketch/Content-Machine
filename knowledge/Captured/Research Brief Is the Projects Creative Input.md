---
title: "Research Brief Is the Project's Creative Input"
type: decision
confidence: VERIFIED
captured_at: 2026-09-19T08:10:15.781929+00:00
contributor: "content-machine-agent"
evidence: "scripts/research.py save_brief/research_project/findings_digest; scripts/creative.py research_section/_mood_pad_layer; scripts/project.py run_research/run_creative; tests/test_research.py, tests/test_creative.py, tests/test_project.py ResearchBriefWiringTest"
tags:
  - knowledge/captured
  - knowledge/decision
---

# Research Brief Is the Project's Creative Input

**A per-project research brief (niche, intent, likes/dislikes, optional YouTube seeds) drives competitor research whose findings feed creative direction and audio design, with observations, interpretations and hypotheses kept distinguishable and neither brief nor findings ever required.**

**Research became an input a human gives a project, not a corpus the pipeline
happens to hold.**

`research/briefs/<video_id>.json` records what the operator wants: niche,
creative intent, likes, dislikes and optional YouTube channel/video seeds. From
it, `research.research_project()` searches through the *same*
`subject_research.SearchProvider` seam (not a second one) and writes
`research/findings/<video_id>.json`.

## Two research artifacts, two failure contracts

They are deliberately not symmetrical, and `run_research` runs both:

- **Subject research** is required and fails closed for concepts flagged
  `requires_subject_research` - narration must not state facts nobody sourced.
- **Competitor research is additive.** No brief, or no configured search
  provider, is not a stage failure; most projects will have neither. Only a
  search that was *attempted* and came back under `MIN_SOURCES` is refused,
  and even then the stage records `competitor_research: SKIPPED` and
  continues.

## Provenance survives the trip into the prompt

A finding carries `kind`: `observation` (traceable to one search result, with
`source_url`), `interpretation` (a deterministic frequency count across >=2
sourced observations - never an LLM editorialising), or `hypothesis` (which
`research_project` never manufactures, because counting what was found is not
guessing about what was not tested). `findings_digest()` tags every prompt
line `[topic/kind]`, and the brief prompt tells the model explicitly that an
interpretation is not a fact about any single source.

## Audio stops being a placeholder tone

`generate_brief` now also returns `audio_mood`, and
`build_audio_composition(mood=..., findings=...)` maps it to a chord colour
for the new rights-clean `pad` provider - three slowly and independently
tremolo'd sine voices, an evolving bed rather than one flat drone - layered
under the existing noise/rain bed with fades. Where no mood was given, the
findings' own audio-topic language is used; where there is neither, the
composition is byte-for-byte what it was before. `tts_required` and the
blocked/partial requirements are untouched: adding an unrequested layer to
narration would be a bigger change than "richer than one tone" calls for.
`audio.compose()` returns a `quality` block that names a single static layer
as likely-monotonous - a warning in the manifest, never a refusal to render,
since that is a design judgement and not a rights or correctness one.

## Evidence

- scripts/research.py save_brief/research_project/findings_digest; scripts/creative.py research_section/_mood_pad_layer; scripts/project.py run_research/run_creative; tests/test_research.py, tests/test_creative.py, tests/test_project.py ResearchBriefWiringTest

## Related

- [[Produce Chain and Reviewable Deliverable]]
- [[Rights and Licensing]]

*Captured 2026-09-19T08:10:15.781929+00:00 by content-machine-agent. Confidence: VERIFIED.*
