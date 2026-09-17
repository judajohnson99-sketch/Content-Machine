---
title: "GPU Worker Render Capacity"
type: constraint
confidence: VERIFIED
captured_at: 2026-09-15T06:06:15.595528+00:00
contributor: "content-machine-agent"
evidence: "jobs/queue/*.json transition logs, 2026-09-15: five SUCCEEDED jobs (dcddfedc4595072e, cff6f9e2ec3703ef, a114d8a9dc41c149, 8a9ffaa4706b73d0, 63019e4669a906ce) at 512x512, RUNNING->UPLOADING 23-24s, 320-370 KB PNGs, model DreamShaper_8_pruned.safetensors"
tags:
  - knowledge/captured
  - knowledge/constraint
---

# GPU Worker Render Capacity

**The GTX 1060 3GB worker renders SD1.5/DreamShaper at 512x512 in about 23 seconds; 1920x1080 latent generation is not viable on it, so 1080p needs render-small-and-upscale inside the ComfyUI workflow graph.**

MEASURED on the workstation the remote worker runs on: one GTX 1060 with 3 GB
of VRAM, ComfyUI over loopback, `DreamShaper_8_pruned.safetensors`.

## What was measured

Five jobs completed the full real path on 2026-09-15 — VPS queue, SSH tunnel,
PC worker, local ComfyUI, verified upload, VPS manifest re-verification. All
were queued at 512x512 with `--capabilities comfyui,sd15`. Each spent 23-24
seconds between the `RUNNING` and `UPLOADING` transitions and produced a
320-370 KB PNG. No job needed a second attempt.

## The limit this imposes

1920x1080 has not been run and is not expected to fit in 3 GB, and SD1.5
degrades above roughly 768px regardless of VRAM. That matters because
`worker enqueue <video-id>` takes its dimensions straight from the video
spec, which is 1920x1080, and the workflow template feeds `%width%`/`%height%`
directly into `EmptyLatentImage`.

Lowering the *enqueued* dimensions is not the fix. The job id is the
`GenerationRequest` digest, so a job queued at different dimensions would no
longer match what `visuals` computes, and the reuse seam that makes a remote
render appear as an ordinary completed job would break. The resize belongs
inside the workflow graph — a small latent, then an upscale before
`SaveImage` — where it is invisible to the digest.

Until that workflow exists, real GPU work is queued with explicit
`--width`/`--height` at a size the card can serve.

## What it does not change

Nothing here touches provenance. A remote render still claims only
`produces_depicted: true`, and `production_grade` remains a human act.

## Evidence

- jobs/queue/*.json transition logs, 2026-09-15: five SUCCEEDED jobs (dcddfedc4595072e, cff6f9e2ec3703ef, a114d8a9dc41c149, 8a9ffaa4706b73d0, 63019e4669a906ce) at 512x512, RUNNING->UPLOADING 23-24s, 320-370 KB PNGs, model DreamShaper_8_pruned.safetensors

## Related

- [[Remote GPU Work Is a Queue Not a Provider]]
- [[Render Throughput]]

*Captured 2026-09-15T06:06:15.595528+00:00 by content-machine-agent. Confidence: VERIFIED.*
