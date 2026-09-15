---
description: "content-machine — milestone and phase structure"
type: Roadmap
about: "content-machine"
---

# Roadmap: content-machine

## Overview

An automated content business that researches what people are interested in,
decides what to make, creates it, publishes it, and learns from what performs.
The journey starts from the existing Create pipeline — the one stage that
already works — and builds outward until a single piece of content can travel
Research → Decide → Create → Publish → Learn with no manual step beyond the
safety review gate, running unattended on an always-on VPS.

Build order is not loop order. Publish comes first because it is the only
stage with a real external destination: until something actually ships and
reports numbers back, Research and Learn have nothing real to feed on.

## Current Milestone

**v0.1 Closed Loop** (v0.1.0)
Status: In progress
Phases: 0 of 5 complete

**Goal:** One real piece of content completes the full loop, and the resulting
publishing and performance data is stored where the next cycle can read it.

## Phases

| Phase | Name | Plans | Status | Completed |
|-------|------|-------|--------|-----------|
| 1 | Publish | 2 | Planning | - |
| 2 | Learn | 2 | Not started | - |
| 3 | Research | 2 | Not started | - |
| 4 | Decide | 1 | Not started | - |
| 5 | Cycle & VPS | 2 | Not started | - |

## Phase Details

### Phase 1: Publish

**Goal:** A rendered, gate-passed project can be uploaded to YouTube by the
system, with human approval as the only manual step, and the result recorded
in `publication_package.json`.
**Depends on:** Nothing (Create already produces the package)
**Research:** Likely (YouTube Data API v3 resumable upload protocol, OAuth 2.0
refresh-token flow, quota costs)

**Scope:**
- A `Publisher` adapter interface mirroring the existing `Provider` shape
  (`configured` / `health` / `publish`), so a second platform is an adapter
  rather than a rewrite
- `YouTubePublisher` — all YouTube specifics inside the class
- A `publish` CLI command that is fail-closed: refuses unless the gate verdict
  is `READY_FOR_REVIEW`, the `gate_digest` still matches, and a human has set
  `publish.approved_by_human`
- Idempotency: a project already carrying a `publication_id` is never
  re-uploaded

**Plans:**
- [ ] 01-01: Publisher adapter + YouTubePublisher + offline stand-in tests
- [ ] 01-02: `publish` CLI command, approval gate, idempotency, credential setup

**Seam already present:** `project.py` writes a `publish` block into
`output/publication_package.json` with `approved_by_human`, `published`,
`publication_id`, `published_utc`. Phase 1 fills it; it does not invent it.

### Phase 2: Learn

**Goal:** Performance data for published videos is fetched on a schedule and
stored on disk where the next Research cycle can read it.
**Depends on:** Phase 1 (needs a real `publication_id` to query)
**Research:** Likely (YouTube Analytics API vs Data API statistics; which is
free and what the quota shape is)

**Scope:**
- A performance ledger, JSON on disk, keyed by `publication_id`
- A `learn` CLI command that refreshes it
- Snapshots over time, not a single reading — a video's numbers move
- Explicitly NOT a datastore; stays JSON until the architecture forces change

**Plans:**
- [ ] 02-01: Performance ledger schema + fetch adapter + offline tests
- [ ] 02-02: `learn` CLI command + snapshot scheduling + surfacing to Research

### Phase 3: Research

**Goal:** The system produces candidate topics from external signals, written
as concept records the existing scorer can already rank.
**Depends on:** Phase 2 (performance history is one of the signals)
**Research:** Likely (which free signal sources are viable and permitted —
YouTube search/trends, Reddit, HN; ToS and rate limits)

**Scope:**
- A signal-source adapter interface; free sources only
- Candidate topics emitted in `experiments/concepts.json` schema, including
  the `procedural_visuals_acceptable` flag that decides VPS viability
- Findings captured into `knowledge/` via the existing knowledge layer
- Honest labelling: assumptions stay assumptions until measured

**Plans:**
- [ ] 03-01: Signal-source adapter + one free source + offline tests
- [ ] 03-02: Candidate synthesis into concept records + knowledge capture

### Phase 4: Decide

**Goal:** The top-ranked concept is selected automatically and scaffolded into
a project without a human choosing it.
**Depends on:** Phase 3 (needs candidates to rank)
**Research:** Unlikely (extends existing internal scoring)

**Scope:**
- Auto-selection over the existing `experiment.ranked()` output
- A VPS-awareness filter: when the PC is off, concepts requiring depicted
  imagery are not selectable — `require_depicted` would fail routing
- Handoff into `project init` / `cmd_scaffold`
- Selection rationale recorded, so a bad pick is auditable afterwards

**Plans:**
- [ ] 04-01: Auto-selection + capability filter + scaffold handoff

### Phase 5: Cycle & VPS

**Goal:** One real piece of content completes Research → Decide → Create →
Publish → Learn unattended on the VPS, with only the review-gate approval
performed by a human.
**Depends on:** Phases 1-4 (orchestrates all of them)
**Research:** Unlikely (internal orchestration; VPS setup is ops, not discovery)

**Scope:**
- A `cycle` command running the loop end-to-end, resumable and idempotent
- Scheduled unattended execution on the VPS
- The milestone's proving run — one real video, start to finish
- Cadence stays conservative; the real number comes from this run's data

**Plans:**
- [ ] 05-01: `cycle` orchestrator + resumability + offline full-loop test
- [ ] 05-02: VPS deployment, scheduling, and the first real end-to-end run

## Resolved During Planning

**VPS image generation — already solved, no phase required.**
`generation.py` ships the exact architecture requested: `DEFAULT_ORDER =
("comfyui", "procedural", "api")`. ComfyUI is preferred and free; the
procedural provider is the always-on VPS floor, needing no GPU, network, or
credentials; the paid API is last and inert without both `IMAGE_API_URL` and
`IMAGE_API_KEY`. `Router.candidates()` filters on `produces_depicted`, so a
request needing depicted imagery fails loudly rather than escalating to a paid
provider.

**Residual capability limitation (named, not solved):** on the VPS, only
concepts whose `procedural_visuals_acceptable` is true can run. Concepts
requiring depicted imagery wait for the PC. This constrains Research and
Decide — Phase 4 carries the filter — and is not a defect in the provider
layer.

---
*Roadmap created: 2026-08-30*
*Last updated: 2026-08-30*
