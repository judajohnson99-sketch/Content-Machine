# Remote GPU Worker — Quick Runbook

The VPS owns the queue. The PC provides the GPU through local ComfyUI.
The PC connects outbound to the VPS through an SSH tunnel. Never expose
ComfyUI or port 8788 directly to the internet.

## Start the system

### 1. PC — Start ComfyUI

Start ComfyUI normally and confirm it is available at:

http://127.0.0.1:8188

Keep this terminal running.

### 2. VPS — Start worker control plane

From:

/root/projects/content-machine

Run:

./content-machine worker serve

Expected:

[INFO] worker control plane on http://127.0.0.1:8788

Keep this terminal running.

### 3. PC — Start SSH tunnel

Run:

ssh -N -L 8788:127.0.0.1:8788 root@74.208.207.251

Keep this terminal running.

### 4. PC — Update the checkout, then start the GPU worker

The agent renders through the same ComfyUIProvider as the VPS and reads its
workflow template from its own checkout, so the checkout must carry the
current `scripts/generation.py`, `scripts/worker.py`,
`scripts/worker_agent.py` and `config/comfyui_workflow_lowvram_upscale.json`
(the low-VRAM default: bounded latent + in-graph upscale). Pull, or copy
those files from the VPS, before starting.

`.env` on the PC needs only:

CONTROL_PLANE_URL=http://127.0.0.1:8788
WORKER_TOKEN=<the token enroll printed>
COMFYUI_URL=http://127.0.0.1:8188

`COMFYUI_MODEL` is optional: unset, the agent uses the first checkpoint
ComfyUI reports installed and says which in its heartbeat. Set it to pin one
(e.g. DreamShaper_8_pruned.safetensors). `COMFYUI_LATENT_MAX_PIXELS` is
optional too (default 262144 = 512x512, verified on the GTX 1060 3GB).

Run:

cd ~/content-machine-worker
set -a
source .env
set +a
python3 scripts/worker_agent.py

Expected when idle:

[INFO] no work: NO_JOB_READY

Keep this terminal running.

## Check workers

On VPS:

./content-machine worker workers

The GPU worker should eventually show ONLINE.

## Check jobs

On VPS:

./content-machine worker jobs

For details:

./content-machine worker show <JOB_ID>

## Queue a DreamShaper test

./content-machine worker enqueue \
  --prompt "a small brass key resting on dark green velvet, dramatic soft lighting, detailed photograph" \
  --negative "blurry, distorted, low quality" \
  --out jobs/gpu-test \
  --count 1 \
  --width 512 \
  --height 512 \
  --model "DreamShaper_8_pruned.safetensors" \
  --capabilities comfyui,sd15

## Normal offline behavior

If the PC worker is off, a compatible job should remain:

QUEUED
WAITING_FOR_CAPABLE_WORKER

It should stay at attempt 0 and automatically run when the worker returns.

## Common problems

HTTP 401 / unrecognised worker token:
The token loaded by the PC worker is invalid or has been rotated. Update
WORKER_TOKEN in ~/content-machine-worker/.env, reload .env, and restart
worker_agent.py.

WAITING_FOR_CAPABLE_WORKER:
No compatible worker is currently ONLINE. Check the PC worker, SSH tunnel,
ComfyUI, and required capabilities.

Missing checkpoint / ComfyUI rejected the workflow (HTTP 400):
Use an installed ComfyUI checkpoint. Current tested model:

DreamShaper_8_pruned.safetensors

ComfyUI workflow template not found:
The PC checkout is missing config/comfyui_workflow.example.json. The agent
renders through the same ComfyUIProvider as the VPS and reads the template
from its own checkout. Restore the file, or set COMFYUI_WORKFLOW in
~/content-machine-worker/.env. This is treated as a permanent failure, so
the job will not retry - fix it and enqueue again.

Do not expose ComfyUI publicly.

## Known hardware limit

The current PC uses a GTX 1060 3GB. DreamShaper/SD1.5 at 512x512 is verified
and takes about 23 seconds per image, end to end. The default workflow
therefore never asks the card for more than 512x512 worth of latent: a 1080p
request renders at 680x384 and is upscaled to 1920x1080 inside the graph
(`config/comfyui_workflow_lowvram_upscale.json`). A CUDA out-of-memory is
reported as a permanent failure - lower COMFYUI_LATENT_MAX_PIXELS on the PC
rather than re-queueing.

## Vision on the GPU worker (optional, not yet validated on hardware)

A local vision model can judge each rendered image (prompt adherence,
composition, artifacts, consistency) and attach advisory findings to the
review. It is off until you do the steps below, and nothing in the pipeline
depends on it. Findings never block; the human review gate is unchanged.

Minimum steps once the PC is on (all on the PC, in the worker checkout):

1. `python3 scripts/vision_setup.py doctor` - reports what is missing
   (Ollama, GPU, models, ComfyUI, free VRAM, `.env`).
2. Install Ollama (https://ollama.com/download) if the doctor says so.
3. `python3 scripts/vision_setup.py env --write` - appends the vision keys to
   `.env` without touching existing ones.
4. `python3 scripts/vision_setup.py pull` - downloads `qwen3-vl:2b` and the
   fallback. Model tags were not checked against the registry from the dev
   environment; if one is rejected, change `QUALITY_VISION_MODELS`.
5. Run `doctor` again, then `python3 scripts/vision_setup.py smoke` (judges
   two test images through the real GPU lease; it defers if ComfyUI is busy).
6. `python3 scripts/vision_calibration.py run --model qwen3-vl:2b --apply`
   to measure the model. Until this passes per dimension the findings stay
   labelled "not yet calibrated" and composition/consistency stay at info.
   Then label some real renders (`label-template`, `import-real`) and re-run.
7. Add `WORKER_VISION=1` to `.env` (step 3 does) and restart the agent.
   Optionally `python3 scripts/vision_setup.py service` prints systemd units
   for the agent and the outbound SSH tunnel.

Things to watch, because they could not be tested without the hardware:

- The GTX 1060 3GB is tight for `qwen3-vl:2b`. Vision and ComfyUI never run
  together (file lease), and ComfyUI's models are freed first via its `/free`
  endpoint - that call's effect on VRAM is unverified. If loading still fails
  with out-of-memory, the job records `oom` and "Not evaluated"; try the
  fallback model or a smaller `QUALITY_VISION_IMAGE_MAX_PX`.
- Some Qwen3-VL builds emit "thinking" text before the answer. Answers that
  are not the requested JSON are rejected as `unparseable`, not guessed at.
- A busy ComfyUI defers judging (`gpu_busy`); the render itself is never
  delayed or failed by vision.

## Automatic queueing from the pipeline

`visuals`/`scenes` (and therefore Produce) queue depicted-imagery work here
by themselves when no synchronous provider can serve it and a worker is
enrolled. The web control center shows the job under "GPU worker" in the
project's workspace; once it lands, "Continue production" (or re-running
Produce) picks the image up without regenerating.

## Verified configuration

Real hardware path verified:

VPS queue
→ SSH tunnel
→ PC worker
→ local ComfyUI
→ DreamShaper
→ GTX 1060 3GB
→ worker upload
→ VPS manifest verification
→ SUCCEEDED

Final verification job:

dcddfedc4595072e — SUCCEEDED
