---
description: "An automated content business that researches demand, creates and publishes content, learns from performance, and monetizes — with the operator setting direction rather than doing the manual work."
type: Project
about: "content-machine"
---

# content-machine

## What This Is

An automated content business. It researches what people are interested in,
decides what content to make, creates it, publishes it, and learns from what
performs. The goal is for it to handle most of the day-to-day content pipeline
on its own while the operator provides overall direction and makes the
important decisions.

Today the repository implements one slice of that loop — Create: a
deterministic pipeline turning images + audio into a YouTube-ready package,
stopping at a human review gate. Research, Decide, Publish, and Learn are the
work ahead. The system is built outward from what already works rather than
replaced.

## Core Value

An automated content business that researches demand, creates and publishes
content, learns from performance, and eventually monetizes it — with the
operator mostly setting direction instead of doing all the work manually.

## Current State

| Attribute | Value |
|-----------|-------|
| Type | Application |
| Version | 0.0.0 |
| Status | Initializing |
| Last Updated | 2026-08-30 |

## Requirements

### Core Features

The five capabilities of the loop:

1. **Research** — find what people are interested in and identify content opportunities.
2. **Decide** — evaluate and prioritize what to create next.
3. **Create** — generate the actual content package using the existing pipeline. *(exists today)*
4. **Publish** — publish to YouTube and eventually other platforms.
5. **Learn** — collect performance data and use it to improve future research and decisions.

Monetization is a milestone outcome of these five working, not a sixth stage.

### Validated (Shipped)

- [x] Create — deterministic render pipeline (images + audio + spec → H.264/AAC MP4), QC probe, publication package, fail-closed review gate
- [x] Knowledge layer — committed Obsidian vault (`knowledge/`) with a derived graph
- [x] Provider adapter architecture — router + `configured`/`health`/`generate` interface
- [x] Offline test policy — 132 tests, FakeProvider and local stand-ins

### Active (In Progress)

None yet — first plan not created.

### Planned (Next)

- [ ] YouTube API/OAuth credential setup (channel exists; credentials do not)
- [ ] Publish stage — connect the existing Create pipeline to a real publishing destination
- [ ] Learn stage — capture publishing and performance data where the next cycle can read it
- [ ] Research stage — turn signals into candidate topics
- [ ] Decide stage — rank candidates into a queue
- [ ] Always-on VPS deployment with scheduled unattended operation
- [ ] VPS-capable generation fallback for when the local PC/ComfyUI is unavailable

### Out of Scope

- Rebuilding or replacing the existing Create pipeline — build outward from it
- A persistent datastore (SQL or otherwise) — JSON-on-disk stays until the architecture actually requires more
- Optimizing for views, revenue, or publishing volume in the first milestone
- Multi-platform publishing beyond YouTube in the first milestone

## Target Users

**Primary:** The operator (project owner) — sets direction, approves at the
review gate, makes the important calls. Does not want to run the day-to-day
pipeline by hand.

**Secondary:** The eventual content audience, reached via YouTube.

## Context

**Business Context:**
The project is a content business, not a tool. Success is a loop that runs
without manual intervention and improves from its own results. Monetization
follows once the loop is proven; it is not chased first.

**Technical Context:**
Python 3.12 CLI (`./content-machine`) with modules for render, audio, qc,
generation, experiment, knowledge, and project lifecycle. Standard library
only on the render/generation path; ffmpeg does the media work. Providers are
adapters behind a router. Run state lives in `projects/<id>/metadata.json` and
`experiments/concepts.json`. Two separate graphs exist — a code graph
(`graphify-out/`) and a knowledge graph (`knowledge/graphify-out/`).

## Constraints

### Technical Constraints

- Standard library only on the render/generation path; ffmpeg does the media work. No image or HTTP dependency there.
- Providers are adapters — vendor specifics stay inside the provider class.
- No hard-coded endpoints or secrets. `COMFYUI_URL` unset means "the PC is off" — never default it to localhost.
- The local PC is optional higher-quality compute (ComfyUI). When unavailable the system falls back to a VPS-capable generation provider rather than halting the pipeline.
- The knowledge graph is a lens, never the operational database. No code path reads run state from `graph.json`.
- New behaviour needs a test in `tests/`; external services are mocked or served by a local stand-in, never contacted for real.
- Target runtime is an always-on VPS so the system operates when the PC is off.
- JSON-on-disk persistence for now; introduce a datastore only when the architecture actually requires it.

