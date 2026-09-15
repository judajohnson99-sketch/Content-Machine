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

### 4. PC — Start GPU worker

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

Missing checkpoint:
Use an installed ComfyUI checkpoint. Current tested model:

DreamShaper_8_pruned.safetensors

Do not expose ComfyUI publicly.

## Known hardware limit

The current PC uses a GTX 1060 3GB. DreamShaper/SD1.5 at 512x512 is verified.
Do not assume direct 1920x1080 latent generation will fit in VRAM. A future
workflow should render smaller and upscale for 1080p output.

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
