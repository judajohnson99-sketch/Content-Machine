# Content Machine — architecture for coding agents

Concise map for agents working in this repo. `README.md` is the user-facing
manual; `CLAUDE.md` carries the non-negotiable rules. Read those too.

## Layers

```
CLI (./content-machine)           thin wrapper; fixes the working directory
  └── scripts/project.py          lifecycle orchestration + the publication gate
        ├── scripts/generation.py provider routing for images
        ├── scripts/audio.py      layered audio composition + rights checks
        ├── scripts/render.py     spec + assets -> MP4 (ffmpeg)
        ├── scripts/qc.py         probes a finished MP4 for faults
        └── scripts/make_visuals.py procedural plates + provenance forensics
scripts/experiment.py             content hypotheses, ranking, scaffolding
scripts/knowledge.py              Obsidian vault + derived Graphify graph
generate.py                       standalone article generator (pre-existing)
```

`project.py` imports the subsystems rather than reimplementing their rules,
so each rule exists in exactly one place. Keep it that way.

## Directories

| Path | What |
|---|---|
| `scripts/` | all pipeline code |
| `tests/` | unittest suites, one per subsystem |
| `projects/<video-id>/` | one video: spec, metadata, assets, output, logs |
| `experiments/` | concepts.json + reusable VideoSpec templates |
| `config/` | example spec and the ComfyUI workflow template |
| `jobs/` | generation job state (runtime, gitignored) |
| `knowledge/` | Obsidian vault: the canonical human-readable knowledge layer |
| `knowledge/graphify-out/` | knowledge graph derived from the vault (gitignored) |
| `graphify-out/` | code graph derived from this repo's source (gitignored) |

## The two contracts

- **`video_spec.json`** is the renderer's contract and stays minimal.
- **`metadata.json`** holds everything editorial: concept, titles, status,
  provenance, plans, review checklist.

Content structure can evolve without touching the renderer. Do not push
editorial fields into the spec.

## Generation architecture

```
caller ──► GenerationRequest ──► Router ──► Provider ──► assets + persisted job
                                   │
       comfyui ─► procedural ─► gemini ─► api   (order: GENERATION_ORDER)
```

- Providers implement `configured()`, `health()`, `generate()` and declare
  `produces_depicted` / `costs_money`. Vendor specifics never leak out.
- `require_depicted` on a request excludes abstract-only providers.
- Jobs are keyed by a hash of the request — that is the idempotency key.
  Writes are atomic; interrupted jobs resume with history intact.
- Failed providers get a persisted cooldown. Unconfigured ones do not.
- Metered providers (`gemini`, `api`) sit after every free one and are inert
  until deliberately configured. `gemini` needs `GEMINI_IMAGE_ENABLED` on top
  of a key, because `GEMINI_API_KEY` already exists for the creative brief and
  must not silently start buying images too.

Adding a provider: subclass `Provider`, add it to `build_providers()`, add it
to `DEFAULT_ORDER` if it should be routed by default, and add tests using the
`FakeProvider` harness in `tests/test_generation.py`.

## Pipeline stages

```
IDEA ─► RESEARCH ─► BRIEF ─► CONTENT ─► VISUAL/AUDIO PLAN ─► ASSET GENERATION
  ─► ASSEMBLY ─► QC ─► PACKAGING ─► READY_FOR_REVIEW ─► [human] ─► PUBLISH
```

Implemented today: visual/audio plan → generation → assembly → QC → packaging
→ READY_FOR_REVIEW. Research, content generation, publishing and analytics are
not built; see README "Not built yet".

## Knowledge layer

`knowledge/` is an Obsidian vault and the **source of truth** for durable
knowledge. `knowledge/graphify-out/` holds the graph derived from it. Flow is
one-way: notes → graph. Nothing may depend on the graph that is not
recoverable from the notes.