### Business Constraints

- **Monthly operating cost as close to $0 as realistically possible.** Paid providers stay optional, off by default, and used only when there is no viable free or local alternative. The VPS is an existing cost, not a licence to add paid services.
- YouTube channel exists; API/OAuth credentials are an upcoming setup task, not an available resource.
- No hard launch date — getting the architecture and automation right outranks shipping fast.
- Cadence starts conservative; the real number is determined after the pipeline is proven.

### Compliance Constraints

- Audio licensing is verified, not assumed. Audio whose rights cannot be established is refused.
- Generated plates are abstracts and are never described as photographs.
- `provenance.images.production_grade` is set only by a human. Code may establish the negative, never the positive.
- The review gate is fail-closed: an absent claim blocks review. "Nobody decided" is not "no objection".

## Key Decisions

| Decision | Rationale | Date | Status |
|----------|-----------|------|--------|
| Build outward from the existing Create pipeline rather than rebuilding | Create already works and is tested; the gap is the rest of the loop | 2026-08-30 | Active |
| First milestone is the full loop end-to-end, not Publish in isolation | Proving autonomy requires the loop to close; Publish alone proves nothing feeds back | 2026-08-30 | Active |
| Always-on VPS as deployment target; local PC as optional better compute | The system must operate when the PC is off | 2026-08-30 | Active |
| Defer a persistent datastore | JSON-on-disk is sufficient at current scale; adding storage early is speculative | 2026-08-30 | Active |
| Enterprise plan audit enabled | Hard non-negotiables (fail-closed gate, human-only claims, spend limits) need a structural check between PLAN and APPLY | 2026-08-30 | Active |
| Keep the paid-provider spend gate intact despite the VPS fallback requirement | An unattended run must not start spending because something local hiccuped; which provider fills the VPS slot is a planning question | 2026-08-30 | Active — unresolved tension, see Blockers |

## Success Metrics

First milestone proves the loop works. Numeric performance targets are set
from real data afterwards, not guessed now.

| Metric | Target | Current | Status |
|--------|--------|---------|--------|
| One piece of content completes Research → Decide → Create → Publish → Learn | 1 full cycle | 0 | Not started |
| Manual steps required in that cycle | Only the safety/review gate approval | n/a | Not started |
| Publishing + performance data stored where the next cycle can read it | Yes | No | Not started |
| Monthly operating cost | ~$0 beyond existing VPS | n/a | Not started |
| Test suite passing (`./content-machine test`) | 100% | 132 passing | On track |

## Tech Stack / Tools

| Layer | Technology | Notes |
|-------|------------|-------|
| Language | Python 3.12 | Standard library only on render/generation path |
| CLI | `./content-machine` wrapper | init / validate / run / status / test / knowledge |
| Media | ffmpeg | Does all media work; no image library dependency |
| Image generation | ComfyUI via `COMFYUI_URL` | Local PC, optional higher-quality compute |
| Generation fallback | TBD — VPS-capable provider | Must not violate the paid-provider-off-by-default rule |
| Publishing | YouTube Data API (OAuth) | Credentials not yet set up |
| Persistence | JSON on disk | `projects/<id>/metadata.json`, `experiments/concepts.json` |
| Knowledge | Obsidian vault + derived graphify graph | `knowledge/` is canonical; graph is a lens |
| Code intelligence | graphify | `graphify-out/`, query-first PreToolUse hook |
| Deployment | Always-on VPS (target) | Currently local |

## Specialized Flows

See: .paul/SPECIAL-FLOWS.md

Quick Reference:
- `/graphify` → any codebase question (hook-enforced)
- `superpowers:test-driven-development` → new behaviour in `scripts/`
- `superpowers:verification-before-completion` → declaring work done (`./content-machine test`)
- `superpowers:systematic-debugging` → diagnosing a failure
- `superpowers:brainstorming` → shaping an unspecified capability (optional)
- `/claude-api` → adding Anthropic API calls
- Research/Decide phase aids: `/last30days`, `/outlier-video-pulse`, `/deep-channel-analysis`, `/creator-breakout-detector`, `/audience-bullseye-builder`, `/hook-machine` (optional, spends credits)

## Links

| Resource | URL |
|----------|-----|
| Repository | local — /root/projects/content-machine |
| Production | TBD |
| Documentation | README.md, AGENTS.md, CLAUDE.md |

---
*PROJECT.md — Updated when requirements or context change*
*Created: 2026-08-30*
