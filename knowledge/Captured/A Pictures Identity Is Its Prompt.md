---
title: "A Picture's Identity Is Its Prompt"
type: decision
confidence: VERIFIED
captured_at: 2026-09-19T15:32:04.428451+00:00
contributor: "Claude Opus 5"
evidence: "scripts/storyboard.py _scene_prompt + _seed_for_prompt + apply_scene_motifs; scripts/project.py thumbnail_timestamps + provenance.images.scene_images; scripts/motion.py scene_start_times; tests/test_storyboard.py OneSeedPerPictureTest; tests/test_project.py SceneAssetReuseTest, ThumbnailSamplingTest; measured read-only against projects/ambient-dogs-home-alone-20260919"
tags:
  - knowledge/captured
  - knowledge/decision
  - storyboard
  - visual-diversity
---

# A Picture's Identity Is Its Prompt

**A scene's picture is identified by its image prompt alone; seeds, generation digests and thumbnail sampling all derive from the set of distinct prompts, so a deliberately repeated environment costs one render and yields one candidate.**

**Nothing that the generator cannot draw belongs in an image prompt, and two scenes meant to show the same thing must resolve to the same prompt — because prompt equality is what the rest of the pipeline uses to decide whether a picture already exists.**

The `ambient-dogs-home-alone-20260919` diagnostic render is the evidence: ten scenes, ten distinct prompts, ten seeds, ten GPU renders and ten near-identical beige living rooms, plus one retry file left in `images/`. The prompts differed only by a trailing `, scene 7, body` — bookkeeping that describes no picture. Because every prompt was unique, every scene got its own seed and its own `request_digest`, so the generation router's idempotency could never fire. The waste was not a bad prompt; it was an identity bug.

Three layers now derive from the same rule:

- `_scene_prompt` appends only what a generator can draw. Index and section stay in the scene's own fields.
- `_seed_for_prompt` assigns one seed per distinct prompt, in both `build_storyboard` and `apply_scene_motifs`, so identical pictures share a seed and a digest and are rendered once. Rebuilding the same project's storyboard from its real metadata now needs one render where it needed ten.
- `thumbnail_timestamps` samples the middle of the first scene showing each distinct image rather than fixed fractions of the runtime — with pictures now deliberately reused, 0.25/0.5/0.75 could hand the reviewer three copies of one frame.

Deliberate repetition is therefore cheap, which is what makes [[Non-Documentary Concepts Get Distinct Environments, Not One Reused Prompt]] safe: a reasoned "this four-hour piece needs one held environment" costs one render, and variety costs exactly as many renders as there are genuinely different pictures.

What a reviewer sees is kept honest at the gate: `provenance.images.scene_images` lists the pictures the storyboard actually uses and `unreferenced_count` counts files in `images/` no scene points at. A retry left behind is residue, not a deliverable — the distinction [[Technical Validity Is Not Production Quality]] draws between what passed a check and what a human would publish.

## Evidence

- scripts/storyboard.py _scene_prompt + _seed_for_prompt + apply_scene_motifs; scripts/project.py thumbnail_timestamps + provenance.images.scene_images; scripts/motion.py scene_start_times; tests/test_storyboard.py OneSeedPerPictureTest; tests/test_project.py SceneAssetReuseTest, ThumbnailSamplingTest; measured read-only against projects/ambient-dogs-home-alone-20260919

## Related

- [[Non-Documentary Concepts Get Distinct Environments, Not One Reused Prompt]]
- [[Technical Validity Is Not Production Quality]]
- [[Depicted Imagery Defers to the GPU Queue]]

*Captured 2026-09-19T15:32:04.428451+00:00 by Claude Opus 5. Confidence: VERIFIED.*
