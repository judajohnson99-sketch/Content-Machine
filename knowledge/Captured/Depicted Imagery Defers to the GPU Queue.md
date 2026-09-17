---
title: "Depicted Imagery Defers to the GPU Queue"
type: decision
confidence: VERIFIED
captured_at: 2026-09-17T20:42:37.135639+00:00
contributor: "content-machine-agent"
evidence: "scripts/project.py _defer_to_gpu_worker, tests/test_project.py TestGpuWorkerDeferral"
tags:
  - knowledge/captured
  - knowledge/decision
---

# Depicted Imagery Defers to the GPU Queue

**When a concept requires depicted imagery and no synchronous provider serves it, visuals/scenes queue the request for the enrolled GPU worker and exit 2 (NEEDS_ATTENTION), never FAILED; the next run resumes from the completed job via the request digest.**

Queueing happens only after the [[Remote GPU Work Is a Queue, Not a Provider]] router has exhausted every synchronous provider, and only when a worker with the comfyui capability is enrolled - its liveness is irrelevant, because the PC being off is the normal state and the job waits at attempt 0. The stage returns exit code 2 so the web layer records NEEDS_ATTENTION and the Workspace shows the job under GPU worker; nothing sets production_grade. Readiness (worker_offline, comfyui_unavailable, model_unavailable, worker_busy, worker_ready) is derived from the agent's heartbeat, which now carries the GPU, VRAM and installed checkpoints, so the catalogue can say honestly whether a depicted concept starts now or waits. The default workflow respects [[GPU Worker Render Capacity]] by rendering a bounded latent and upscaling in-graph.

## Evidence

- scripts/project.py _defer_to_gpu_worker, tests/test_project.py TestGpuWorkerDeferral

## Related

- [[Remote GPU Work Is a Queue, Not a Provider]]
- [[GPU Worker Render Capacity]]

*Captured 2026-09-17T20:42:37.135639+00:00 by content-machine-agent. Confidence: VERIFIED.*
