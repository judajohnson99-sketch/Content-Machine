---
title: "Provenance and Production-Grade Claims"
type: decision
confidence: VERIFIED
tags:
  - knowledge/doctrine
---

# Provenance and Production-Grade Claims

**A machine may not certify an asset as production-grade on a human's behalf.**

`provenance.images.production_grade` is set by a person, explicitly. Code is
allowed to establish only the negative: if an abstract-only provider made the
plate, the claim is `false`. That asymmetry is what makes the verdict at the
[[Publication Gate]] mean something.

Metadata alone is not trusted. Procedural plates are provenance-stamped at
generation time and flat fills are measured from the pixels, so hand-editing
`metadata.json` cannot relabel a placeholder as a real asset.

Generated plates are **abstracts, never photographs**. Describing one as a
photograph is a factual misstatement about the artefact, and for archival-looking
material it also risks the disclosure trigger in
[[YouTube Synthetic Content Disclosure]].

Source of truth: `scripts/make_visuals.py` (stamping and forensics),
`provenance` in `projects/<id>/metadata.json`.

## Related
- [[Publication Gate]]
- [[Autonomy Boundaries]]
