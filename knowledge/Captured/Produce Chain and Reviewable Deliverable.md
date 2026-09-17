---
title: "Produce Chain and Reviewable Deliverable"
type: decision
confidence: VERIFIED
captured_at: 2026-09-17T16:57:43.511313+00:00
contributor: "claude-fable-5-1"
evidence: "scripts/project.py (run_produce, produce_uses_scenes, project_assets, project_file_path); tests/test_project.py::TestProduceSceneSelection/ProjectAssetsTest/ProjectFilePathTest; webapp/apps/projects/views.py; frontend DeliverablePanel/AssetsPanel"
tags:
  - knowledge/captured
  - knowledge/decision
---

# Produce Chain and Reviewable Deliverable

**One-click produce runs research -> creative -> images -> audio -> render, choosing storyboard scenes only for narrated or already-storyboarded projects; the deliverable and its evidence are served read-only through project_assets()/project_file_path(), the single rule for which files may leave a project.**

**`run_produce` now runs the full chain** - research (no-op unless the concept
requires it, fails closed otherwise) -> creative -> images -> audio ->
assemble/render -> QC -> package - and picks its image stage with
`produce_uses_scenes()`: storyboard -> scenes when the project has narration or
already has a storyboard, the single plate set otherwise. `--scenes`/`--no-scenes`
(API: `scenes: true|false|null`) override it. The rule is small on purpose:
narration is what a storyboard distributes across scenes, and the long static
ambient formats keep the image-cycling render whose cost [[Render Throughput]]
was measured against. A re-run never silently switches format.

Two latent defects were fixed on the way, both of which made the storyboard
path unusable on the always-on host:

- `ProceduralProvider` ignored `request.width/height` and always rendered
  1920x1080, so every procedural scene contradicted its declared 512x512
  source and storyboard QC failed on `scene_image_dimensions`. `make_visuals.
  build_still()` now takes the size; the default keeps `visuals` unchanged.
- `run_pipeline`'s storyboard QC measured "the first file in `audio/`", which
  is the hand-supplied source whenever one sits next to the composed
  `track.wav`. It now measures the track the spec declares.

**The deliverable is served, not re-derived.** `project_assets()` is a read
model like `status_report()`: it lists the render, thumbnails, images (with
scene lineage), the composed audio and each layer's licence, the QC report,
storyboard plan, publication package and run logs, and computes no verdict -
[[Publication Gate]] logic stays where it was. `project_file_path()` is the
one rule for which files may leave a project directory (only `output/`,
`thumbnail/`, `images/`, `audio/`, `logs/`; no traversal, no root files, no
symlink escaping the project); the web API, and any future MCP adapter, call
it rather than re-checking. Every refusal is a 404 so a probe learns nothing.
The `files` endpoint honours single byte ranges so a 30-minute render seeks
in the browser without a full download.

This is what makes [[Human Review Decision Design]]'s approve/reject real in
the product: the reviewer watches the render and reads the QC and blocking
evidence in the same panel as the decision.

## Evidence

- scripts/project.py (run_produce, produce_uses_scenes, project_assets, project_file_path); tests/test_project.py::TestProduceSceneSelection/ProjectAssetsTest/ProjectFilePathTest; webapp/apps/projects/views.py; frontend DeliverablePanel/AssetsPanel

## Related

- [[Publication Gate]]
- [[Render Throughput]]
- [[Human Review Decision Design]]

*Captured 2026-09-17T16:57:43.511313+00:00 by claude-fable-5-1. Confidence: VERIFIED.*
