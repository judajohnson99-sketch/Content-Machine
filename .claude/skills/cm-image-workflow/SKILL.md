---
name: cm-image-workflow
description: AI image generation workflow and perceptual image quality evaluation in Content Machine. Use when generating, retrying, routing, or judging scene images, or touching providers, ComfyUI, or the GPU queue.
---

# Image generation and perceptual evaluation

**Generation** goes through `scripts/generation.py` only:
`GenerationRequest -> Router -> Provider`, order comfyui -> procedural ->
gemini -> api. `./content-machine providers` shows health.

- Metered providers (`gemini`, `api`) are off by default and need a human to
  enable them. Never enable, configure, or call them to "fix" a failure.
- `COMFYUI_URL` unset means the PC is off. Never default it to localhost.
- Depicted imagery (`require_depicted`) excludes abstract-only providers. If
  none serves it, the request queues for the GPU worker and the stage exits 2
  (NEEDS_ATTENTION), never FAILED. A queued job waiting is normal; see README
  "Remote GPU worker".
- Jobs are keyed by request digest (idempotent); an identical request reuses the
  job. Adding a provider: subclass `Provider`, register in `build_providers()`,
  test with `FakeProvider`.

**Evaluation** is layered; each layer answers a different question:
1. `qc.assess_image(path)`: measurable defects (flat fill, luminance, `block`
   vs `warn`). Passing means nothing measurable is wrong, not that it is good.
2. `qc.duplicate_scene_findings` / `image_hash` / `hash_distance`: near-duplicate
   scenes.
3. `scripts/media_intelligence.py`: optional local CLIP text-image fit
   (no paid calls, no effect on rights or grades).
4. A human looking at the images. Only a person sets
   `provenance.images.production_grade` (`./content-machine visual-grade`).
   Code may only establish `false`.

Never call procedural plates photographs; they are abstracts. Technical validity
is not production quality (vault note of that name).
