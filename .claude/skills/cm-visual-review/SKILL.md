---
name: cm-visual-review
description: Visual direction, scene consistency, AI image generation workflow and perceptual quality review for Content Machine. Use when writing or reviewing visual direction, storyboards, scene images or generation settings, or judging whether generated images are fit for review.
---

# Visual direction, generation and perceptual review

## Workflow (all steps already exist as code)
1. **One visual-direction document per video** (`scripts/visual_direction.py`): palette, light, materials, camera, atmosphere, continuity anchors, depicted environments, shot plan. Scene prompts are *compiled* from it deterministically (`compile_scene_prompts`, `compile_negative_prompt`); never hand-write a different prompt per scene or add "8k / masterpiece / hyper-realistic" tags (`_strip_slop` removes them).
2. `./content-machine storyboard <id> --scenes N` then `scenes <id>`: images go through the provider router (comfyui → procedural → gemini → api). Metered providers stay off unless a human enabled them; do not enable them.
3. Jobs are idempotent by request hash; re-running reuses results. Changing prompts creates new jobs and clears any earlier production-grade claim.

## Perceptual review (what measurements cannot see)
`qc.assess_image` / `qc_storyboard` already measure luma, flat fills, duplicates and hash distance; they never say an image is *good*. After they pass, open the actual images (Read the PNGs, a few at a time) and judge:
- **Consistency:** continuity anchors and palette hold across scenes; no drift in style, lighting direction or subject.
- **Variety:** scenes differ in framing/subject; not N copies of one picture.
- **Fidelity:** matches the scene prompt; depicted subjects are actually depicted; no artifacts (hands, text, warped geometry, seams).
- **Honesty:** procedural plates are abstracts, never "photographs". A plate standing in for a depicted scene is a placeholder (`procedural_visuals_acceptable: false` concepts).

## Boundaries
Report findings as *recommendations with image ids*. Never set `provenance.images.production_grade`; only a person does (`visual-grade`). Do not edit `metadata.json` to relabel assets. Detail: README "Visuals".
