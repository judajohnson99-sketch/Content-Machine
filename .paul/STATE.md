---
description: "content-machine — current position and resume point"
type: ProjectState
about: "content-machine"
---

# Project State

The single resume file.

## Current Position

Milestone: autonomous production, proven (branch
claude/autonomous-production-soxl7z). Done and validated through the
dashboard with a stand-in PC worker and stand-in ComfyUI: a goal became a
2.5-hour sleep video (9000s, 1920x1080, QC PASS 15/15) with 36 PC-generated
scenes plus 2 owner photos mixed in, seven motion types, a 1125s unique cycle
looped by stream copy, music plus rain/cabin/wind ambience, and a 9000s
Kdenlive project. The production parked while images were queued for the PC
and celery beat resumed it when they landed. Job reuse now copies assets into
the new project; the PC worker route is preferred when its heartbeat is live;
the 3GB ComfyUI template is two-pass with an out-of-memory fallback. With no
LLM reachable, goal, brief, visual direction and sound design fall back to
rules from the concept's own words. Full suite 893 OK, webapp 150, frontend 86.

Blockers (owner): PC worker checkout/agent must be updated to this branch;
celery must run with -B on the VPS; research needs a reachable search route
(SearXNG, or egress to DuckDuckGo/YouTube) or the gate stays blocked;
visuals and audio need a human grade.

Next: merge this branch with the other session's work, deploy to the VPS and
PC, then a real-GPU run from the dashboard.

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

Latest: branch claude/autonomous-production-soxl7z pushed, unmerged. It
contains claude/project-thread-mupl5p (research routing) plus GPU routing,
motion/long-form, research-for-every-production, rules fallbacks and owner
"mixed" visuals. Next: coordinate the merge with the other session.
