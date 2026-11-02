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
4. `python3 scripts/vision_setup.py pull` - downloads `qwen3-vl:2b` (about
   1.9 GB; needs Ollama 0.12.7 or newer, which `doctor` checks) and
   `moondream` (about 1.7 GB, small context, older). Both tags appear in
   Ollama's public library; a pull on the PC is the real confirmation. If one
   is rejected, change `QUALITY_VISION_MODELS`.
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
  together (file lease plus a check that ComfyUI's queue is empty). ComfyUI's
  models are freed first via its `/free` endpoint; ComfyUI's source shows that
  call only sets a flag applied between prompts, so it can never interrupt a
  running render, but how much VRAM it returns on this card is unmeasured. A
  batch of images takes the GPU once: the model loads once, stays resident for
  at most a minute between images, and is unloaded at the end. If loading still fails
  with out-of-memory, the job records `oom` and "Not evaluated"; try the
  fallback model or a smaller `QUALITY_VISION_IMAGE_MAX_PX`.
- Some Qwen3-VL builds "think" before answering and can spend the whole
  token budget on it. An empty reply that carries reasoning is retried once
  with `think: false` (an Ollama parameter); if it is still empty, or the
  answer is not the requested JSON, the image is "not evaluated", never
  guessed at.
- A busy ComfyUI defers judging (`gpu_busy`); the render itself is never
  delayed or failed by vision.

## Validating 768x432 on the GTX 1060 3GB (Windows, run only when the desktop is on)

Goal: show that one 768x432, batch-size-1 image renders on the 3GB card in
ComfyUI's low-VRAM mode, lands through the queue, and is usable as a scene
picture. Nothing here needs a paid API, publishes anything, sets
`production_grade`, or turns on `WORKER_VISION` (leave it unset).

What the code does on this card, so the result is read correctly: with
`COMFYUI_LATENT_MAX_PIXELS` **unset**, a 16:9 request at least 768 wide on a
2.5-3 GB card gets a native 768x432 latent (`ComfyUIProvider._native_size`);
setting it (the optional 262144 default in this runbook) forces about 680x384
instead and would make the test pass for the wrong reason. A card under 4 GB
also selects `config/comfyui_workflow_lowvram_tiny.json` (no in-graph upscale;
the finish to the requested size happens on CPU) unless `COMFYUI_WORKFLOW`
pins a template. Batch size 1 is `--count 1`, which the provider renders as
one prompt per image.

### Before touching the PC (VPS, no GPU needed)

    ./content-machine worker workers          # the home worker should be ONLINE
    ./content-machine assets status           # gpu.state should become "ready"

If no worker is enrolled: `./content-machine worker enroll home-gpu-01
--capabilities comfyui,sd15`, put the token in the PC `.env`, and start the
tunnel and control plane as in "Start the system". Update the PC checkout
first (it must carry the current `scripts/` and `config/`).

Offline check of the template the card will use (no GPU, no network):

    ./content-machine worker validate-workflow config/comfyui_workflow_lowvram_tiny.json

### On the Windows PC (PowerShell)

1. Start ComfyUI in low-VRAM mode and leave it running:

       cd C:\path\to\ComfyUI
       python main.py --lowvram --listen 127.0.0.1 --port 8188

2. In a second window record the card, then watch memory during the render:

       nvidia-smi --query-gpu=name,memory.total,memory.used --format=csv
       nvidia-smi --query-gpu=memory.used --format=csv -l 1 > vram.csv

3. Confirm ComfyUI sees the card and what it reports:

       curl http://127.0.0.1:8188/system_stats

   Expect one device, a GTX 1060, `vram_total` about 3 GB.

4. Worker `.env` must contain `CONTROL_PLANE_URL`, `WORKER_TOKEN`,
   `COMFYUI_URL=http://127.0.0.1:8188`, and **no** `COMFYUI_LATENT_MAX_PIXELS`
   and no `WORKER_VISION`. Start the tunnel and `python scripts\worker_agent.py`.

### Queue the test (VPS)

    ./content-machine worker enqueue \
      --prompt "a small brass key resting on dark green velvet, soft light, detailed photograph" \
      --negative "blurry, distorted, low quality, text" \
      --out jobs/gpu-768x432 --count 1 --width 768 --height 432 \
      --model "DreamShaper_8_pruned.safetensors" --capabilities comfyui,sd15
    ./content-machine worker jobs

### Record and judge

| Check | Pass when |
|---|---|
| Job | reaches SUCCEEDED on the first attempt (an OOM is a permanent failure, not a retry) |
| Native size | the job's workflow/notes say 768x432, not 680x384 |
| Output | `ffprobe` of the image reports 768 x 432 |
| VRAM | `vram.csv` peak stays under about 2.9 GB with no CUDA OOM in the ComfyUI log |
| Time | seconds per image recorded (512x512 baseline is about 23 s) |
| Image | not flat or black: the manifest verification and `qc.assess_image` pass |
| Heartbeat | the worker stayed ONLINE through the render |

If it runs out of memory: record the peak, set `COMFYUI_LATENT_MAX_PIXELS=262144`
on the PC, rerun, and note that 768x432 is **not** viable on this card (the
pipeline then renders about 680x384 and finishes on CPU). Do not re-queue the
failed job; enqueue a new one.

### Then the pipeline path (one scene)

    ./content-machine assets search <video-id> <scene-id> --source generated
    ./content-machine assets select <video-id> <scene-id> <generated-candidate-id> --actor you
    ./content-machine scenes <video-id>

Expect a depicted image for that scene only, a recorded seed and request
digest, and no change to `production_grade` (still unset until a person sets
it). With the PC off the same `select` is recorded as deferred and `scenes`
reports WAITING_FOR_GPU_WORKER instead of failing.

Record the outcome (peak VRAM, seconds, job id, pass or fail) in
`.paul/STATE.md`. This section is a procedure, not evidence: it has not been
run on the hardware.

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
