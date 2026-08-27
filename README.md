# Content Machine

A local, deterministic pipeline that turns still images + audio into a
YouTube-ready video package, with a **human approval gate before any
publishing**. Everything below is implemented and tested; see
[Not built yet](#not-built-yet) for the honest boundary.

## Architecture

```
project assets ─► validate ─► render ─► thumbnails ─► QC ─► package ─► READY_FOR_REVIEW ─► [human] ─► publish
                                                                                            (not built)
```

| Component | File | Responsibility |
|---|---|---|
| Renderer | `scripts/render.py` | images + audio + spec → H.264/AAC MP4 (FFmpeg) |
| Quality control | `scripts/qc.py` | probes a finished MP4 for spec conformance and render faults |
| Project lifecycle | `scripts/project.py` | `init` / `validate` / `run` orchestration |
| Entry point | `./content-machine` | thin wrapper; fixes the working directory |
| Fixtures | `scripts/make_test_fixtures.py` | generates test images/audio via FFmpeg |
| Article generator | `generate.py` | pre-existing; writes Markdown articles via an LLM |

Design notes:

- **`video_spec.json` is the renderer's contract and stays minimal.**
  Editorial fields (concept, audience, titles, scenes, status) live in
  `metadata.json`, so content structure can evolve without touching the
  renderer.
- `project.py` imports `render.py` and `qc.py` rather than reimplementing
  their validation, so each rule exists in exactly one place.
- Ingested assets are **hardlinked**, not copied — a project costs
  essentially no extra disk.

## Installation

```bash
sudo apt-get install -y ffmpeg     # provides ffmpeg + ffprobe
```

No Python packages are required for rendering — the pipeline uses only the
standard library plus the system FFmpeg binaries. (`requirements.txt` covers
the separate `generate.py` article generator.)

## Configuration

`generate.py` reads `.env` (see `.env.example`). The render pipeline needs
no credentials. `.env` is gitignored; never commit API keys.

## Project format

```
projects/<video-id>/
├── video_spec.json   render contract (consumed by render.py)
├── metadata.json     editorial spec + pipeline status
├── images/           still images (hardlinked from source)
├── audio/            audio track
├── thumbnail/        candidate_1..3.jpg, extracted from the render
├── output/           <video-id>.mp4, qc_report.json, publication_package.json
└── logs/             run_<timestamp>.log
```

### `video_spec.json`

| Field | Required | Description |
|---|---|---|
| `width`, `height` | yes | Output resolution. Must be even (H.264 requirement). |
| `fps` | yes | Frame rate. |
| `duration_seconds` | yes | Exact output length. |
| `images.source_dir` | yes | Directory of `.png`/`.jpg`/`.jpeg`, relative to the project. |
| `images.seconds_per_image` | no (`4.0`) | Time per image. |
| `audio.file` | yes | Audio track, relative to the project. |
| `ken_burns.enabled` | no (`true`) | Slow pan/zoom on each still. |
| `ken_burns.zoom_start` / `zoom_end` | no (`1.0` / `1.15`) | Zoom range. |
| `crossfade.enabled` | no (`true`) | Crossfade between images. |
| `crossfade.duration_seconds` | no (`0.75`) | Must be less than `seconds_per_image`. |
| `output_path` | no | Only used when calling `render.py` directly. |

Images cycle automatically when there are fewer than the duration needs;
audio loops when shorter than the video. Output is deterministic — the same
inputs produce a byte-identical MP4.

## Commands

### Create a project

```bash
./content-machine init my-video \
  --images /path/to/images \
  --audio  /path/to/track.mp3 \
  --title  "My Video Title" \
  --concept "what this video is" \
  --audience "who it's for"
```

Optional: `--width` `--height` `--fps` `--duration` `--seconds-per-image`
`--copy` (copy instead of hardlink) `--force` (re-init).
**Duration defaults to the audio track's length.**

### Validate

```bash
./content-machine validate my-video
```

Checks structure, spec validity, supported formats, and that every asset
actually decodes (not merely that it exists).

### Render everything

```bash
./content-machine run my-video
```

Runs validate → render → thumbnails → QC → package.

Exit codes: `0` READY_FOR_REVIEW · `2` NEEDS_ATTENTION (e.g. QC failed or
metadata incomplete) · `1` hard failure.

Outputs `projects/my-video/output/`:
- `<video-id>.mp4` — the video
- `qc_report.json` — every check with pass/fail and detail
- `publication_package.json` — MP4 + sha256, thumbnail, title, description,
  tags, QC result, generation details, and a `publish` block that stays
  `approved_by_human: false` until a human acts

A project only reaches `READY_FOR_REVIEW` when QC passes **and** a title and
description are set. QC passing is not sufficient.

### Render without a project

```bash
python3 scripts/render.py --spec config/video_spec.example.json
python3 scripts/qc.py --video output/video/test_render.mp4 --spec config/video_spec.example.json
```

## Testing

```bash
./content-machine test          # 28 tests, ~23s
```

Covers the real CLIs end to end and inspects real media with ffprobe:
codecs, resolution, FPS, duration, faststart atom order, audio looping
audibility, byte-level determinism, image cycling, every validation error
path, hardlink deduplication, the READY_FOR_REVIEW gate, and idempotent
re-runs.

Regenerate fixtures with `python3 scripts/make_test_fixtures.py`.

## Troubleshooting

| Symptom | Cause / fix |
|---|---|
| `ffmpeg executable not found` | `apt-get install ffmpeg` |
| `'width' must be an even number` | H.264 needs even dimensions. |
| `crossfade.duration_seconds must be less than seconds_per_image` | Shorten the crossfade or lengthen each image. |
| `unreadable/corrupt image(s)` | ffprobe reports zero dimensions — the file is damaged despite a valid extension. |
| `NEEDS_ATTENTION` with QC PASS | Title/description missing in `metadata.json`. |
| Render fails | The last 40 lines of ffmpeg stderr are logged, and copied into `projects/<id>/logs/`. |

## Audio

`scripts/audio.py` builds a complete track from layered sources. Every layer
comes from a named **provider**, and every layer carries a licence
determination — audio whose rights cannot be established is refused, not
silently used.

```bash
python3 scripts/audio.py providers            # what can produce audio
python3 scripts/audio.py voices               # narration voices + licence evidence
python3 scripts/audio.py compose --plan plan.json --output track.wav --manifest m.json
./content-machine audio <video-id>            # build a project's track from its plan
```

| Provider | Needs | Rights |
|---|---|---|
| `noise` | ffmpeg only | generated-original |
| `tone` | ffmpeg only | generated-original |
| `rain` | ffmpeg only | generated-original (procedural, not a field recording) |
| `silence` | ffmpeg only | generated-original |
| `file` | a local asset | **must declare** source/license/commercial_use |
| `tts` | piper-tts + voice model | per-voice, verified from its MODEL_CARD |

An audio plan lives in `metadata.json` under `audio_plan.composition` and
defaults its length to the video's `duration_seconds`:

```json
{
  "normalize": true, "target_lufs": -20,
  "fade_in_seconds": 4, "fade_out_seconds": 4,
  "layers": [
    {"id": "bed", "provider": "rain", "params": {"intensity": "steady"}, "gain_db": -14},
    {"id": "vo",  "provider": "tts",  "params": {"text": "..."}, "start_seconds": 2}
  ]
}
```

Composition fits each layer to the exact target (generated sources are
synthesised at length, so they have no loop seam; file sources loop with
optional `crossfade_loop_seconds` at the join), applies gain/fades/offsets,
mixes, optionally loudness-normalises, then verifies duration and rejects a
silent result. Output is 48 kHz stereo PCM, matching what the renderer
encodes.

**Determinism:** noise sources are seeded (`DEFAULT_NOISE_SEED`) and Piper
runs with zeroed noise scales, so the same plan yields byte-identical audio.

### Narration

Piper runs locally, offline and free, at ~0.33× realtime on this host.

```bash
.venv/bin/pip install piper-tts
mkdir -p ~/.local/share/piper-voices && cd ~/.local/share/piper-voices
BASE=https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_US/libritts_r/medium
curl -sSLfO $BASE/en_US-libritts_r-medium.onnx
curl -sSLfO $BASE/en_US-libritts_r-medium.onnx.json
```

**Voice licensing is verified, not assumed.** `en_US-libritts_r-medium` is
CC BY 4.0 (commercial use permitted, attribution required — the manifest
surfaces the required attribution string, and it flows into the publication
package). Two commonly-suggested voices are explicitly blocked in code:
`lessac` (Blizzard 2013, research-only) and `hfc_female` (CC BY-**NC**-SA).

### Rights gate

`run` refuses READY_FOR_REVIEW when `audio_manifest.json` reports
`commercial_use_cleared: false`, naming the offending layers.

## Visuals

There is no image generation on this host (no GPU, no ComfyUI).
`scripts/make_visuals.py` produces deterministic procedural plates —
gradients, vignetting, dither — tuned to stay above the QC blackdetect
luminance floor:

```bash
python3 scripts/make_visuals.py --style deep-night --out projects/<id>/images --count 1
```

These are genuinely generated abstracts, **not photographs**, and must never
be described as such. Whether they are a real deliverable depends on the
concept:

- A dark plate for a brown-noise sleep video **is** the intended asset.
- A plate standing in for "rain on a window at night" **is not** — that
  concept needs depicted imagery.

Projects record this in `provenance.images.production_grade`. When it is
`false`, `run` blocks READY_FOR_REVIEW and names the reason, so placeholder
imagery cannot quietly ship.

### Review checklist

`metadata.review_checklist` travels into the publication package.
READY_FOR_REVIEW means *assembled and ready to be checked*, not cleared to
publish — so items like "fact-check the narration" belong here rather than
as blockers.

## Experiments

Content hypotheses live in `experiments/concepts.json`; reusable VideoSpec
templates live in `experiments/templates/`. Each concept carries its
audience, format, visual/audio concept, monetization hypothesis, risks, and
explicit **continue** and **kill** evidence — a hypothesis without a kill
condition is not an experiment.

```bash
python3 scripts/experiment.py list              # ranked by expected information value
python3 scripts/experiment.py show <concept-id> # full detail
python3 scripts/experiment.py blockers          # what stands between us and production
python3 scripts/experiment.py validate          # check the batch is well-formed
python3 scripts/experiment.py scaffold <concept-id> <video-id> [--images DIR --audio FILE]
```

`scaffold` turns a concept into a real project directory, copying the
hypothesis and kill criteria into `metadata.json` so a result is always
attributable to the experiment that produced it. With assets it is
immediately renderable; without them it is created with
`status.assets: PENDING`.

**Ranking is computed, not hand-assigned** (`experiment.score()`):

```
benefit  = uncertainty_reduction x generalizability x repeatability x monetization_clarity
cost     = production_complexity + generation_dependence
discount = 1 - (policy_risk - 1) x 0.15
EIV      = benefit / cost x discount
```

This deliberately ranks *information value*, not appeal: a concept expected
to fail still scores well if failing would be cheap and instructive.

### Measured production constraints

Recorded in `concepts.json` under `production_constraints`, measured on this
host (4 cores, no GPU) rather than estimated:

- **Render throughput, 1080p:** 3.33x realtime (24fps, no Ken Burns), 2.84x
  (30fps, no Ken Burns), 2.14x (30fps, Ken Burns). A 3-hour video costs
  ~55–85 min of CPU, capping output at roughly 2–3 long videos/day.
- **Dark-visual luminance floor:** perceived luma below ~26/255 trips the QC
  `blackdetect` check and blocks READY_FOR_REVIEW. Dark sleep visuals must
  sit at or above roughly `#1a1a2e`.
- **Audio loop seam:** the renderer loops audio but does **not** crossfade
  the join. Long-form ambient needs either a seam-free source or a renderer
  enhancement before it is publishable.

### The binding constraint is audio, not images

`experiment.py blockers` groups concepts by what they actually need. Only
**5 of 20** are producible with today's toolchain — and every blocked one is
blocked on *audio* (TTS, music licensing, or multi-track mixing), not on
image generation. ComfyUI unblocks imagery; it does not unblock most of this
batch.

## Not built yet

Deliberately unimplemented, in dependency order:

- **ComfyUI / AI image generation** — *blocked on this host*: no GPU
  (`nvidia-smi` absent), 4 CPU cores, 3.8 GB RAM. Local diffusion is not
  viable here. The renderer already consumes any folder of images, so
  ComfyUI output can be pointed at `projects/<id>/images/` with no renderer
  change once suitable hardware exists.
- **AI script/title/description generation** — `generate.py` shows the
  provider pattern; wiring it into `metadata.json` costs API credits, so it
  is left off by default.
- **Narration / TTS and multi-track audio mixing** — no TTS engine is
  installed; the pipeline currently takes one supplied audio track.
- **YouTube publishing** — intentionally absent. The pipeline stops at
  `READY_FOR_REVIEW` with a complete package; publishing stays a human step.
- **Analytics** — `publication_package.json` reserves `publish.publication_id`
  and the `experiment` block in `metadata.json` records cost/duration, so
  real data can be attached later. No performance data is fabricated.
