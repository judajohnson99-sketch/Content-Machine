# Remote ComfyUI Worker v0.1 — handoff

Status: **implemented, tested offline, and verified end to end on real
hardware** (2026-09-15). Day-to-day operation: `WORKER_RUNBOOK.md`.
Detail: `README.md` → "Remote GPU worker"; invariants: `AGENTS.md`.
Shipped in `35724fb` (worker + queue) and `a6093ff` (runbook).

## What was implemented

The VPS owns a queue; the PC runs an agent that dials out, claims work under a
lease, renders on its own ComfyUI over loopback, uploads verified assets, and
reports. Nothing connects *to* the PC and ComfyUI is never exposed.

The remote GPU is deliberately **not** a generation provider: `Router.generate`
is synchronous and the PC being off is normal, so routing to it would burn
retry attempts and cooldown a sleeping machine. Instead a job id *is* a
`GenerationRequest` digest — already the router's idempotency key — so the
control plane writes an ordinary completed-job record on success and the next
`visuals` run reuses it. `project.py`, the router and the gate are untouched.

| Concern | How |
|---|---|
| Auth | token printed once at enroll, only its SHA-256 stored; identity resolved *from* the token (constant-time); revoke and rotate |
| Liveness | `ONLINE`/`STALE`/`OFFLINE` derived from heartbeat age on every read, never stored — a dead PC cannot leave an `ONLINE` flag behind |
| Lifecycle | `QUEUED → CLAIMED → SUBMITTED → RUNNING → UPLOADING → SUCCEEDED` plus `RETRY_WAIT`/`FAILED`/`CANCELLED`; table-driven, fail-closed, every move appending `{at, from, to, actor, attempt, detail}` |
| Offline | no capable `ONLINE` worker means nothing touches the job: attempt stays 0, never fails, reports `WAITING_FOR_CAPABLE_WORKER`. An attempt is spent at *claim* time |
| Leases | fresh `lease_id` per claim, so a reaped job's previous holder is refused (409) instead of overwriting work in progress. Expiry → `RETRY_WAIT` (backoff) → `QUEUED`, or `FAILED` when spent. A heartbeat does **not** renew a lease |
| Manifests | hashed on the worker, re-verified from the staged bytes on the VPS; missing, unaccounted, mis-hashed, oversized, unsafe-named and non-image uploads all refused |
| Governance | no worker path sets `production_grade`; a remote render claims only `produces_depicted: true` and the gate still re-inspects the artefacts |

## Test results

`./content-machine test` → **319 tests, ~85s, OK** (229 existing + 90 new).

## Real-hardware verification (2026-09-15)

The full path was exercised against the real workstation, not a stand-in:

```
VPS queue → SSH tunnel → PC worker → local ComfyUI → DreamShaper_8_pruned
→ GTX 1060 3GB → verified upload → VPS manifest re-verification → SUCCEEDED
```

- **Five real renders succeeded** at 512x512, each ~23s from `RUNNING` to
  `UPLOADING`, producing ~320-370 KB PNGs — real images, not placeholder
  bytes. Final verification job: `dcddfedc4595072e` (`jobs/queue/`), whose
  transition log shows the whole lifecycle under a single attempt.
- **Offline waiting and recovery hold on real hardware.** A job queued while
  the PC worker was down stayed `QUEUED` at attempt 0 with
  `WAITING_FOR_CAPABLE_WORKER`, then was claimed and completed on the
  worker's return — its first transition after `QUEUED` is `CLAIMED` at
  attempt 1, so the wait cost nothing.
- **Token rotation was exercised for real.** `home-gpu-01` was re-enrolled
  with `--rotate` at `2026-09-15T05:41:41Z` (`token_issued_at` in
  `jobs/workers/home-gpu-01.json`) and the final verification job ran under
  the new credential four minutes later. The transcript-leaked token from the
  earlier smoke test is dead.
- **Two real failure modes were observed and handled as permanent failures**
  (no wasted GPU attempts): a missing `config/comfyui_workflow.example.json`
  on the worker machine, and `ComfyUI rejected the workflow: HTTP 400` before
  the checkpoint name matched an installed model.

Still unverified: 1920x1080 latent generation on this card (see blocker 1),
WAN latency from a worker that is not on the same tunnel, and TLS.

## Files changed

