---
title: "Publication Gate"
type: decision
confidence: VERIFIED
tags:
  - knowledge/doctrine
---

# Publication Gate

**`READY_FOR_REVIEW` is the hard stop, and the gate that reaches it is
fail-closed: an absent claim blocks review, because "nobody decided" is not
"no objection".**

Every stage up to packaging is safe to run unattended. Nothing downstream of
the gate is automated. The gate recomputes from the artefacts on disk rather
than trusting `metadata.json`, and stamps a `gate_digest` over every input the
verdict depended on — so a verdict is only meaningful for the exact assets it
was computed against, and changing an asset invalidates it.

The three things that can hold a project at `NEEDS_ATTENTION` are an undeclared
production-grade claim ([[Provenance and Production-Grade Claims]]), audio whose
rights are not established ([[Rights and Licensing]]), and a QC fault such as the
black-frame trip described in [[Dark Visual Luminance Floor]].

Source of truth: the gate implementation in `scripts/project.py`; per-project
verdicts in `projects/<id>/metadata.json`.

## Related
- [[Autonomy Boundaries]]
- [[Provenance and Production-Grade Claims]]
- [[Experiment Log]]
