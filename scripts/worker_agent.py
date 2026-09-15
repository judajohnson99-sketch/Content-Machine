#!/usr/bin/env python3
"""The GPU worker agent. Runs on the machine with the graphics card.

This is the only component that runs outside the VPS, and it is deliberately
the dumbest one: it dials out, asks for work, renders it with the local
ComfyUI, uploads the result, and says what happened. It holds no queue, makes
no scheduling decisions and keeps no state worth backing up - which is what
makes the machine it runs on replaceable. Losing the PC loses nothing except
throughput.

    while True:
        job = POST /v1/claim                  # outbound; nothing listens here
        if not job: sleep(poll_seconds)
        render it on ComfyUI at COMFYUI_URL   # loopback on this machine
        upload each asset, then the manifest
        POST /v1/jobs/<id>/complete           # or /fail

Only three things are configured, none of them with a default:

    CONTROL_PLANE_URL   where the VPS answers, e.g. https://vps.example
    WORKER_TOKEN        the token `worker enroll` printed once
    COMFYUI_URL         this machine's own ComfyUI, e.g. http://127.0.0.1:8188

``COMFYUI_URL`` is not defaulted to loopback even here, where loopback is the
right answer: "unset" has to keep meaning "not configured" on every host, or
the VPS inherits a default that makes it claim to be a workstation.

Rendering reuses ``generation.ComfyUIProvider`` rather than reimplementing
the submit/poll/download protocol, so there is exactly one place that knows
how to talk to ComfyUI.

    ./content-machine worker agent
    ./content-machine worker agent --once      # one poll, for a smoke test
"""
import hashlib
import json
import logging
import os
import shutil
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import generation  # noqa: E402
import worker  # noqa: E402
from generation import GenerationRequest, GenerationError  # noqa: E402

log = logging.getLogger("worker.agent")

AGENT_VERSION = "0.1"
DEFAULT_REQUEST_TIMEOUT = 30.0
DEFAULT_UPLOAD_TIMEOUT = 120.0

# Failures that another attempt cannot fix. A missing checkpoint or a broken
# workflow template is a configuration fault on this machine: spending two
# more GPU attempts on it only delays the operator finding out.
PERMANENT_MARKERS = (
    "workflow template not found",
    "not valid JSON after substitution",
    "rejected the workflow",
)


class AgentError(Exception):
    """The agent cannot run - misconfiguration or the control plane refusing."""


def _env(name, default=None):
    value = os.environ.get(name, "")
    return value.strip() or default


