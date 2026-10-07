---
title: "Non-Documentary Concepts Get Distinct Environments, Not One Reused Prompt"
type: decision
confidence: VERIFIED
captured_at: 2026-09-19T10:32:25.141931+00:00
contributor: "Claude Sonnet 5"
evidence: "scripts/creative.py generate_scene_environments; scripts/project.py run_storyboard elif branch; tests/test_project.py test_storyboard_assigns_environment_motifs_when_no_research_is_required_but_depicted_imagery_is"
tags:
  - knowledge/captured
  - knowledge/decision
  - storyboard
  - visual-diversity
  - creative
---

# Non-Documentary Concepts Get Distinct Environments, Not One Reused Prompt

**Concepts needing real depicted imagery but with no sourced facts (procedural_visuals_acceptable=false, requires_subject_research=false) now get their scene environments from generate_scene_environments, cycled across scenes, instead of one fixed base prompt reused for every scene. How many environments is reasoned per video from duration, format and viewing behaviour — never a fixed range.**

Root cause: the ambient-dogs-home-alone-20260919 diagnostic render showed several near-identical beige living-room scenes. generate_scene_motifs (subject-grounded visual motifs) only fires when requires_subject_research is true, so concepts like ambient-dogs-home-alone (documentary-free, but still needing real photographic-style imagery) fell through to one fixed visual_plan.prompt reused across every scene, varied only by motion/transition.

Fix: generate_scene_environments is a sibling batched LLM call (one call per video, never per scene) that chooses distinct but visually consistent environments — the same coherent world from different angles — grounded in the concept, its [[visual_direction]] and the project's [[Research Brief Is the Projects Creative Input|research brief]]/findings.

**How many is not a formula.** The first implementation asked for 4-8 scaled to scene count; that was rejected as one arbitrary number replacing another. The prompt now carries the video's real duration, publishing format and viewing behaviour and asks the model to reason about how much unique visual content *this* video needs, returning its reasoning alongside the environments. A single excellent held environment is an allowed, deliberate answer for a long passive piece — only an absolute ceiling (12, never a target) guards a malformed reply. Verified against ambient-dogs-home-alone-20260919's real concept: given 4 hours of unattended "owner presses play then leaves" viewing, the model chose exactly one environment and cited that duration and viewing behaviour as its reason. scripts/project.py::run_storyboard gained a new elif branch: when a concept is not requires_subject_research but also not procedural_visuals_acceptable, it calls generate_scene_environments and feeds the result through the existing storyboard_mod.apply_scene_motifs, exactly the wiring generate_scene_motifs already used. Cached scene_motifs are reused on rebuild without --force, same as the subject-research path.

Concepts with procedural_visuals_acceptable=true (e.g. sleep-brown-noise-dark) are unaffected — they still get no scene_motifs key, since a procedural plate needs no distinct depicted environment.

The companion repetition signal (scripts/qc.py::visual_diversity_warnings) fires only when *no* environment-reasoning step ran for the storyboard — a recorded scene_motifs key means the choice was deliberate and is trusted, however few environments it produced. It stays advisory at the technical-QC level but now also blocks review through gate_blockers; see [[Technical Validity Is Not Production Quality]] for why those are two signals rather than one.

## Evidence

- scripts/creative.py generate_scene_environments; scripts/project.py run_storyboard elif branch; tests/test_project.py test_storyboard_assigns_environment_motifs_when_no_research_is_required_but_depicted_imagery_is

## Related

- [[Research Brief Is the Projects Creative Input]]
- [[GPU Capacity Chooses the Execution Path]]

*Captured 2026-09-19T10:32:25.141931+00:00 by Claude Sonnet 5. Confidence: VERIFIED.*
