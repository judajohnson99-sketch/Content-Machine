---
title: "Vision Judgements Travel With the Completed GPU Job"
type: decision
confidence: INFERRED
captured_at: 2026-10-22T09:07:48.194244+00:00
contributor: "content-machine-agent"
evidence: "scripts/vision.py, scripts/gpu_coordination.py, scripts/worker.py (vision evidence), tests/test_vision.py, tests/test_worker.py; no run on real hardware"
tags:
  - knowledge/captured
  - knowledge/decision
  - vision
  - gpu-worker
---

# Vision Judgements Travel With the Completed GPU Job

**The GPU worker judges images right after rendering and ships sha-bound advisory evidence with the job; the VPS never calls a vision model on the PC.**

Image judging by a local vision model could have been a synchronous provider the VPS calls, or a second job kind in the queue. Neither fits [[Remote GPU Work Is a Queue Not a Provider]]: the PC only dials out, so the VPS cannot call a model on it, and a separate queue job would load a model onto a GPU that [[GPU Worker Render Capacity]] says is already tight (GTX 1060 3GB).

Decision: the worker agent judges each image immediately after rendering it, while it already holds the GPU, and reports the judgements with `complete` as advisory evidence bound to the image hashes. The control plane re-validates the payload (schema, hashes, closed vocabularies) and `scripts/quality.py` reads evidence first, then the judgement cache, then a direct provider. The worker still cannot declare success or set `production_grade`; see [[Perceptual Quality Is Judged Against Intent and Informs Review]] for why findings inform review and never block.

Renders win over vision. A file lease (`scripts/gpu_coordination.py`) and a ComfyUI queue probe make vision defer (`gpu_busy`) rather than delay or fail a render, and the model is unloaded after every image so ComfyUI gets the VRAM back. This is the same shape as [[GPU Capacity Chooses the Execution Path]]: capacity decides, and absence of capacity is "Not evaluated", never a pass.

Unverified: ComfyUI's `/free` endpoint effect on VRAM, and whether qwen3-vl:2b fits beside a 3GB card at all. Both need the GPU PC.

## Evidence

- scripts/vision.py, scripts/gpu_coordination.py, scripts/worker.py (vision evidence), tests/test_vision.py, tests/test_worker.py; no run on real hardware

## Related

- [[Remote GPU Work Is a Queue Not a Provider]]
- [[Perceptual Quality Is Judged Against Intent and Informs Review]]
- [[GPU Capacity Chooses the Execution Path]]
- [[GPU Worker Render Capacity]]

*Captured 2026-10-22T09:07:48.194244+00:00 by content-machine-agent. Confidence: INFERRED.*
