---
title: "Scene Prompts Are Compiled From Structured Direction, Not Written"
type: decision
confidence: VERIFIED
captured_at: 2026-09-20T02:17:36.269581+00:00
contributor: "content-machine-agent"
evidence: "projects/ambient-dogs-home-alone-20260919/storyboard.json and projects/moonlit-victorian-glasshouse-20260917/storyboard.json, 2026-09-20: 10 scenes compiled to 6 distinct pictures each, from one environment, with framing and light cycling at different periods."
tags:
  - knowledge/captured
  - knowledge/decision
  - visuals
  - prompting
---

# Scene Prompts Are Compiled From Structured Direction, Not Written

**A video carries a visual direction document of named facets - palette, light, materials, camera, continuity anchors - and each scene's prompt is compiled from it, so the identity is constant while environment, framing and light state vary.**

Asking a model for an `image_prompt` returns one free-text blob, and reusing it for every scene produces one picture N times. Asking it for a *per-scene* prompt costs a call per scene and loses the identity that makes scenes belong to one video.

`scripts/visual_direction.py` takes a third path. One call per video returns structured facets (palette, lighting, atmosphere, materials, continuity anchors, camera/lens/depth-of-field, render intent) plus the environments the video actually depicts. `compile_scene_prompt` then assembles each scene deterministically in an order chosen for how diffusion models weight tokens: environment and focal point first, then framing and light state (what distinguishes this scene), then palette, materials, anchors and atmosphere (the identity that must not vary), then camera and render intent last.

Three consequences worth keeping:

- **Variation is structural, not verbal.** Environments, framings and light states cycle at deliberately unequal periods, so a returning environment returns differently rather than repeating. Scenes that genuinely land on the same picture share a digest and one render, which is [[A Picture's Identity Is Its Prompt]] working as intended, not a defect.
- **Slop is stripped, not emitted.** `SLOP_TERMS` ("8k", "hyper-realistic", "unreal engine", "masterpiece") is removed from anything reaching a generator, including from the model's own reply, and what was removed is recorded on the document. The moonlit-victorian base prompt contained three of them.
- **The prompt is written in the provider's dialect.** `Provider.prompt_style` is `tag` for SD-family checkpoints (comma-separated, real negative prompt) and `natural` for hosted models (sentences, no negative channel). The router reports it from configuration only - never a health probe - so a plan does not change shape because the GPU box was asleep when it was written.

## Evidence

- projects/ambient-dogs-home-alone-20260919/storyboard.json and projects/moonlit-victorian-glasshouse-20260917/storyboard.json, 2026-09-20: 10 scenes compiled to 6 distinct pictures each, from one environment, with framing and light cycling at different periods.

## Related

- [[A Picture's Identity Is Its Prompt]]
- [[Non-Documentary Concepts Get Distinct Environments, Not One Reused Prompt]]
- [[Technical Validity Is Not Production Quality]]

*Captured 2026-09-20T02:17:36.269581+00:00 by content-machine-agent. Confidence: VERIFIED.*
