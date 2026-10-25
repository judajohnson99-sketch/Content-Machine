---
description: "content-machine — current position and resume point"
type: ProjectState
about: "content-machine"
---

# Project State

The single resume file.

## Current Position

Milestone: Unified Visual Asset Discovery & Generation, built on branch
`claude/unified-visual-asset-discovery` and ready for review on PR. One
per-scene workflow (`scripts/visual_assets.py`) ranks the owner library, stock
photography/footage (Pexels, Pixabay, Unsplash via official APIs,
`scripts/stock_providers.py`) and a planned ComfyUI generation together
(`scripts/asset_ranking.py`), under an approval policy
(`scripts/asset_licensing.py`, only `supervised` enabled). Choosing becomes an
ordinary owner-media pin on the scene, so render, Review Center, package and
the Kdenlive archive carry source, licence, creator, retrieval date and
approval. Stock stays unapproved (`commercial_use` false) until a person
approves first use; a GPU that is off defers a generation, never fails search.
Workspace panel: "Find a picture for each shot".

NOT validated: no Pexels/Pixabay/Unsplash key exists in this environment, so
every provider call was exercised against scripted vendor responses only; no
real ComfyUI render ran (GPU PC off); `melt` is not installed here so the
Kdenlive project was built and inspected but not MLT-verified. WORKER_VISION
stays disabled. Earlier blockers stand (search provider for sourced research,
PC agent update, idempotent-job reuse defect, vision calibration on hardware).

Tooling: project skills (shared, web, `cm-*`) are vendored in `.claude/skills/`; see its REGISTRY.md.

Next: add real API keys and run one search per provider; when the GPU PC is on,
choose a generated candidate for one scene and verify 768x432 on the GTX 1060
and follow WORKER_RUNBOOK.md "Vision on the GPU worker" (calibrate with
`vision_calibration.py run --apply`, label real renders, verify ComfyUI `/free`
and VRAM fit); install `melt` and rerun the Kdenlive export; then the
creator-workflow next steps (update the PC agent, a working search provider,
repair job output ownership).

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

Latest request: implement the unified visual asset discovery and generation
milestone end to end without GPU or provider credentials. Done: stock adapters
with cache/cooldown/dedupe, unified ranking with explanations, approval policy,
library-first reuse with near-duplicate collapse, deferred generation and
per-scene regeneration, pinned scene selection with lineage, licence trail into
package/Review Center/Kdenlive, API endpoints, workspace panel, tests, docs and
a vault decision note. Graphs were not refreshed: `graphify` is not installed
here. PR opened, not merged.
