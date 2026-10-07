---
title: "GPU Capacity Chooses the Execution Path"
type: decision
confidence: VERIFIED
captured_at: 2026-09-19T08:09:56.765417+00:00
contributor: "content-machine-agent"
evidence: "jobs/queue/*.json 2026-09-19: 8 of 10 moonlit-victorian scene jobs FAILED with VAEDecode torch.OutOfMemoryError (~30-55 MiB free of a 2.90 GiB device limit) on config/comfyui_workflow_lowvram_upscale.json; s01/s03 succeeded. scripts/generation.py ComfyUIProvider._select_workflow_path/vram_total_mb; tests/test_generation.py VramTieredWorkflowTestCase"
tags:
  - knowledge/captured
  - knowledge/decision
---

# GPU Capacity Chooses the Execution Path

**Content Machine picks the ComfyUI workflow template from the worker's own reported VRAM, using a no-upscale graph under COMFYUI_UPSCALE_MIN_VRAM_MB and deferring the upscale rather than repeatedly failing a card that cannot fit it.**

**A worker's reported VRAM selects the render graph; it is not something an operator has to know and configure per machine.**

[[GPU Worker Render Capacity]] established that the GTX 1060 3GB fits an SD1.5
512x512 latent. What it did not establish - and what 2026-09-19 measured - is
that the *in-graph upscale* added to reach 1080p does not fit alongside it.
Eight of ten scene renders died in `VAEDecode` with 30-55 MiB free against a
2.90 GiB device limit, while the two that landed early (when less was resident)
succeeded. The latent was never the problem; decoding it up to 1920x1080 was.

## The rule

`ComfyUIProvider._select_workflow_path()` asks `/system_stats` for total VRAM
once per provider instance and, when it is under `COMFYUI_UPSCALE_MIN_VRAM_MB`
(default 4096), renders `config/comfyui_workflow_lowvram_tiny.json` - the same
bounded latent, no `ImageScale` before `SaveImage`.

Three properties make this safe to leave on by default:

- **An explicit choice always stands.** A template named by `COMFYUI_WORKFLOW`
  or the constructor is never second-guessed; only the deployed default is
  swapped.
- **A card that cannot say its VRAM keeps the default.** Unreachable, or too
  old for `/system_stats`, means "do not guess", not "assume small".
- **The result says what it is.** `generate()` reads the submitted graph for
  an upscale node and returns `upscaled_in_graph` plus `native_width/height`;
  when false the notes say the requested size was "not yet reached". The asset
  is honestly at generation resolution, and the upscale is deferred to ffmpeg
  at render time or a higher-VRAM provider - never silently claimed as done.

## Why capacity is distinguished from breakage

`generation.classify_failure()` labels a failure `capacity` (VRAM/memory
ceiling) or `software` (a real workflow or checkpoint fault), derived on every
read in `worker.job_view()` rather than stored. A dashboard that shows both as
a red FAILED teaches an operator to ignore red. The distinction is also why
the earlier `sd_xl_base_1.0.safetensors` rejections were worth fixing
separately: those were `software` - a hard-coded checkpoint name that did not
exist on the machine - and no amount of capacity would have helped.

Nothing here touches provenance. A render on either template still claims only
`produces_depicted: true`, and `production_grade` remains a human act.

## Evidence

- jobs/queue/*.json 2026-09-19: 8 of 10 moonlit-victorian scene jobs FAILED with VAEDecode torch.OutOfMemoryError (~30-55 MiB free of a 2.90 GiB device limit) on config/comfyui_workflow_lowvram_upscale.json; s01/s03 succeeded. scripts/generation.py ComfyUIProvider._select_workflow_path/vram_total_mb; tests/test_generation.py VramTieredWorkflowTestCase

## Related

- [[GPU Worker Render Capacity]]
- [[Remote GPU Work Is a Queue Not a Provider]]

*Captured 2026-09-19T08:09:56.765417+00:00 by content-machine-agent. Confidence: VERIFIED.*
