---
name: cm-video-qc
description: Video assembly and quality evaluation in Content Machine. Use when rendering, running or interpreting QC, building a Kdenlive project, or diagnosing a project's status or review verdict.
---

# Video production and quality evaluation

Pipeline: `./content-machine produce <id>` (concept -> creative -> images ->
audio -> render -> QC -> package) or stage by stage with `run`
(validate -> render -> thumbnails -> QC -> package). Stdlib + ffmpeg only on
the render path; add no image or HTTP dependency.

- **Contracts:** `video_spec.json` is the renderer's minimal contract;
  editorial fields live in `metadata.json`. Do not move editorial data into
  the spec.
- **Render:** `scripts/render.py`; long videos are assembled piecewise
  (120 shots for 30 min), with real motion and dissolves.
- **QC:** `python3 scripts/qc.py --video <mp4> --spec <spec> --json`
  (`qc_video`: file, streams, duration, silence, black runs, scene images
  decodable). `qc_storyboard` adds per-scene checks.
- **Editable output:** `./content-machine kdenlive <id>` builds and verifies a
  native Kdenlive project via MLT.
- **State:** `./content-machine status <id>` gives the verdict and whether it
  still applies (a stale digest voids it).

QC PASS = technically sound, never = publishable. `READY_FOR_REVIEW` is the hard
stop; no code or skill may advance past it or set a production-grade claim.
When diagnosing, read `projects/<id>/metadata.json` and the QC report, not
`output/` binaries. Test with `python3 -m unittest tests.test_qc` /
`tests.test_render` before the full suite.
