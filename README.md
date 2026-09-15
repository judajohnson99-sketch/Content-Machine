# Content Machine

A local, deterministic pipeline that turns still images + audio into a
YouTube-ready video package, with a **human approval gate before any
publishing**. Everything below is implemented and tested; see
[Not built yet](#not-built-yet) for the honest boundary.

## Architecture

```
                    ┌─ ComfyUI (workstation, preferred)
generation router ──┼─ procedural (always-on, abstract only)
        │           └─ external API (off unless configured)
        │
worker queue ───────── remote GPU, when the workstation is off right now
        │              (the PC dials in; the job waits until it does)
        ▼
project assets ─► validate ─► render ─► thumbnails ─► QC ─► package ─► READY_FOR_REVIEW ─► [human] ─► publish
                                                                                            (not built)
```

| Component | File | Responsibility |
|---|---|---|
| Generation router | `scripts/generation.py` | provider-agnostic image generation: ComfyUI → procedural → API |
| Renderer | `scripts/render.py` | images + audio + spec → H.264/AAC MP4 (FFmpeg) |
| Quality control | `scripts/qc.py` | probes a finished MP4 for spec conformance and render faults |
| Project lifecycle | `scripts/project.py` | `init` / `validate` / `run` orchestration |
| Entry point | `./content-machine` | thin wrapper; fixes the working directory |
| Fixtures | `scripts/make_test_fixtures.py` | generates test images/audio via FFmpeg |
| Article generator | `generate.py` | pre-existing; writes Markdown articles via an LLM |
| Knowledge layer | `scripts/knowledge.py` | the Obsidian vault and its derived Graphify graph |
| Worker control plane | `scripts/worker.py` | the remote-GPU queue: leases, retries, manifests, audit trail |
| Worker API | `scripts/worker_api.py` | the HTTP surface the worker dials into |
| Worker agent | `scripts/worker_agent.py` | runs **on** the GPU machine; outbound only |

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

### Generate visuals

```bash
./content-machine providers              # which providers are reachable now
./content-machine visuals my-video       # generate images through the router
```

Images come from whichever provider is healthy, in order: the workstation's
ComfyUI, then procedural plates, then an external API. See
[Generation](#generation). The prompt comes from `metadata.visual_plan.prompt`
unless `--prompt` overrides it.

### Queue a render for the GPU machine

```bash
./content-machine worker enroll home-gpu-01 --capabilities comfyui,sd15
./content-machine worker serve                 # on the VPS
./content-machine worker agent                 # on the GPU machine
./content-machine worker enqueue my-video
./content-machine worker jobs
```

Full protocol, lifecycle and safety rules: [Remote GPU worker](#remote-gpu-worker).

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

### Check a project's state

```bash
./content-machine status my-video
```

Reports what the project's verdict is **now**, recomputed from what is on
disk, rather than echoing what `metadata.json` recorded earlier:

```
project:  exp-c-narrated-sleep
recorded: READY_FOR_REVIEW
verdict:  NEEDS_ATTENTION
blocking:
  - visuals are not production-grade: ...
WARNING:  recorded status 'READY_FOR_REVIEW' is STALE - a re-run would produce 'NEEDS_ATTENTION'
digest:   CHANGED - project inputs differ from those the recorded verdict was computed from
```

`run` stores a `gate_digest` over every gate-relevant input (spec, asset
hashes, provenance, title/description, the linked concept, audio clearance).
`status` recomputes it, so editing a project after a run surfaces as
staleness instead of leaving a verdict that no longer holds. Same exit codes
as `run`, and it renders nothing.

### Render without a project

```bash
python3 scripts/render.py --spec config/video_spec.example.json
python3 scripts/qc.py --video output/video/test_render.mp4 --spec config/video_spec.example.json
```

## Testing

```bash
./content-machine test          # 319 tests, ~85s
```

Covers the real CLIs end to end and inspects real media with ffprobe:
codecs, resolution, FPS, duration, faststart atom order, audio looping
audibility, byte-level determinism, image cycling, every validation error
path, hardlink deduplication, the READY_FOR_REVIEW gate, and idempotent
re-runs.

Generation is covered too: provider selection and failover, retry, cooldown,
malformed provider responses, job reuse and restart recovery, and the
production-grade claim lifecycle. The ComfyUI adapter runs against a local
stand-in HTTP server, so no test needs a GPU, the workstation, or a paid API.

The [remote worker](#remote-gpu-worker) is covered over real sockets: token
authentication and revocation, derived liveness, capability matching, claim
and lease semantics, a zombie worker's stale lease being refused, lease
expiry returning work to the queue, retry exhaustion, permanent failure,
every illegal transition, upload digest/size/filename/extension refusals,
manifest re-verification, the handoff into the generation job store, and a
full agent cycle end to end. Also the invariant that matters most: a job
queued while the PC is off waits at attempt 0 and never fails.

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
| Worker job stuck at `WAITING_FOR_CAPABLE_WORKER` | Nothing is wrong: no `ONLINE` worker has the required capabilities. `worker workers` shows why. The job waits at attempt 0. |
| `cannot listen on 127.0.0.1:8788` | Something else has the port. Set `WORKER_API_PORT` or pass `--port`. |
| Agent: `CONTROL_PLANE_URL is not set` | None of the three worker variables has a default. See [Setup](#setup). |
| Agent: `control plane refused ... (HTTP 401)` | Wrong or revoked token. Re-issue with `worker enroll <id> --rotate` (this invalidates the current one). |
| Agent: `(HTTP 409) stale or wrong lease` | The lease expired and the job was returned to the queue mid-render. Raise `WORKER_LEASE_SECONDS` if renders legitimately take longer. |
| Job `FAILED` after one attempt | A permanent fault the worker reported — usually a missing checkpoint or a bad workflow template. `worker show <job-id>` has the detail. |

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

## Generation

Images are requested through one provider-agnostic interface
(`scripts/generation.py`). Callers describe *what* they want; the router
decides *where* it happens.

| Provider | Needs | Produces | Cost |
|---|---|---|---|
| `comfyui` | `COMFYUI_URL` reachable | depicted imagery | free (your GPU) |
| `procedural` | ffmpeg only | abstract plates only | free |
| `gemini` | `GEMINI_IMAGE_ENABLED` + a key | depicted imagery | **metered** |
| `api` | `IMAGE_API_URL` + `IMAGE_API_KEY` | depicted imagery | **metered** |

### Routing

```
comfyui healthy?  ──yes──►  use it
      │ no
procedural healthy? ──yes──►  use it   (skipped if depicted imagery is required)
      │ no
gemini configured? ──yes──►  use it   (never reached unless you opt in)
      │ no
api configured?   ──yes──►  use it   (never reached unless you set both vars)
      │ no
    fail, with every attempt recorded on the job
```

Order is `GENERATION_ORDER` (default `comfyui,procedural,gemini,api`). Both
metered providers come after every free one deliberately: a transient local
failure must not silently start spending money. Each is inert until
deliberately configured, and each health check is configuration-only — pinging
a metered endpoint can itself cost money.

**Enabling `gemini` is a separate act from having a Gemini key.**
`GEMINI_API_KEY` already exists for the creative brief, so a key alone does
not switch on paid image generation; `GEMINI_IMAGE_ENABLED=1` does, and it
means "I am consenting to spend". Without it the provider is skipped exactly
like an absent one. It talks to the REST API over stdlib HTTP — no SDK is
added to the generation path — and records `cost_usd: null` rather than
inventing a dollar figure the API never reported.

**The workstation being off is a normal state, not an error.** `COMFYUI_URL`
has no default; unset means "no workstation", so a VPS run never probes
itself. When ComfyUI is unreachable the pipeline keeps working on procedural
plates.

A provider that fails is put in a cooldown (`GENERATION_COOLDOWN`, default
300s) that survives a restart, so a dead provider is not re-hammered on every
job. Being *unconfigured* is not a failure and earns no cooldown — there is
nothing to recover from.

### Depicted imagery

`procedural` cannot produce a depicted scene and never claims it can. When a
project's concept sets `procedural_visuals_acceptable: false`, the router
excludes abstract-only providers instead of generating assets the publication
gate is guaranteed to reject — with the PC off and no API configured, that
request fails loudly rather than quietly producing the wrong thing.

### Jobs

Every generation is a persisted job under `jobs/` (gitignored), keyed by a
hash of the request. That key is the idempotency guarantee: re-running an
identical request reuses the finished job instead of regenerating — and
possibly re-paying for — the same assets. Reuse is conditional on the assets
still being on disk. A job interrupted mid-flight resumes with its attempt
history intact, and writes are atomic, so a crash cannot leave an unreadable
job file.

```bash
python3 scripts/generation.py providers
python3 scripts/generation.py generate --prompt "..." --out DIR --count 2
python3 scripts/generation.py jobs
```

### ComfyUI setup

Set `COMFYUI_URL` to your workstation (e.g. `http://192.168.1.50:8188`).
Nothing is hard-coded and no endpoint is assumed.

The submitted graph is a template
(`config/comfyui_workflow.example.json`, override with `COMFYUI_WORKFLOW`),
with `%prompt%`, `%negative%`, `%width%`, `%height%`, `%seed%` and `%model%`
substituted in. Prompt text is JSON-escaped, so quotes and newlines cannot
corrupt the graph. Point `COMFYUI_WORKFLOW` at your own export to use a
different pipeline without touching code.

Do not expose ComfyUI to the public internet. Keep it on the LAN or behind a
VPN/SSH tunnel; it has no authentication of its own.

## Remote GPU worker

The VPS is always on and has no GPU. The workstation has a GPU and is off
most of the time, behind a home router. A remote worker reconciles those two
facts: **the VPS holds the queue and the truth, and the PC is a replaceable
worker that dials out to ask for work.**

```
VPS (control plane, always on)             PC (worker, GTX 1060 3GB)
  jobs/queue/<job_id>.json                   ./content-machine worker agent
  jobs/workers/home-gpu-01.json   <--------- outbound HTTPS only
  ./content-machine worker serve             ComfyUI on 127.0.0.1:8188
```

Nothing ever connects *to* the PC. There is no inbound path into the home
network, no port forwarded, and ComfyUI is never exposed or proxied — the
only thing that talks to it is the agent on that same machine, over
loopback. Losing the PC costs throughput, not data.

### Why a queue and not a provider

The generation router is synchronous, and the PC being off is the normal
state, not an error. Routing to a provider that cannot answer for six hours
would burn retry attempts and put a cooldown on a machine that is merely
asleep. A queued job instead simply **waits**: it consumes no attempt, trips
no cooldown, never fails, and says so — `WAITING_FOR_CAPABLE_WORKER`.

The connection back to the pipeline is the job id. A job id *is* a
`GenerationRequest` digest, which is already the router's idempotency key
(see [Jobs](#jobs)), so when a remote render finishes the control plane writes
an ordinary completed-job record and the next `visuals` run **reuses** it
instead of regenerating. Nothing above `generation.py` knows a worker exists.

### Setup

On the VPS, enroll the worker. The token is printed once; only its SHA-256 is
stored, so a leaked registry file does not leak the credential.

```bash
./content-machine worker enroll home-gpu-01 --capabilities comfyui,sd15
./content-machine worker serve                    # binds 127.0.0.1:8788
```

On the GPU machine, set three things — none of them has a default — and run
the agent:

```bash
CONTROL_PLANE_URL=https://vps.example      # where the VPS answers
WORKER_TOKEN=<the token printed above>     # keep it in .env, gitignored
COMFYUI_URL=http://127.0.0.1:8188          # that machine's own ComfyUI

./content-machine worker agent
```

`COMFYUI_URL` is not defaulted to loopback even here, where loopback is the
right answer: "unset" has to keep meaning "no ComfyUI on this host" on every
machine, or the VPS inherits a default that makes it claim to be a
workstation.

**The control plane binds loopback and speaks plain HTTP.** The bearer token
travels in a header, so put TLS in front of it — a reverse proxy, or reach it
through an SSH or WireGuard tunnel. Binding anything else has to be asked for
explicitly (`WORKER_API_BIND`), because it cannot be the safe default.

### Queueing work

```bash
./content-machine worker enqueue <video-id>            # from the visual plan
./content-machine worker enqueue --prompt "..." --out DIR
./content-machine worker jobs
./content-machine worker workers
./content-machine worker show <job-id>                 # full audit trail
./content-machine worker cancel <job-id>
./content-machine worker reap                          # expire leases by hand
```

Queueing is idempotent on the request digest: the same request queued twice
is one job, and a request that has *already* been generated is refused rather
than re-rendered.

### Job lifecycle

```
QUEUED ─► CLAIMED ─► SUBMITTED ─► RUNNING ─► UPLOADING ─► SUCCEEDED
   ▲          │           │           │           │
   │          └───────────┴───────────┴───────────┴──► RETRY_WAIT ─┐
   └──────────────────────────────────────────────────────────────┘
                                                      └──► FAILED
   any non-terminal state ───────────────────────────────► CANCELLED
```

The table of permitted moves is the only way a job changes state; anything
else is refused with a 409 and recorded. Every move appends to the job's
`transitions` log with who did it, when, which attempt, and why — so a bad
outcome is reconstructible afterwards rather than inferred.

| Rule | Why |
|---|---|
| An attempt is spent at **claim** time | `max_attempts` then bounds real work, not waiting. A job nobody can run spends nothing, however long it sits. |
| Each claim mints a fresh `lease_id` | Once a job is reaped and handed on, the previous holder's late upload is refused instead of overwriting work in progress. |
| Lease expiry counts as a spent attempt | A PC that claims and dies repeatedly is bounded, not an infinite loop. |
| A heartbeat does **not** renew a lease | Liveness is not progress. An agent that is breathing while its render has hung still loses the job. |
| `RETRY_WAIT` backs off exponentially | 60s, then 120s, capped at 15 min. |
| A worker can report a **permanent** failure | A missing checkpoint will still be missing in five minutes; burning two more GPU attempts on it only delays you finding out. |
| A worker may only report `SUBMITTED`/`RUNNING`/`UPLOADING` | It cannot declare its own job succeeded. Only the control plane does that, and only after verifying the manifest. |

`reap` runs at the top of every API request, so recovery needs no background
timer: whatever the worker does next performs it.

### Worker state

`ONLINE` / `STALE` / `OFFLINE` is **derived from heartbeat age** every time it
is read, never stored — a worker that loses power cannot leave an `ONLINE`
flag behind it. Default thresholds: `ONLINE` within 90s, `STALE` to 10 min,
`OFFLINE` beyond. A revoked worker is `OFFLINE` however recently it spoke.

Only an `ONLINE` worker whose capabilities cover the job's
`required_capabilities` is offered it, so a CPU-only worker is never handed a
ComfyUI render.

### Assets and manifests

Assets are uploaded one at a time as raw bytes with their SHA-256 declared in
a header, staged per job, and published only once a manifest accounts for
them. The manifest is built from the bytes on the worker and **re-verified
from the staged bytes on the VPS** — two independent hashes of the same file,
so a truncated upload is a refusal rather than a corrupt asset that fails
later in QC. A listed file that was never uploaded, an uploaded file missing
from the manifest, a filename that is not a plain safe image name, or a hash
that does not match are all refused.

Published assets are named exactly as a local ComfyUI render would be
(`gen_<digest>_NN.png`), so a remote result is indistinguishable downstream.

### What a worker still cannot do

A remote render is recorded as `produces_depicted: true` — ComfyUI does
produce depicted imagery, and that is the strongest claim any machine may
make. It does **not** set `production_grade`: that stays a human act (see
[The production-grade claim](#the-production-grade-claim)), so review is still
blocked until you make it. The publication gate also re-inspects the
artefacts themselves, so a worker that uploads a flat placeholder is caught
there regardless of what it reported.

## Visuals

`scripts/make_visuals.py` produces the deterministic procedural plates the
`procedural` provider serves — gradients, vignetting, dither — tuned to stay
above the QC blackdetect luminance floor:

```bash
python3 scripts/make_visuals.py --style deep-night --out projects/<id>/images --count 1
```

These are genuinely generated abstracts, **not photographs**, and must never
be described as such. Whether they are a real deliverable depends on the
concept:

- A dark plate for a brown-noise sleep video **is** the intended asset.
- A plate standing in for "rain on a window at night" **is not** — that
  concept needs depicted imagery.

Projects record this in `provenance.images.production_grade`, and the gate
is **fail-closed**: the claim must be explicitly `true`. An absent claim
blocks review, because "nobody decided" is not "no objection".

The gate does not rely on that claim alone. `make_visuals.py` stamps PNG
`tEXt` provenance into every plate it generates, and flat fills are measured
directly, so when a project's concept declares
`procedural_visuals_acceptable: false` the **images themselves** are
inspected. Editing `provenance.images` cannot relabel a placeholder as a
production asset.

This is what makes failover safe: falling back to `procedural` degrades
quality but cannot slip a placeholder past review as a production asset.

### The production-grade claim

`visuals` records the provider it used, and never claims an asset is
production-grade. It can only establish the negative — an abstract-only
provider means `production_grade: false`. Certifying the positive stays a
human act, which is what keeps `READY_FOR_REVIEW` a real boundary.

The claim describes *specific assets*. Generating different ones clears it,
so a human approval cannot be inherited by images it was never made about.
Re-running an identical request reuses the job, and the existing claim stands.

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

## Knowledge

Two layers, one direction of flow:

```
knowledge/                  canonical, human-readable, committed   (SOURCE)
  └── graphify-out/         graph.json + graph.html + GRAPH_REPORT.md   (DERIVED)
```

`knowledge/` is an **Obsidian vault** and the source of truth for durable
knowledge: doctrine, content niches, verified research, measured constraints,
and what past runs taught us. Open the folder as a vault and start at
`Knowledge Graph.md`.

**Graphify is a lens, not a database.** It reads the vault and derives how
those notes relate. The graph is rebuilt from scratch on every refresh and is
safe to delete. Operational state stays where it already lives —
`experiments/concepts.json` and `projects/<id>/metadata.json` — and a note
points at those files rather than restating them.

### Commands

```bash
./content-machine knowledge status              # notes, graph size, staleness
./content-machine knowledge refresh             # rebuild the graph from the vault
./content-machine knowledge open                # open the interactive graph
./content-machine knowledge query "what blocks the kids_sleep niche?"
```

`refresh` runs Graphify's extraction and clustering over `knowledge/` with the
`claude-cli` backend — it drives the local Claude Code session, so it needs no
extra Python package and spends nothing through a metered API. Override with
`--backend` (or `GRAPHIFY_BACKEND`) only deliberately. `--force` re-extracts
every note instead of using the incremental cache.

`status` reports `STALE` as soon as any note is newer than the graph, so a
refresh is never guessed at.

Refreshes are serialised by a lock in the output directory: two of them race on
the same files, and the loser silently overwrites the winner with a graph built
from an older corpus. A second refresh is refused rather than allowed to
clobber. Each refresh also re-names communities from scratch — reused labels go
stale the moment the corpus changes shape, and all three artefacts should come
out of one pass agreeing with each other.

### The three artefacts

Every refresh writes all three into `knowledge/graphify-out/`:

| Artefact | For | How to open |
|---|---|---|
| `graph.html` | humans | click the link in `Knowledge Graph.md`, or `knowledge open` |
| `GRAPH_REPORT.md` | humans | opens as a note inside Obsidian |
| `graph.json` | agents and tooling | `knowledge query`, or `graphify --graph` |

Obsidian will not render a local HTML file inside a note — an `<iframe>` at a
vault file is blocked — so the entry-point note uses a relative link, which
Obsidian hands to the system browser. That is the mechanism that actually
works; there is no embed to invent.

### Capturing knowledge

Agents persist what they learn instead of letting it die in a transcript:

```bash
./content-machine knowledge capture \
  --type finding \
  --title "Made-for-Kids RPM" \
  --summary "Realised RPM under MFK treatment is X." \
  --evidence "https://support.google.com/youtube/answer/..." \
  --confidence VERIFIED \
  --link "Kids Sleep" --link "Open Research Questions" <<'EOF'
The body, in markdown, read from stdin.
EOF
```

Notes land in `knowledge/Captured/` with frontmatter in Graphify's own ingest
convention (`type`, `confidence`, `captured_at`, `contributor`, `source_url`),
so the next refresh carries that provenance straight onto the graph node. The
same gate is available in Python as `knowledge.capture()`.

**The gate is fail-closed and narrow.** A capture is refused unless it declares
one of six types — `decision`, `finding`, `insight`, `constraint`, `entity`,
`topic` — cites its evidence, and carries enough substance to be worth
re-reading. Operational activity is deliberately not capturable: render logs,
QC verdicts, job status and asset counts belong in `metadata.json`.

### Two graphs

| Graph | Corpus | Location | Refresh |
|---|---|---|---|
| Code graph | this repo's source | `graphify-out/` | `graphify update .` |
| Knowledge graph | the vault | `knowledge/graphify-out/` | `./content-machine knowledge refresh` |

Both are gitignored and regenerable. If a graph and its source disagree, the
source wins — re-run rather than trusting the graph.

## Not built yet

Deliberately unimplemented, in dependency order:

- **Diffusion on this host** — still not viable, and deliberately not
  attempted: no GPU (`nvidia-smi` absent), 4 CPU cores, 3.8 GB RAM. The
  answer is not to shrink a model onto this box but to route the work
  elsewhere — see [Generation](#generation). The ComfyUI adapter is built and
  tested; it needs only `COMFYUI_URL` pointed at a machine that has a GPU.
- **Verified against a real ComfyUI server** — the adapter is exercised
  against a local stand-in implementing ComfyUI's submit/poll/download
  protocol, not against a real instance. Expect to adjust the workflow
  template for your checkpoint on first use. The same applies to the
  [remote worker](#remote-gpu-worker): the queue, leases, retries, uploads
  and manifests are tested end to end over real sockets against that
  stand-in, but no render has yet been produced by the GTX 1060.
- **Automatic deferral to the worker** — `visuals` still fails when depicted
  imagery is required and no synchronous provider can produce it; queueing
  the work for the PC is a deliberate `worker enqueue` rather than something
  the pipeline decides on its own. Wiring it into the routing failure path
  changes what a failed `visuals` run means, which is a decision, not an
  omission.
- **A depicted-imagery route that needs no GPU** — built: the `gemini`
  provider. Off by default; `GEMINI_IMAGE_ENABLED=1` is the deliberate opt-in
  to spending. Until you enable it (or point `COMFYUI_URL` at a GPU), the 18
  of 20 concepts with `procedural_visuals_acceptable=false` stop at asset
  generation, because procedural plates cannot satisfy them and nothing else
  can run here.
- **External image API** — the adapter exists and is disabled. Its
  request/response shape is generic; confirm it against your chosen vendor
  before enabling, since that is the point where spending starts.
- **AI script/title/description generation** — `generate.py` shows the
  provider pattern; wiring it into `metadata.json` costs API credits, so it
  is left off by default.
- **YouTube publishing** — intentionally absent. The pipeline stops at
  `READY_FOR_REVIEW` with a complete package; publishing stays a human step.
- **Analytics** — `publication_package.json` reserves `publish.publication_id`
  and the `experiment` block in `metadata.json` records cost/duration, so
  real data can be attached later. No performance data is fabricated.