Do not confuse the two graphs. `graphify-out/` at the repo root is the **code**
graph (how the pipeline works). `knowledge/graphify-out/` is the **knowledge**
graph (how topics, research, decisions and constraints relate).

### Using it

Before substantive work, retrieve a ranked brief instead of reading the corpus:

```bash
./content-machine knowledge context "add a second narration voice"
```

`context` ranks the vault against the task (IDF-weighted lexical overlap,
expanded one hop through the knowledge graph), and prints the active position
from `.paul/STATE.md`, recent `projects/<id>/metadata.json` status, one-line
note summaries with paths, and `file:line` pointers from the code graph - all
under a hard token budget. It reads; it never writes. `--full N` adds bodies,
`--budget N` raises the cap. The `context` skill in `.claude/skills/` wires it
into Claude Code.

To follow a specific relationship rather than gather context for a task,
consult the graph directly:

```bash
./content-machine knowledge query "what blocks the kids_sleep niche?"
graphify path "Bedtime Stories" "Local Narration Capability" --graph knowledge/graphify-out/graph.json
graphify explain "YouTube Inauthentic Content Policy" --graph knowledge/graphify-out/graph.json
```

`knowledge/graphify-out/GRAPH_REPORT.md` gives hubs, communities and orphans in
prose; `graph.json` is the machine-readable form (nodes carry `type`,
`confidence`, `captured_at`, `contributor` and `source_url` from note
frontmatter). Run `./content-machine knowledge status` first — a `STALE` graph
means notes changed since it was built.

### Capturing into it

When work produces something durable — a verified research finding, a decision
and its reasoning, an insight validated against measured data, a newly measured
constraint, a recurring entity or topic — persist it:

```bash
./content-machine knowledge capture --type finding --title "..." \
  --summary "..." --evidence "<file, run, URL or measurement>" \
  --confidence VERIFIED --link "Adult Sleep" <<'EOF'
body markdown
EOF
```

Importable as `knowledge.capture()`. The gate is fail-closed: an unrecognised
type, missing evidence or a thin body is refused.

Capture, then refresh. Do not run two refreshes at once — the second is refused
by a lock, because a race between them ends with the older corpus winning.

**Do not capture operational activity.** Render logs, QC verdicts, job status,
asset counts and per-project editorial fields belong in
`projects/<id>/metadata.json` and `experiments/concepts.json`, which stay
authoritative. A note points at those files; it does not restate them.

## Autonomy boundaries

Safe to do unattended: generate assets, render, QC, package, reach
`READY_FOR_REVIEW`, run the test suite.

Requires a human: setting `production_grade`, enabling either metered image
provider (`gemini`, `api`), publishing anything publicly, and any spending.

`READY_FOR_REVIEW` is the hard stop. Nothing downstream of it is automated,
and no code path may set a production-grade claim on a person's behalf.

## Testing

```bash
./content-machine test          # full suite
python3 -m unittest tests.test_generation   # routing/providers/jobs only
python3 -m unittest tests.test_knowledge    # capture gate + corpus accounting
```

Tests inspect real media with ffprobe and exercise the real CLIs. External
services are never contacted: the ComfyUI and Gemini adapters run against
local stand-in HTTP servers, and router logic uses `FakeProvider`. No test
requires a GPU, the workstation, or a paid API.

Tests that redirect module state (`generation.JOBS_DIR`,
`project.PROJECTS_DIR`) must restore it in `tearDown`.

## Security

Secrets come from the environment (`.env`, gitignored). Never commit,
hard-code, or log a key. `COMFYUI_URL` has no default — do not add one.
Do not expose ComfyUI publicly; it has no authentication.

## Deployment boundary

The VPS (4 cores, 3.8 GB RAM, no GPU) is the always-on orchestration and
reliability layer — it renders, QCs, packages, and serves procedural plates.
It is not a diffusion host; do not attempt to install one there. Heavy
generation belongs on the workstation's ComfyUI or an external API.
