# Specialized Flows

**Project:** content-machine
**Created:** 2026-08-30
**Last Updated:** 2026-08-30

---

## Project-Level Dependencies

Skills and commands that apply to this project's work.

| Work Type | Skill/Command | Priority | When Required | Notes |
|-----------|---------------|----------|---------------|-------|
| Any codebase question | `/graphify` | required | Before grepping or reading source | Already enforced by the PreToolUse hook in `.claude/settings.json`. Two graphs exist — code (`graphify-out/`) and knowledge (`knowledge/graphify-out/`); do not confuse them |
| New behaviour in `scripts/` | `superpowers:test-driven-development` | required | Before writing implementation | CLAUDE.md: new behaviour needs a test in `tests/`; external services mocked or served by a local stand-in, never contacted for real |
| Declaring work done | `superpowers:verification-before-completion` | required | Before any completion claim | Must include `./content-machine test` (132 tests, ~85s) |
| Diagnosing a failure | `superpowers:systematic-debugging` | required | Unknown cause, intermittent behaviour, or a regression | Precedes domain work — do not patch before the cause is established |
| Shaping a new capability | `superpowers:brainstorming` | optional | Before `/paul:plan` on an unspecified stage | Most relevant to Research and Decide, which are the least specified |
| Adding LLM / Anthropic API calls | `/claude-api` | required | Before writing any Anthropic API integration | Never answer model, pricing, or limit questions from memory. Skip only if another provider is being worked on |

**Priority Legend:**
- `required` - Gap documented if not invoked during UNIFY
- `optional` - Informational only, no gap logged

---

## Phase Overrides

Skills needed for specific phases beyond project-level defaults.

These map onto the Research and Decide stages — the two capabilities not yet
built. Phase numbers are assigned during `/paul:plan`; the mapping is recorded
here by stage until then.

| Phase | Additional Skills | Notes |
|-------|-------------------|-------|
| Research (TBD) | `/last30days` | Reddit, HN, X, YouTube, Polymarket over the last 30 days. Works out of the box, spends no credits — the cheapest research aid available |
| Research (TBD) | `/outlier-video-pulse` | Competitor digest ranked by outlier score. Requires Sandcastles MCP; spends no credits |
| Research (TBD) | `/deep-channel-analysis` | Channel teardown — winning topics, hooks, formats. Requires Sandcastles MCP |
| Research (TBD) | `/creator-breakout-detector` | Locates a creator's inflection point and reverse-engineers the cause. Requires Sandcastles MCP; spends no credits |
| Decide (TBD) | `/audience-bullseye-builder` | Niche definition as concentric ICP rings plus a per-week content mix. Interview-driven, needs no external data |
| Decide / Create (TBD) | `/hook-machine` | **Optional — spends credits.** Data-driven hook scoring rubric. Weigh against the ~$0 monthly cost target before use |

### Two standing caveats on the above

1. **Sandcastles MCP is not currently configured.** Four of these six depend on
   it. Treat standing it up as a planning question, never as an assumption a
   plan may rely on. `/last30days` and `/audience-bullseye-builder` are the two
   that work without it.
2. **These are manual research aids for the operator, not the implementation.**
   A skill invoked in an interactive session does not run unattended on a VPS at
   3am. They are useful for *designing* the Research and Decide stages; the
   stages themselves must still be code. No plan may treat a skill invocation as
   the delivered automation.

---

## Templates & Assets

Reference templates, assets, or external resources for this project.

| Asset Type | Location | When Used |
|------------|----------|-----------|
| Project instructions and non-negotiables | `CLAUDE.md` | Required reading before any plan — encodes decisions that must not be simplified away |
| Architecture and conventions | `AGENTS.md` | Required reading before any plan touching pipeline stages, providers, or the knowledge layer |
| User manual and lifecycle | `README.md` | Required reading before any plan touching the CLI or project lifecycle |
| ComfyUI workflow reference | `config/comfyui_workflow.example.json` | When working on image generation or the ComfyUI provider |
| Environment contract | `.env.example` | When adding configuration; no hard-coded endpoints or secrets |

---

## Verification Checklist

During UNIFY, confirm:
- [ ] Required skills were invoked for matching work types
- [ ] `./content-machine test` was run and passed
- [ ] Gaps documented in STATE.md Deviations section
- [ ] Patterns updated if intentional deviation is now standard

### Skill Audit Template

Use this during UNIFY to track invocations:

| Expected Skill | Invoked? | Gap? | Notes |
|----------------|----------|------|-------|
| `/graphify` | ✓/○ | Yes/- | |
| `superpowers:test-driven-development` | ✓/○ | Yes/- | |
| `superpowers:verification-before-completion` | ✓/○ | Yes/- | |
| `superpowers:systematic-debugging` | ✓/○ | Yes/- | Only if a failure was diagnosed |
| `/claude-api` | ✓/○ | Yes/- | Only if Anthropic API code was written |

---

## Amendment History

| Date | Change | Reason |
|------|--------|--------|
| 2026-08-30 | Initial creation | Project setup during `/paul:init` |

---
*Generated by `/paul:flows` or `/paul:init`*
*Reference: @references/specialized-workflow-integration.md*
