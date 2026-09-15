---
title: "Remote GPU Work Is a Queue, Not a Provider"
type: decision
confidence: VERIFIED
captured_at: 2026-09-15T03:37:14.690474+00:00
contributor: "content-machine-agent"
evidence: "scripts/worker.py; tests/test_worker.py (90 tests, incl. OfflineQueueTests); measured: a job queued with no capable ONLINE worker stays QUEUED at attempt 0 across repeated reaps"
tags:
  - knowledge/captured
  - knowledge/decision
---

# Remote GPU Work Is a Queue, Not a Provider

**Depicted-imagery work for the GPU machine is queued by the VPS and claimed by an outbound worker, rather than routed to a synchronous provider, because the workstation being off is the normal state and must cost nothing.**

## The problem this resolves

`generation.Router` is synchronous. The workstation with the GPU is off most
of the time, which is a normal operating state, not a fault. Routing depicted
work to a `comfyui` provider that cannot answer for hours burns retry
attempts and puts a persisted cooldown on a machine that is merely asleep -
and the only remaining depicted routes cost money, which an unattended run
must not reach.

Before this, the residual limitation was that concepts with
`procedural_visuals_acceptable: false` simply halted at asset generation
whenever the PC was off.

## The decision

The remote GPU is **not** a provider. The VPS owns a queue; the PC runs an
agent that dials out, claims work under a lease, renders on its own ComfyUI
over loopback, uploads verified assets, and reports. Consequences:

- A queued job with no capable `ONLINE` worker **waits**. It spends no
  attempt, trips no cooldown, and never fails. It reports
  `WAITING_FOR_CAPABLE_WORKER`.
- An attempt is spent at **claim** time, so `max_attempts` bounds real work
  rather than waiting.
- The job id is the `GenerationRequest` digest - already the router's
  idempotency key - so on success the control plane writes an ordinary
  completed-job record and the next `visuals` run reuses it. Nothing above
  `generation.py` changed.
- Connections are outbound only. Nothing dials the home network and ComfyUI
  is never exposed.

## What it does not change

A remote render is recorded as `produces_depicted: true`; it never sets
`production_grade`. That remains a human act, and the publication gate still
re-inspects the artefacts themselves, so a worker that uploads a placeholder
is caught there regardless of what it reported.

## Evidence

- scripts/worker.py; tests/test_worker.py (90 tests, incl. OfflineQueueTests); measured: a job queued with no capable ONLINE worker stays QUEUED at attempt 0 across repeated reaps

## Related

- [[Autonomy Boundaries]]
- [[Render Throughput]]

*Captured 2026-09-15T03:37:14.690474+00:00 by content-machine-agent. Confidence: VERIFIED.*
