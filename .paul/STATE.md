---
description: "content-machine — current position and resume point"
type: ProjectState
about: "content-machine"
---

# Project State

The single resume file. Update `## Current Position` and `## Session
Continuity` at every task boundary (done, blocked, before compaction or a
model switch). Current Position holds plain sentences only: `knowledge
context` prints its first 8 non-list lines. Never paste logs, diffs or test
output here. Per-video run state stays in `projects/<id>/metadata.json`;
durable knowledge goes through `./content-machine knowledge capture`.

## Current Position

Milestone: Web Control Center over the pipeline (plan and per-phase handoffs: ~/.claude/plans/effervescent-snuggling-lighthouse.md). The roadmap milestone v0.1 Closed Loop (.paul/ROADMAP.md, Phase 1 Publish) waits behind it.
Done: Phase 2 Django/DRF + React control center (75411e2); Phase 3 human review-decision recording (73d0e24); produce runs the real chain and surfaces the deliverable (90e99d6); remote GPU worker v0.1 shipped and verified on hardware (WORKER_HANDOFF.md).
In progress, uncommitted as of 2026-09-17: concepts API (webapp/apps/concepts, webapp/apps/engine/services/concepts.py, frontend/src/api/concepts.ts); New Project page (frontend/src/features/new-project); scripts/envfile.py with tests/test_envfile.py; dashboard and workspace UI edits; low-token workflow docs (CLAUDE.md, AGENTS.md, .gitignore, .claude/settings.json).
Blockers: none on the current work. For v0.1 Publish: YouTube API/OAuth credentials not set up; an unattended VPS run has no image provider when the PC is off (must not weaken the spend gate).
Next: verify the uncommitted concept and New Project work (frontend `npx tsc -b` + `npx vitest run`, `cd webapp && pytest`, `./content-machine test`), fix what fails, commit as one change.

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

Last session: 2026-09-17 — low-token workflow: CLAUDE.md rewritten around
resume/model-routing/targeted-reads/milestone-refresh, AGENTS.md deduplicated,
.gitignore widened to runtime trees, project model default set to Sonnet 5,
this file made the single resume point.
Stopped at: workflow change complete. Concept/New Project work is still
uncommitted and was not verified in that session.
Next action: the "Next" line above.
