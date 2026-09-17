---
description: "content-machine — current position and resume point"
type: ProjectState
about: "content-machine"
---

# Project State

The single resume file. Update `## Current Position

Milestone: Web Control Center visual/UX overhaul + real ComfyUI worker path (plan and per-phase handoffs: ~/.claude/plans/effervescent-snuggling-lighthouse.md). The roadmap milestone v0.1 Closed Loop (.paul/ROADMAP.md, Phase 1 Publish) waits behind it.
Done: Phase 2/3 control center, produce chain, remote GPU worker v0.1 (WORKER_HANDOFF.md). 2026-09-17: concepts API + New Production; design system and redesign of shell, dashboard, new production, workspace, deliverable/assets, review center (production-grade claim in the UI); visuals/scenes defer to the GPU queue (exit 2, WAITING_FOR_GPU_WORKER); worker readiness states from heartbeat (ComfyUI, GPU, checkpoints); low-VRAM upscale workflow as default; reference concept catalogue (kind=reference, 8 concepts); system/readiness, gpu-jobs, pipeline-runs and visual-grade endpoints; visual QA in the browser.
Blockers: the real ComfyUI canary needs the PC: pull the updated checkout, start the SSH tunnel and the agent (WORKER_RUNBOOK.md). Canary job ca317f798759a16e (project moonlit-victorian-glasshouse-20260917, 1920x1080 via 680x384 latent) is queued and will render when home-gpu-01 comes online; then "Continue production" in its workspace.
Next: run the canary on the PC, verify the image in the workspace, continue production to a 60 s preview render, then decide production_grade in the Review Center. Then v0.1 Publish.

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

Last session: 2026-09-17 — UX overhaul + ComfyUI path. All suites green
(457 Python, 79 pytest, 28 vitest, tsc clean). Dev services for browser QA
were started in the background (Django :8010, Celery, worker control plane
:8788); `qa/` holds the screenshots and is gitignored.
Stopped at: waiting for the PC action for the real canary (see Blockers).
Next action: the "Next" line above.
