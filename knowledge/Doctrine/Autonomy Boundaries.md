---
title: "Autonomy Boundaries"
type: decision
confidence: VERIFIED
tags:
  - knowledge/doctrine
---

# Autonomy Boundaries

**Unattended work may reach `READY_FOR_REVIEW` and stop. It may not spend money
and it may not publish.**

Safe unattended: generating assets, rendering, QC, packaging, running tests.

Requires a human: declaring an asset production-grade
([[Provenance and Production-Grade Claims]]), enabling the paid generation
provider, publishing anything publicly, and any spending at all.

The paid provider stays last in the routing order and off by default,
specifically so an unattended run cannot start spending because something local
hiccuped. The same instinct governs the knowledge layer: the graph refresh
defaults to the local `claude-cli` backend rather than a metered API.

There are no hard-coded endpoints or secrets. `COMFYUI_URL` unset means "the
workstation is off" — it is never defaulted to localhost, because a silent
default turns an unreachable service into a confusing failure.

Deployment boundary: the VPS is the always-on orchestration layer (render, QC,
package, procedural plates). It is not a diffusion host. Heavy generation
belongs on the workstation's ComfyUI or an external API — which bears on
[[Render Throughput]].

## Related
- [[Publication Gate]]
- [[Knowledge Graph]]