class ControlPlaneClient:
    """Outbound-only HTTP client for the control plane.

    Every call carries the bearer token in a header, never in a URL, so it
    cannot end up in a redirect, a proxy log or an echoed request line.
    """

    def __init__(self, base_url=None, token=None, timeout=None):
        self.base_url = (base_url if base_url is not None
                         else _env("CONTROL_PLANE_URL") or "").rstrip("/")
        self.token = token if token is not None else _env("WORKER_TOKEN")
        self.timeout = timeout or float(_env("WORKER_REQUEST_TIMEOUT")
                                        or DEFAULT_REQUEST_TIMEOUT)
        if not self.base_url:
            raise AgentError(
                "CONTROL_PLANE_URL is not set. It has no default - point it "
                "at the VPS, e.g. https://vps.example")
        if not self.token:
            raise AgentError(
                "WORKER_TOKEN is not set. Run `./content-machine worker "
                "enroll <id>` on the VPS and put the token it prints in this "
                "machine's .env")

    def _request(self, path, data, content_type, extra_headers=None, timeout=None):
        headers = {"Authorization": f"Bearer {self.token}",
                   "Content-Type": content_type}
        headers.update(extra_headers or {})
        request = urllib.request.Request(
            f"{self.base_url}{path}", data=data, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(request, timeout=timeout or self.timeout) as r:
                return json.loads(r.read().decode("utf-8") or "{}")
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", "replace").strip()[:300]
            raise AgentError(f"control plane refused {path} (HTTP {e.code}): {detail}")
        except (urllib.error.URLError, OSError, ValueError) as e:
            raise AgentError(f"control plane unreachable at {path}: {e}")

    def post(self, path, payload=None, timeout=None):
        body = json.dumps(payload or {}).encode("utf-8")
        return self._request(path, body, "application/json", timeout=timeout)

    def upload(self, job_id, lease_id, path, digest):
        data = Path(path).read_bytes()
        return self._request(
            f"/v1/jobs/{job_id}/assets", data, "application/octet-stream",
            {"X-Lease-Id": lease_id,
             "X-Asset-Filename": Path(path).name,
             "X-Asset-Sha256": digest},
            timeout=float(_env("WORKER_UPLOAD_TIMEOUT") or DEFAULT_UPLOAD_TIMEOUT))


class Agent:
    """One poll-render-report cycle, repeated."""

    def __init__(self, client=None, provider=None):
        self.client = client or ControlPlaneClient()
        if provider is not None:
            self.provider = provider
        else:
            if not _env("COMFYUI_URL"):
                raise AgentError(
                    "COMFYUI_URL is not set. On the worker this is its own "
                    "ComfyUI, e.g. http://127.0.0.1:8188 - it is not "
                    "defaulted, because unset must keep meaning "
                    "'no ComfyUI here' on every machine")
            self.provider = generation.ComfyUIProvider()
        self.poll_seconds = float(_env("WORKER_POLL_SECONDS")
                                  or worker.DEFAULT_POLL_SECONDS)
        # Overwritten by the control plane on the first exchange: the cadence
        # is its decision, not this machine's.
        self.heartbeat_seconds = worker.heartbeat_seconds()

    # --- status ----------------------------------------------------------

    def status(self, current_job=None):
        """What the control plane learns about this machine each heartbeat.

        ComfyUI's reachability is reported rather than acted on: the control
        plane decides whether to hand out work, and a worker whose GPU host
        is down should say so instead of quietly claiming jobs it will fail.
        """
        ok, detail = self.provider.health()
        return {
            "agent_version": AGENT_VERSION,
            "comfyui": detail if ok else f"unavailable: {detail}",
            "current_job": current_job,
        }

    def heartbeat(self, current_job=None):
        return self._absorb(self.client.post(
            "/v1/heartbeat", {"status": self.status(current_job)}))

    def _absorb(self, response):
        """Take the cadences the control plane hands back."""
        self.poll_seconds = float(response.get("poll_seconds") or self.poll_seconds)
        self.heartbeat_seconds = float(response.get("heartbeat_seconds")
                                       or self.heartbeat_seconds)
        return response

    # --- one cycle -------------------------------------------------------

    def poll_once(self):
        """Claim and run at most one job. Returns the final state, or None."""
        response = self._absorb(
            self.client.post("/v1/claim", {"status": self.status()}))
        job = response.get("job")
        if not job:
            log.info("no work: %s", response.get("reason", "unknown"))
            return None
        log.info("claimed %s (attempt %s/%s)", job["job_id"],
                 job.get("attempt"), job.get("max_attempts"))
        return self.run_job(job)

    def run_job(self, job):
        job_id, lease_id = job["job_id"], job["lease_id"]
        request = _request_from(job["request"])
        lease_seconds = float(job.get("lease_seconds") or worker.DEFAULT_LEASE_SECONDS)
        self.heartbeat_seconds = float(job.get("heartbeat_seconds")
                                       or self.heartbeat_seconds)
        staging = Path(tempfile.mkdtemp(prefix=f"cm-worker-{job_id[:8]}-"))
        started = time.monotonic()
        try:
            assets, result = self._render(job_id, lease_id, request, staging,
                                          lease_seconds)
            return self._deliver(job_id, lease_id, assets, result,
                                 round(time.monotonic() - started, 2))
        except AgentError:
            # The control plane itself is unreachable or refusing. There is
            # nobody to report the failure to; the lease will expire and the
            # job returns to the queue on its own.
            raise
        except (GenerationError, OSError, ValueError) as e:
            permanent = any(m in str(e).lower() for m in PERMANENT_MARKERS)
            log.error("job %s failed%s: %s", job_id,
                      " (permanently)" if permanent else "", e)
            self.client.post(f"/v1/jobs/{job_id}/fail",
                             {"lease_id": lease_id, "detail": str(e)[:1000],
                              "permanent": permanent})
            return worker.FAILED if permanent else worker.RETRY_WAIT
        finally:
            shutil.rmtree(staging, ignore_errors=True)

    def _render(self, job_id, lease_id, request, staging, lease_seconds):
        """Run ComfyUI, reporting SUBMITTED then RUNNING and staying alive.

        Two independent clocks, because they answer different questions. The
        lease is renewed every third of its length - the control plane does
        not need to hear from a healthy render several times a second, and
        renewing with a third still to run leaves room for a retry. The
        heartbeat goes at the faster cadence the control plane asked for,
        because a render lasting longer than the ONLINE window would
        otherwise make a working machine look STALE.
        """
        now = time.monotonic()
        clock = {"state": None, "renewed_at": now, "beat_at": now}

        def progress(event, detail):
            if event == "submitted":
                self._progress(job_id, lease_id, worker.SUBMITTED,
                               f"ComfyUI prompt {detail}", provider_job_id=detail)
                clock.update(state=worker.SUBMITTED, renewed_at=time.monotonic())
                return
            if clock["state"] != worker.RUNNING:
                self._progress(job_id, lease_id, worker.RUNNING, "rendering")
                clock.update(state=worker.RUNNING, renewed_at=time.monotonic())
                return
            if time.monotonic() - clock["renewed_at"] >= lease_seconds / 3:
                self._progress(job_id, lease_id, None, "still rendering")
                clock["renewed_at"] = time.monotonic()
            if time.monotonic() - clock["beat_at"] >= self.heartbeat_seconds:
                self.heartbeat(current_job=job_id)
                clock["beat_at"] = time.monotonic()

        result = self.provider.generate(
            request, staging, timeout=generation.DEFAULT_GENERATE_TIMEOUT,
            progress=progress)
        return [Path(a) for a in result.get("assets") or []], result

    def _progress(self, job_id, lease_id, state, detail, provider_job_id=None):
        payload = {"lease_id": lease_id, "detail": detail}
        if state:
            payload["state"] = state
        if provider_job_id:
            payload["provider_job_id"] = provider_job_id
        return self.client.post(f"/v1/jobs/{job_id}/progress", payload)

    def _deliver(self, job_id, lease_id, assets, result, elapsed):
        """Upload every asset, then the manifest that accounts for them.

        The manifest is built from the bytes on disk here and re-verified
        from the staged bytes there. Two independent hashes of the same file
        is the point: a truncated upload cannot be completed as a success.
        """
        if not assets:
            raise GenerationError("ComfyUI produced no assets")
        self._progress(job_id, lease_id, worker.UPLOADING,
                       f"uploading {len(assets)} asset(s)")
        manifest = []
        for path in assets:
            digest = _sha256(path)
            self.client.upload(job_id, lease_id, path, digest)
            manifest.append({"filename": path.name, "sha256": digest,
                             "bytes": path.stat().st_size})
            log.info("uploaded %s (%d bytes)", path.name, path.stat().st_size)
        response = self.client.post(f"/v1/jobs/{job_id}/complete", {
            "lease_id": lease_id,
            "manifest": manifest,
            "provider_job_id": result.get("provider_job_id"),
            "model": result.get("model"),
            "elapsed_seconds": elapsed,
            "notes": (f"generated on remote ComfyUI worker via agent "
                      f"{AGENT_VERSION} in {elapsed:.1f}s"),
        })
        log.info("job %s %s", job_id, response.get("state"))
        return response.get("state")


def _request_from(payload):
    """Rebuild a GenerationRequest from a claim, ignoring unknown fields."""
    known = ("prompt", "negative_prompt", "width", "height", "count", "seed",
             "model", "style", "require_depicted", "params")
    return GenerationRequest(**{k: v for k, v in (payload or {}).items()
                                if k in known})


def _sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while block := f.read(1 << 20):
            h.update(block)
    return h.hexdigest()


def run(once=False, max_iterations=None, agent=None):
    """The loop. Survives a control plane that is down or restarting.

    A worker that cannot reach the VPS waits and retries rather than exiting,
    because the ordinary reason for that is a reboot at the other end - and a
    worker that dies on the first blip needs a human to restart it, which
    defeats the point of it being unattended.
    """
    try:
        agent = agent or Agent()
    except AgentError as e:
        log.error("%s", e)
        return 1
    log.info("worker agent %s polling %s every %.0fs",
             AGENT_VERSION, agent.client.base_url, agent.poll_seconds)
    iterations = 0
    while True:
        iterations += 1
        try:
            agent.poll_once()
        except AgentError as e:
            log.warning("%s", e)
        except KeyboardInterrupt:
            log.info("stopping")
            return 0
        if once or (max_iterations and iterations >= max_iterations):
            return 0
        try:
            time.sleep(agent.poll_seconds)
        except KeyboardInterrupt:
            log.info("stopping")
            return 0


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="[%(levelname)s] %(message)s")
    sys.exit(run())
