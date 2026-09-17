---
title: "Human Review Decision Design"
type: decision
confidence: VERIFIED
captured_at: 2026-09-17T11:56:13.487879+00:00
contributor: "claude-sonnet-5"
evidence: "scripts/project.py (record_review_decision, cmd_approve, cmd_reject); tests/test_project.py::ReviewDecisionTest; webapp/apps/review/"
tags:
  - knowledge/captured
  - knowledge/decision
---

# Human Review Decision Design

**record_review_decision() is the sole writer of metadata.json.publish.approved_by_human: reviewer identity is always server/session-sourced, gate_blockers() is re-checked live on every call, and a rejection revokes any prior approval.**

Phase 3 of the Web Control Center added \`record_review_decision()\` in
\`scripts/project.py\`, called by both the CLI (\`cmd_approve\`/\`cmd_reject\`,
a required \`--reviewer\` flag) and the Django \`apps/review\` endpoint
(\`POST /api/v1/projects/{id}/review-decisions/\`) - one implementation, two
callers, proven equivalent by a CLI/API parity test.

Design points that are not obvious from the code alone:

- **The gate is re-checked on every decision, not just approval.** Both
  \`expected_digest\` freshness (must match a live \`gate_digest()\` call) and
  \`gate_blockers()\` are recomputed inside the function itself, under
  [[Publication Gate]]'s \`project_lock\`. Approval additionally requires
  \`gate_blockers()\` to be empty; rejection does not, since flagging a
  broken project must always be possible - but a *stale* digest blocks
  both, deliberately, so a decision is never recorded against a snapshot
  the reviewer wasn't actually looking at.
- **Reviewer identity can never come from the request body.** The CLI
  requires \`--reviewer\` with no default; the DRF view takes it from
  \`request.user.email\` (falling back to the username), never from
  \`request.data\`. \`record_review_decision()\` itself also refuses an
  empty/blank reviewer, so a third caller couldn't slip a system identity
  through either surface.
- **A rejection revokes prior approval.** \`publish.approved_by_human\` is
  set to \`decision == "approved"\` unconditionally, not just written to
  \`True\` on approval - "approved" means "the most recent human decision
  was approval", not "was ever approved."
- **This never touches \`output/publication_package.json\`'s inert \`publish\`
  block** (still hardcoded false/null, written only by \`run\`/\`produce\`) or
  [[Provenance and Production-Grade Claims]]'s \`production_grade\` field -
  publishing and production-grade declaration remain separate, unbuilt-on
  capabilities.
- A static AST test (\`tests/test_project.py::ReviewDecisionTest\`) asserts
  \`record_review_decision\` is only ever called from \`cmd_approve\`/
  \`cmd_reject\` in \`scripts/project.py\` - never from \`run_produce\`,
  \`run_pipeline\`, or the Celery dispatcher.

## Evidence

- scripts/project.py (record_review_decision, cmd_approve, cmd_reject); tests/test_project.py::ReviewDecisionTest; webapp/apps/review/

## Related

- [[Publication Gate]]
- [[Provenance and Production-Grade Claims]]
- [[Autonomy Boundaries]]

*Captured 2026-09-17T11:56:13.487879+00:00 by claude-sonnet-5. Confidence: VERIFIED.*
