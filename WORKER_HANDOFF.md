# Remote ComfyUI Worker v0.1 — handoff

Status: **implemented, tested offline, never run against a real GPU.**
Detail: `README.md` → "Remote GPU worker"; invariants: `AGENTS.md`.

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

`./content-machine test` → **319 tests, 84.5s, OK** (229 existing + 90 new).

## Was real PC-to-ComfyUI connectivity tested?

**No — only against a local stand-in.** No real ComfyUI, no GPU and no second
machine were involved. What *was* real: HTTP over loopback sockets, so the wire
protocol is tested rather than mocked; the real `ComfyUIProvider`
submit/poll/download path; and the real CLI end to end, producing a
`SUCCEEDED` job with a verified manifest. Unverified: the GTX 1060 3GB, a real
checkpoint, a real render, WAN latency, TLS. Expect to adjust
`config/comfyui_workflow.example.json` on first use.

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

## Exact end-to-end test commands

```bash
./content-machine test                   # 319 tests
python3 -m unittest tests.test_worker    # 90 worker tests only

# Live loop on one host against a stand-in ComfyUI (what was actually run).
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
./content-machine worker enroll home-gpu-01 --capabilities comfyui,sd15
./content-machine worker serve &              # binds 127.0.0.1:8788
# prints WAITING_FOR_CAPABLE_WORKER while no worker is online:
./content-machine worker enqueue --prompt "a lit window" --out jobs/_smoke_out
CONTROL_PLANE_URL=http://127.0.0.1:8788 WORKER_TOKEN=<token> \
  COMFYUI_URL=http://127.0.0.1:8188 ./content-machine worker agent --once
./content-machine worker jobs
./content-machine worker show <job-id>        # audit trail + manifest
pkill -f "worker.py serve"; rm -rf jobs/_smoke_out jobs/workers jobs/queue
```

## Blockers and open decisions

1. **Blocker — 1080p on a 3GB card.** The workflow template feeds
   `%width%`/`%height%` straight into `EmptyLatentImage`, and
   `worker enqueue <video-id>` takes 1920x1080 from the video spec: a GTX 1060
   3GB will OOM, and SD1.5 degrades above ~768px. Lowering the enqueued
   dimensions is **not** a fix — the digest would stop matching what `visuals`
   computes and the reuse seam would break. The fix belongs in the workflow
   graph (small latent + upscale before `SaveImage`) and needs your VRAM and
   quality call.
2. **No automatic deferral from `visuals`** — it still fails when depicted
   imagery is required and nothing synchronous can serve it. Wiring queueing
   into that failure path changes what a failed run means, which the gate
   reads around: a decision, not an omission.
3. **`sd15` is declarative** — matching works and is tested, but nothing
   derives a capability from the checkpoint, so `enqueue` asks only for
   `comfyui` unless you pass `--capabilities comfyui,sd15`.
4. **TLS is yours to add** — the control plane binds loopback and speaks plain
   HTTP with the token in a header.
5. **Re-enroll before first real use** — the smoke-test enrollment was removed
   because its token was printed into a session transcript.
