---
description: "content-machine — current position and resume point"
type: ProjectState
about: "content-machine"
---

# Project State

The single resume file.

## Current Position

Milestone: Perceptual Quality System (phases 1 and 1.5) merged with the
creator-workflow milestone, ready for review on PR. Phase 1: every render gets
`output/quality_report.json` (scene imagery vs intent, continuity, rendered
frames, soundtrack delivery/dynamics/repetition) that informs review; only
objective defects and a stale report reach the gate. Phase 1.5: a local
Ollama vision layer (`scripts/vision.py`), GPU lease/ComfyUI coordination
(`scripts/gpu_coordination.py`), worker-side judging shipped as sha-bound
advisory evidence, an 82-case constructed calibration dataset and harness,
and `scripts/vision_setup.py`. NOT validated on hardware: no model is
calibrated, and unavailable/uncalibrated/partial vision results are never
drawn as a pass.

Creator workflow (from main): the dashboard is the product - a goal becomes
a reviewable production without the CLI (goal -> concept -> project ->
research brief, piecewise renderer, research-derived production directives,
media library, Kdenlive export). Its validation blockers stand: no working
search provider for sourced research; the PC worker must be updated to the
current agent; an idempotent-job reuse defect can leave a new production
without images.

Tooling: project skills (shared, web, `cm-*`) are vendored in `.claude/skills/`; see its REGISTRY.md.

Next: when the GPU PC is on, follow WORKER_RUNBOOK.md "Vision on the GPU
worker", calibrate with `vision_calibration.py run --apply`, label real
renders, and verify ComfyUI `/free` and VRAM fit on the GTX 1060 3GB. Then
the creator-workflow next steps (update the PC agent, a working search
provider, repair job output ownership).

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

Latest request: prepare the combined Perceptual Quality System for
integration into main. Merged main into the branch (five conflicts resolved:
worker heartbeat keys, agent init, Workspace step layout, tests, this file),
reviewed phases 1 and 1.5, verified ComfyUI `/free` and the Ollama API against
upstream source, made vision batches take the GPU once, made held mode check
ComfyUI too, stopped an OK verdict with unrun evaluations looking like a
clean pass, and opened the PR (not merged). No hardware integration and no
Phase 2 work. `graphify` is not installed here, so graphs were not refreshed.
