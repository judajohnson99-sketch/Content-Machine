---
description: "content-machine — current position and resume point"
type: ProjectState
about: "content-machine"
---

# Project State

The single resume file.

## Current Position

Milestone: the dashboard is the product - a production now starts from a
plain-language goal and runs to a reviewable 1080p deliverable without the
CLI. Done: goal -> concept -> project -> research brief derivation
(`scripts/goal.py`, POST /api/v1/projects/from-goal/); a piecewise renderer
that removes the 24-scene ceiling, so a 30-minute video is 120 shots with
real motion and dissolves instead of a slideshow; sourced findings now
produce production directives (shot length, movement style, dissolve length,
audio layers) that the storyboard and sound design actually apply, recorded
with their evidence in `research_influence.json` and shown in the dashboard;
procedural plates vary per scene prompt and carry real structure; Kdenlive
export, permanent deletion and "produce at full length" are dashboard
actions. Validated through the dashboard: a 90-second 1080p Dreamdrip
excerpt, QC PASS 15/15, 15 shots across 8 motions, four cleared audio
layers, editable Kdenlive archive.

Blocker (needs the owner): real sourced research cannot run on this host.
Gemini grounding returns RESOURCE_EXHAUSTED and the Anthropic key has no
credit balance, so both configured search providers refuse. Every derived
production therefore carries the new fail-closed gate blocker "research has
not run". Also outstanding: no depicted-image provider (COMFYUI_URL unset,
paid providers off), so visuals are abstracts and cannot be production-grade.

Next: top up one search provider and re-run research on
`dreamdrip-rain-ambient-music-psychedelic-202610030311` to see directives
drive the build end to end; then owner-media selection from `library/` in
the dashboard.

## Standing decisions

| Decision | Impact |
|---|---|
| Build outward from the existing Create pipeline, never replace it | Every plan extends rather than rewrites |
| First milestone is the full loop, not Publish alone | Phases must reach Learn, not stop at upload |
| Always-on VPS target; the local PC is optional better compute | Every stage tolerates the PC being off |
| Defer a persistent datastore | JSON-on-disk until architecture forces the change |
| `scripts/*.py` is the one domain layer | webapp/frontend are control surfaces, never a second authority on pipeline state |

Safety boundaries (production_grade, fail-closed gate, audio rights, paid
provider last and off, stdlib render path, graph is a lens) live in CLAUDE.md
and are not restated here.

## Deferred

| Issue | Revisit |
|---|---|
| Persistent datastore for research/publishing/performance history | When JSON-on-disk demonstrably strains |
| Multi-platform publishing beyond YouTube | After the YouTube loop is proven |
| Publishing cadence target | After the first full cycle produces real data |
| Monetization | After the loop runs unattended and consistently |

## Session Continuity

Latest request: make Content Machine a genuinely usable autonomous
production system operated from the dashboard. Built this session, all
through `scripts/*.py` as the one domain layer: `scripts/goal.py`,
`render.render_scenes_piecewise`, `research.production_directives`,
`storyboard` directive/pacing handling, a prompt-varied procedural plate
generator, `project.run_editable`, `project.delete_project`,
`project.set_project_duration`, and the web/UI surface for all of it
(GoalComposer, ResearchInfluencePanel, DeleteProduction, editable
downloads, produce-at-full-length). `SEARCH_PROVIDER=anthropic` was added to
`.env` and a second real search provider (`AnthropicSearchProvider`) written,
but neither vendor will serve a search on the current credentials. A
`claude-qa` superuser exists in webapp/db.sqlite3 purely for browser
validation. No commits.
