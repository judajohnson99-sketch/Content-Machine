---
description: "content-machine — current position and resume point"
type: ProjectState
about: "content-machine"
---

# Project State

The single resume file.

## Current Position

Milestone: Perceptual Quality System, phase 1.5 - vision layer prepared, NOT validated on
hardware (see Session Continuity); merged onto the creator-workflow milestone below.

Milestone: the dashboard is the product - a production now starts from a
plain-language goal and runs to a reviewable deliverable without the CLI.
Done: goal -> concept -> project -> research brief derivation
(`scripts/goal.py`, POST /api/v1/projects/from-goal/); a piecewise renderer
that removes the 24-scene ceiling, so a 30-minute video is 120 shots with
real motion and dissolves instead of a slideshow; sourced findings now
produce production directives (shot length, movement style, dissolve length,
audio layers) that the storyboard and sound design actually apply, recorded
with their evidence in `research_influence.json` and shown in the dashboard;
procedural plates vary per scene prompt and carry real structure; Kdenlive
export, permanent deletion and "produce at full length" are dashboard
actions. Validated through the dashboard: a 90-second 1080p Dreamdrip
excerpt, QC PASS 15/15, 15 shots across 8 motions, four cleared audio
layers, editable Kdenlive archive.

Validation blockers (needs the owner): real sourced research cannot run on
this host because the configured providers refuse (Gemini resource exhausted;
Anthropic has no credit balance). The connected `home-gpu-01` is reachable
with ComfyUI, but its deployed worker still reports v0.2 and does not include
the current media-sync/low-VRAM agent; the validated browser generation was
therefore a real ComfyUI job, but not proof of the updated multi-image/transfer
path. The canonical suite also exposes an idempotent-job reuse defect: a
completed job can retain an old project output path, leaving a new production
without its images.

Next: update the PC worker checkout/agent, provide one working search
provider, then repair/revalidate generation-job output ownership before
claiming the full creator workflow complete.

Perceptual quality (phases 1 and 1.5): NOT validated on hardware. Built and mock-tested: a local Ollama vision provider with model chain, schema validation and judgement cache (`scripts/vision.py`); GPU lease/ComfyUI coordination so vision never blocks a render (`scripts/gpu_coordination.py`); the GPU worker judges images after rendering and ships sha-bound advisory evidence that the control plane re-validates; `quality.py` reports adherence/composition/artifacts/consistency with PARTIAL and Not-evaluated states; Workspace and Review Center show them. Calibration harness plus a constructed 82-case dataset (`calibration/vision`); `scripts/vision_setup.py` for doctor/env/pull/smoke/ systemd. No model is calibrated; the dataset labels are constructed, not human-reviewed. Steps for the GPU PC are in WORKER_RUNBOOK.md. Blockers unchanged (need the owner): no working search provider for sourced research; no depicted-image provider, so plates are abstracts. The GPU PC was not reachable in this phase, so nothing ran against real Ollama or ComfyUI. Next, when the GPU PC is on: run the runbook's vision steps, then `vision_calibration.py run --model qwen3-vl:2b --apply`, label some real renders (`label-template`, `import-real`) and re-run; verify ComfyUI `/free` and VRAM fit on the GTX 1060 3GB. Then the earlier next steps (search provider, narrated production, owner-media selection). 

## Standing decisions

| Decision | Impact |
|---|---|
| Build outward from the existing Create pipeline, never replace it | Every plan extends rather than rewrites |
| First milestone is the full loop, not Publish alone | Phases must reach Learn, not stop at upload |
| Always-on VPS target; the local PC is optional better compute | Every stage tolerates the PC being off |
| Defer a persistent datastore | JSON-on-disk until architecture forces the change |
| `scripts/*.py` is the one domain layer | webapp/frontend are control surfaces, never a second authority on pipeline state |

Safety boundaries (production_grade, fail-closed gate, audio rights, paid
provider last and off, stdlib render path, graph is a lens) live in CLAUDE.md
and are not restated here.

## Deferred

| Issue | Revisit |
|---|---|
| Persistent datastore for research/publishing/performance history | When JSON-on-disk demonstrably strains |
| Multi-platform publishing beyond YouTube | After the YouTube loop is proven |
| Publishing cadence target | After the first full cycle produces real data |
| Monetization | After the loop runs unattended and consistently |

## Session Continuity

Latest request: Phase 1.5 - prepare the vision-model layer for the GPU worker
without GPU access. Delivered the provider, coordination, worker evidence,
quality integration, UI, calibration dataset and harness, setup tooling and
docs; two knowledge notes captured (decision: evidence travels with the
completed job; insight: tiny calibration sets reward rule search). `graphify`
is not installed here, so graphs and `knowledge refresh` were not run.
Validation projects `dreamdrip-*` are local and untracked.

Creator-workflow handoff (merged from main): Latest request: finish validation only. Creator-step workspace, media library, Image Lab save/use, owner-media sync APIs, semantic indexing, rights-aware Openverse discovery, safe GTX-1060 generation sizing, provenance and worker completion gates are implemented in the shared domain/UI layers. Browser validation covered dashboard, new-production direction selection, Image Lab, and a real ComfyUI one-image job. Frontend typecheck/tests and Django tests pass; the host canonical suite runs 790 tests with 3 failures described above. Checkpoint: `/tmp/content-machine-creator-checkpoint-20261007.tar.gz`. No commits. 
