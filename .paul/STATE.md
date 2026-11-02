---
description: "content-machine — current position and resume point"
type: ProjectState
about: "content-machine"
---

# Project State

The single resume file.

## Current Position

Milestone: post-merge integration validation of PR #4 (unified visual asset
discovery), on main. One per-scene workflow (`scripts/visual_assets.py`) ranks
the owner library, stock (Pexels, Pixabay; Unsplash optional and off) and a
planned ComfyUI generation under the `supervised` approval policy; graduated
autonomy is defined and disabled. Required credits are appended to the package
description and fail closed.

Validated here: a real `melt` 7.22 + frei0r export of a mixed production
(library still, stock photo, stock footage) verified, rendered through MLT,
QC PASS, licence trail present in the handoff and archive; the previously
failing Kdenlive tests pass. Code graph refreshed (`graphify update .`).

NOT validated: no Pexels/Pixabay/Unsplash key exists and the environment
network policy denies api.pexels.com, pixabay.com and api.unsplash.com (403 on
CONNECT), so no real provider search ran; the GTX 1060 PC is not connected and
no worker is enrolled, so 768x432 is unverified on hardware (procedure written
in WORKER_RUNBOOK.md, not run). The Kdenlive editor itself has not opened the
project. WORKER_VISION stays unset.

Tooling: project skills (shared, web, `cm-*`) are vendored in `.claude/skills/`; see its REGISTRY.md.

Next: allow the three provider hosts in the environment and add the two free
keys, then one search each; when the PC is on, run the "Validating 768x432"
section of WORKER_RUNBOOK.md and record peak VRAM and seconds; then vision
calibration and the creator-workflow next steps (update the PC agent, a working
search provider, repair job output ownership).

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

Latest request: post-merge validation. Installed melt and frei0r plugins in
this container (not repo changes), ran the real Kdenlive export, wrote the
Windows GPU validation procedure, refreshed the code graph. Provider and GPU
validation are blocked on network access, keys and the desktop being on. No
code changed; only WORKER_RUNBOOK.md and this file.