```
 M .env.example            +24   separate VPS and GPU-machine blocks
 M AGENTS.md               +67   layer map, worker invariants, security
 M CLAUDE.md               +11   one new non-negotiable; test count 174 -> 319
 M README.md              +190   setup, lifecycle, troubleshooting
 M content-machine          +6   `worker` subcommand
 M scripts/generation.py   +37   ComfyUIProvider.generate(progress=) + _notify
?? scripts/worker.py          1213  control plane: queue, leases, states, CLI
?? scripts/worker_api.py       260  HTTP transport only
?? scripts/worker_agent.py     347  runs on the GPU machine; outbound only
?? tests/test_worker.py       1001  90 tests
?? knowledge/Captured/Remote GPU Work Is a Queue Not a Provider.md
```

## Test commands

```bash
./content-machine test                   # 319 tests
python3 -m unittest tests.test_worker    # 90 worker tests only
```

For the real two-machine loop, follow `WORKER_RUNBOOK.md`. The single-host
loop below needs no GPU and is still the fastest way to smoke-test a change
to the control plane:

```bash
# Live loop on one host against a stand-in ComfyUI.
# Do NOT point `enqueue` at a real project here: it would write 12-byte
# placeholder bytes into projects/<id>/images/.
cd /root/projects/content-machine
python3 -c "
import sys
from http.server import ThreadingHTTPServer
sys.path[:0] = ['tests', 'scripts']
from test_generation import FakeComfyHandler
ThreadingHTTPServer(('127.0.0.1', 8188), FakeComfyHandler).serve_forever()
" &                                           # stand-in ComfyUI on :8188
./content-machine worker enroll smoke-gpu-01 --capabilities comfyui,sd15
./content-machine worker serve &              # binds 127.0.0.1:8788
# prints WAITING_FOR_CAPABLE_WORKER while no worker is online:
./content-machine worker enqueue --prompt "a lit window" --out jobs/_smoke_out
CONTROL_PLANE_URL=http://127.0.0.1:8788 WORKER_TOKEN=<token> \
  COMFYUI_URL=http://127.0.0.1:8188 ./content-machine worker agent --once
./content-machine worker jobs
./content-machine worker show <job-id>        # audit trail + manifest
./content-machine worker revoke smoke-gpu-01    # retire the throwaway worker
pkill -f "worker.py serve"; rm -rf jobs/_smoke_out
```

Enroll a *throwaway* worker id for this and revoke it afterwards — do not
rotate `home-gpu-01`, whose token is deployed on the PC, and do not `rm -rf
jobs/workers` or `jobs/queue`: those now hold the real worker registry and
the real job history.

## Update 2026-09-17

Blockers 1-3 below are resolved: the default workflow is now
`config/comfyui_workflow_lowvram_upscale.json` (bounded latent + in-graph
upscale, so `enqueue <video-id>` at 1920x1080 is safe on the 3 GB card);
`run_visuals`/`run_scenes` queue here automatically (exit 2, never FAILED)
when depicted imagery is required, no synchronous provider serves it and a
`comfyui` worker is enrolled; and the agent's heartbeat now reports
ComfyUI reachability, GPU/VRAM, installed checkpoints and the checkpoint it
will use, from which `worker.depicted_readiness()` derives the readiness the
concept catalogue and the web control center show. Agent version 0.2. TLS
(blocker 4) is unchanged: SSH tunnel.

## Blockers and open decisions

1. **Blocker — 1080p on a 3GB card.** 512x512 is now *verified* on the GTX
   1060 3GB; 1920x1080 has not been attempted and is not expected to fit. The
   workflow template feeds `%width%`/`%height%` straight into
   `EmptyLatentImage`, and `worker enqueue <video-id>` takes 1920x1080 from
   the video spec, so that path is still untried. Lowering the enqueued
   dimensions is **not** a fix — the digest would stop matching what `visuals`
   computes and the reuse seam would break. The fix belongs in the workflow
   graph (small latent + upscale before `SaveImage`) and needs your VRAM and
   quality call. Until then, real GPU work is queued with explicit
   `--width`/`--height`, which is what the runbook shows.
2. **No automatic deferral from `visuals`** — it still fails when depicted
   imagery is required and nothing synchronous can serve it. Wiring queueing
   into that failure path changes what a failed run means, which the gate
   reads around: a decision, not an omission.
3. **`sd15` is declarative** — matching works and is tested, but nothing
   derives a capability from the checkpoint, so `enqueue` asks only for
   `comfyui` unless you pass `--capabilities comfyui,sd15`.
4. **TLS is yours to add** — the control plane binds loopback and speaks plain
   HTTP with the token in a header. The verified runs reached it through an
   SSH tunnel (`WORKER_RUNBOOK.md` step 3), which is the supported answer for
   now.
5. ~~**Re-enroll before first real use**~~ — **done.** `home-gpu-01`'s token
   was rotated on 2026-09-15 and the leaked smoke-test credential is dead.
