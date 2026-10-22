---
description: "content-machine — current position and resume point"
type: ProjectState
about: "content-machine"
---

# Project State

The single resume file.

## Current Position

Milestone: Perceptual Quality System, phase 1 - done. Every render now gets
`output/quality_report.json` from `scripts/quality.py`: scene imagery vs the
direction's palette/lighting and the viewing profile, scene-to-scene
continuity, rendered flashes/seams, the delivered soundtrack, dynamics
(spikes, dropouts, drift) and loop repetition, all judged against the
production's intent. Findings inform review (Workspace card with Re-assess,
Review Center evidence); only objective delivered-video defects and a stale
report reach the gate. `./content-machine quality <id>` and the `quality`
web stage re-assess without re-rendering. Validated on a real 3-minute
Dreamdrip production and a degraded copy that old QC passes everywhere.

Blockers unchanged (need the owner): no working search provider for sourced
research; no depicted-image provider, so plates are abstracts. Scene
depiction (vision model) reports "not evaluated" until QUALITY_VISION_URL and
QUALITY_VISION_MODEL point at a local Ollama vision model.

Next: point the depiction check at a local vision model on the GPU PC and
calibrate it on depicted renders; validate a narrated production (needs the
piper voice installed); then the earlier next steps (top up a search
provider, owner-media selection from `library/`).

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

Latest request: Phase 1 of the Perceptual Quality System - evaluate creative
quality, not just technical validity. Built `scripts/quality.py`, wired into
`project.run_pipeline` (stage 4b), the gate (`quality.blockers`),
`status_report`, the package, a `quality` CLI/web stage, a
`/quality-report/` endpoint and a dashboard QualityPanel. Thresholds were
calibrated on the real production (vividness instead of raw chroma; sleep
spike 6 LU; loop detection on masked spectral shape). Validation projects
`dreamdrip-qa-01` and `dreamdrip-qa-bad` are local, untracked. `graphify` is
not installed here, so the code and knowledge graphs were not refreshed.
