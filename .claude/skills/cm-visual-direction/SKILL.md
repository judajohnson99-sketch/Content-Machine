---
name: cm-visual-direction
description: Visual direction, scene consistency, and cinematic storyboarding for Content Machine videos. Use when writing or reviewing a visual direction document, scene prompts, shot plans, or motion.
---

# Visual direction and storyboarding

Prompts are **compiled** from a structured direction document
(`scripts/visual_direction.py`), never hand-written per scene. Storyboards come
from `scripts/storyboard.py`; motion from `scripts/motion.py`.

Rules the code and vault already encode (verify in the cited note before
changing any):
- One direction per video: identity facets (palette, light, materials, camera,
  atmosphere, continuity anchors) are constant; environment, framing and light
  state vary per scene. That split is what makes it one video, not ten.
- **Vary exactly one thing per axis.** A constant tag and a varying tag for the
  same property in one prompt: the constant wins and variation goes inert
  (vault: "A Constant Tag Beats a Varying One in the Same Prompt").
- **A picture's identity is its prompt.** Seeds, job digests and thumbnails
  derive from distinct prompts; never append scene numbers or bookkeeping.
  Deliberate repetition is cheap (one render).
- Omit what a generator cannot draw (sound, pacing, "rapid cuts") and the slop
  vocabulary ("8k", "masterpiece"). Match the target provider's prompt dialect.
- Non-documentary concepts need distinct environments, not one reused prompt.
- One batched LLM call per video for scene motifs
  (`creative.generate_scene_motifs`); never per scene.

Workflow: `./content-machine storyboard <id>` then inspect `storyboard.json`
(scene count, distinct prompts, motion, dissolves). Check variety with
`qc.visual_diversity_warnings` and `qc.qc_storyboard`.
Do not describe generated plates as photographs.

Look up: `./content-machine knowledge query "scene prompts"` before editing.
