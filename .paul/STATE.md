---
description: "content-machine — current position and resume point"
type: ProjectState
about: "content-machine"
---

# Project State

The single resume file.

## Current Position

Milestone: GPU-free usability, on branch `claude/gpu-free-validation`. The
unified visual asset workflow (library, stock, planned generation; `supervised`
approval; graduated autonomy defined and disabled) now has a no-GPU path:
`./content-machine assets fill <id> --actor <you>` (and the workspace button)
pins approved library media on shots without a picture, never approving stock,
downloading or generating. A concept that requires depicted imagery completes
from the library alone: scenes, audio, render, QC PASS, real `melt` Kdenlive
export. `run_scenes` now points at this path when it cannot resolve a shot.

Validated with mocks/fixtures only: Pexels/Pixabay adapters (scripted vendor
responses). NOT validated: live Pexels/Pixabay searches (no keys; the
environment network policy denies the provider hosts); the creative stage (needs
an LLM key and the anthropic SDK, absent here); 768x432 on the GTX 1060 (desktop
off; checklist in WORKER_RUNBOOK.md); the Kdenlive editor opening a project.
WORKER_VISION stays unset.

Tooling: project skills (shared, web, `cm-*`) are vendored in `.claude/skills/`; see its REGISTRY.md.

Next: allow the provider hosts and add the two free keys, then one search each;
when the PC is on, follow the reconnect checklist in WORKER_RUNBOOK.md; then
vision calibration and the creator-workflow next steps (update the PC agent, a
working search provider, repair job output ownership).

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

Latest request: keep developing without the GTX 1060 or ComfyUI. Found that a
depicted-image concept stopped at `scenes` with no GPU and no pointer to the
library path; added `assets fill` (CLI, API, workspace button), a hint in the
scenes failure, tests (fill behaviour, GPU-free production end to end, real
melt export) and the reconnect checklist. Knowledge graph not refreshed again
by request.
