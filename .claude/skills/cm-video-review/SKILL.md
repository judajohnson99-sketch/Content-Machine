---
name: cm-video-review
description: Video production and quality evaluation for Content Machine renders before READY_FOR_REVIEW. Use when judging a rendered MP4, its storyboard pacing, motion and transitions, QC results, or the review checklist.
---

# Video production and quality evaluation

Pipeline: `run <id>` = validate → render (`scripts/render.py`, ffmpeg, piecewise so long videos work) → thumbnails → QC (`scripts/qc.py`) → package. `kdenlive <id>` builds an editable project and a review MP4. `status <id>` reports the verdict and whether it still applies.

## What QC already checks
File exists/non-trivial/opens, video + audio streams, duration vs spec, audio not silent, black-run detection, storyboard checks (duplicate scenes, visual diversity, scene timing vs audio). Read the QC report first; do not re-derive it.

## Added human-style review (after QC passes)
1. **Pacing:** shot lengths and dissolves match the research directives in `research_influence.json`; no dead stretches, no abrupt cuts.
2. **Motion:** variety of pan/zoom/static; no jitter, no motion that fights the scene content.
3. **Spot-check frames:** extract a few frames across the timeline (`ffmpeg -ss T -i out.mp4 -frames:v 1 f.png`, into a scratch dir) and view them; check first/last second and each transition.
4. **A/V sync and levels:** narration aligned to scenes; audio ending cleanly, no click at loop/cut.
5. **Honesty & metadata:** title/description make no claim the content cannot support; review checklist items (e.g. "fact-check the narration") present.

## Boundaries
`READY_FOR_REVIEW` is the hard stop; approve/reject (`approve`, `reject`) are human acts requiring a fresh gate digest. Report recommendations and blockers; never approve. Heavy generation belongs on the GPU worker or external API, not the VPS.
