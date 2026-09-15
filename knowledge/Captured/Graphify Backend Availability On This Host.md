---
title: "Graphify Backend Availability On This Host"
type: constraint
confidence: VERIFIED
captured_at: 2026-08-30T04:32:16.596254+00:00
contributor: "claude-code"
evidence: "measured 2026-08-30: pip list in /root/.local/share/pipx/venvs/graphifyy shows no anthropic and no openai package; extract --backend gemini and --backend claude both aborted with 'the X package is required for this backend but is not installed'"
tags:
  - knowledge/captured
  - knowledge/constraint
---

# Graphify Backend Availability On This Host

**claude-cli is the only usable Graphify backend here; every API backend fails on a missing SDK package.**

Graphify is installed via pipx. Its virtualenv carries the extraction and
clustering machinery but **no LLM SDK**: neither `anthropic` nor `openai` is
present. Both API backends therefore abort before dispatching a single chunk,
with `the '<pkg>' package is required for this backend but is not installed`.

The `claude-cli` backend needs no SDK. It shells out to the local `claude`
binary, so it runs on the existing Claude Code session and reports
`est. cost (~claude-cli): $0.0000`. That is why
`./content-machine knowledge refresh` defaults to it.

## Consequence

Do not "fix" a backend error by injecting an SDK into the Graphify venv. That
would add a dependency the project does not otherwise carry and would route
graph refreshes through a metered API — the exact shape of unattended spending
that Autonomy Boundaries forbids. If an API backend is ever genuinely wanted,
it is a deliberate, human-made decision, not a workaround.

Note that `claude-cli` is forced to a concurrency of one, so a refresh over a
corpus of this size takes minutes rather than seconds. That is a throughput
cost, not a correctness one.

## Evidence

- measured 2026-08-30: pip list in /root/.local/share/pipx/venvs/graphifyy shows no anthropic and no openai package; extract --backend gemini and --backend claude both aborted with 'the X package is required for this backend but is not installed'

## Related

- [[Knowledge Graph]]
- [[Autonomy Boundaries]]

*Captured 2026-08-30T04:32:16.596254+00:00 by claude-code. Confidence: VERIFIED.*
