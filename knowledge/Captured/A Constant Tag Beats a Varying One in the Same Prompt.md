---
title: "A Constant Tag Beats a Varying One in the Same Prompt"
type: insight
confidence: VERIFIED
captured_at: 2026-09-20T06:11:22.532951+00:00
contributor: "claude-opus-5"
evidence: "18 moonlit-victorian and 11 ambient-dogs ComfyUI stills (projects/*/images, 2026-09-19); fix and tests in scripts/visual_direction.py, tests/test_visual_direction.py"
tags:
  - knowledge/captured
  - knowledge/insight
---

# A Constant Tag Beats a Varying One in the Same Prompt

**When a scene prompt carries both a per-video shot-size tag and a per-scene one, the constant tag governs every render and the variation cycle becomes inert.**

The compiler described in [[Scene Prompts Are Compiled From Structured Direction, Not Written]] emitted two shot-size instructions per prompt: the environment's own `scale` ("medium interior view"), identical in every scene, and the scene's `framing` from the six-term cycle. Ten moonlit-victorian scenes cycled through all six framings and rendered as ten versions of the same medium wide shot; the constant tag won.

This is the failure mode that made the one-environment choice look like the problem. It was not. Environment count and framing variety are separate levers, and the second one was disconnected: judging [[Non-Documentary Concepts Get Distinct Environments, Not One Reused Prompt]] against these renders would have drawn the wrong conclusion. Whichever facet varies per scene must be the only one of its kind in the prompt; a constant of the same kind is not redundancy, it is an override.

The same renders exposed a second leak in the same direction. A video's avoid-list and its project negative prompt are written about the finished deliverable, so they name sound and pacing - "storm sounds", "thunder", "barking dogs", "rapid cuts", "sudden motion" - and an image model spends conditioning on them. They also repeat what the positive prompt asks for: "water" against a direction built on condensation, wet flagstone and pooling moisture. Both are now filtered out of the concept-supplied sources, while the generic failure list stays intact because it is about pictures by construction.

Neither defect showed up in QC. Every one of those stills passed the measurable checks and the near-duplicate detector, which is another instance of [[Technical Validity Is Not Production Quality]]: the variation was in the prompts, the digests differed, and the pictures were still the same picture. Because [[A Picture's Identity Is Its Prompt]], a defect in prompt compilation is invisible to everything downstream of it.

## Evidence

- 18 moonlit-victorian and 11 ambient-dogs ComfyUI stills (projects/*/images, 2026-09-19); fix and tests in scripts/visual_direction.py, tests/test_visual_direction.py

## Related

- [[Scene Prompts Are Compiled From Structured Direction, Not Written]]
- [[A Picture's Identity Is Its Prompt]]
- [[Non-Documentary Concepts Get Distinct Environments, Not One Reused Prompt]]
- [[Technical Validity Is Not Production Quality]]

*Captured 2026-09-20T06:11:22.532951+00:00 by claude-opus-5. Confidence: VERIFIED.*
