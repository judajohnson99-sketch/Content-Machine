#!/usr/bin/env python3
"""Remote GPU worker control plane.

The VPS is always on but has no GPU. The workstation has a GPU but is off
most of the time and sits behind a home router. This module is the part that
reconciles those two facts: the VPS holds the queue and the truth, and the PC
is a replaceable worker that *dials out* to ask for work.

    VPS (control plane)                     PC (worker)
    ------------------------------          -------------------------
    jobs/queue/<job_id>.json                scripts/worker_agent.py
    jobs/workers/<worker_id>.json     <---- outbound HTTPS only
    scripts/worker_api.py  (HTTP)           ComfyUI on 127.0.0.1:8188

Nothing here ever connects *to* the worker. There is no inbound path into the
home network, and ComfyUI is never exposed: only the agent on that machine
talks to it, over loopback.

Why a queue rather than a generation provider: ``generation.Router`` is
synchronous, and the workstation being off is the normal case. Routing to a
provider that cannot answer for six hours would burn retry attempts and trip
a cooldown for a machine that is merely asleep. A queued job instead simply
waits - it consumes nothing, fails nothing, and reports
``WAITING_FOR_CAPABLE_WORKER`` so the state is legible rather than silent.

How the result gets back into the pipeline: a job id *is* a
``GenerationRequest`` digest, which is already the router's idempotency key.
On success the control plane writes the ordinary completed-job record that
``generation.Router`` reads, so the next ``visuals`` run reuses the remote
result instead of regenerating it. The remote worker is therefore invisible
to every caller above generation - and it cannot make a production-grade
claim, because that record has no such field and only a human writes one.

    ./content-machine worker enroll home-gpu-01 --capabilities comfyui,sd15
    ./content-machine worker serve
    ./content-machine worker enqueue <video-id>
    ./content-machine worker workers | jobs | show <job-id> | cancel <job-id>
    ./content-machine worker agent            # on the PC
"""
import argparse
import contextlib
import fcntl
import hashlib
import hmac
import json
import logging
import os
import re
import secrets
import shutil
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import generation  # noqa: E402
from generation import GenerationRequest, utc_now  # noqa: E402

logging.basicConfig(level=logging.INFO, format="[%(levelname)s] %(message)s")
log = logging.getLogger("worker")

ROOT = Path(__file__).resolve().parent.parent

# --- Job lifecycle ---------------------------------------------------------

QUEUED = "QUEUED"
CLAIMED = "CLAIMED"
SUBMITTED = "SUBMITTED"
RUNNING = "RUNNING"
UPLOADING = "UPLOADING"
SUCCEEDED = "SUCCEEDED"
RETRY_WAIT = "RETRY_WAIT"
FAILED = "FAILED"
CANCELLED = "CANCELLED"

# The only permitted moves. Anything else is a bug in a caller or a confused
# worker, and is refused with the attempt recorded - a state machine that
# quietly accepts an impossible jump is not auditable.
TRANSITIONS = {
    QUEUED: frozenset({CLAIMED, CANCELLED}),
    CLAIMED: frozenset({SUBMITTED, RUNNING, UPLOADING, RETRY_WAIT, FAILED, CANCELLED}),
    SUBMITTED: frozenset({RUNNING, UPLOADING, RETRY_WAIT, FAILED, CANCELLED}),
    RUNNING: frozenset({UPLOADING, RETRY_WAIT, FAILED, CANCELLED}),
    UPLOADING: frozenset({SUCCEEDED, RETRY_WAIT, FAILED, CANCELLED}),
    RETRY_WAIT: frozenset({QUEUED, FAILED, CANCELLED}),
    SUCCEEDED: frozenset(),
    FAILED: frozenset(),
    CANCELLED: frozenset(),
}
TERMINAL = frozenset({SUCCEEDED, FAILED, CANCELLED})
# States in which a worker holds the job and a lease is running.
LEASED = frozenset({CLAIMED, SUBMITTED, RUNNING, UPLOADING})
# What a worker is allowed to report through /progress.
WORKER_REPORTABLE = frozenset({SUBMITTED, RUNNING, UPLOADING})

# Worker liveness. Derived from heartbeat age on every read, never stored: a
# worker that loses power cannot leave an "ONLINE" flag behind it.
ONLINE = "ONLINE"
STALE = "STALE"
OFFLINE = "OFFLINE"

# Why a QUEUED job is not moving. A view over state, not a state itself.
WAITING_FOR_CAPABLE_WORKER = "WAITING_FOR_CAPABLE_WORKER"
READY_TO_CLAIM = "READY_TO_CLAIM"
RETRY_BACKOFF = "RETRY_BACKOFF"

DEFAULT_ONLINE_SECONDS = 90.0      # ~3 missed heartbeats at a 30s cadence
DEFAULT_STALE_SECONDS = 600.0      # beyond this the worker is simply gone
DEFAULT_LEASE_SECONDS = 900.0      # a 3 GB card is slow; 15 min is not generous
DEFAULT_POLL_SECONDS = 15.0
DEFAULT_MAX_ATTEMPTS = 3
DEFAULT_MAX_ASSET_BYTES = 32 * 1024 * 1024
DEFAULT_BACKOFF_SECONDS = 60.0
DEFAULT_BACKOFF_CAP = 900.0
DEFAULT_API_BIND = "127.0.0.1"
DEFAULT_API_PORT = 8788

# What the renderer can actually read (render.py's SUPPORTED_IMAGE_EXTENSIONS).
ALLOWED_ASSET_SUFFIXES = frozenset({".png", ".jpg", ".jpeg", ".webp"})
_SAFE_FILENAME = re.compile(r"\A[A-Za-z0-9][A-Za-z0-9._-]{0,127}\Z")
_SAFE_WORKER_ID = re.compile(r"\A[a-z0-9][a-z0-9-]{1,63}\Z")
_SAFE_CAPABILITY = re.compile(r"\A[a-z0-9][a-z0-9._-]{0,31}\Z")
_SAFE_JOB_ID = re.compile(r"\A[a-f0-9]{8,64}\Z")


class WorkerError(Exception):
    """A refusal. ``status`` is the HTTP code the API layer should return."""

    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


def _env(name, default=None):
    value = os.environ.get(name, "")
    return value.strip() or default


def _env_number(name, default):
    raw = _env(name)
    if raw is None:
        return default
    try:
        return type(default)(raw)
    except ValueError:
        log.warning("%s=%r is not a number; using %s", name, raw, default)
        return default


# --- Paths -----------------------------------------------------------------
#
# Derived from generation.JOBS_DIR at call time rather than captured at
# import, so redirecting that one module attribute (what the existing tests
# already do) moves the queue, the registry and the completed-job records
# together instead of scattering them across two roots.

def jobs_dir():
    return Path(generation.JOBS_DIR)


def workers_dir():
    return jobs_dir() / "workers"


def queue_dir():
    return jobs_dir() / "queue"


def uploads_dir():
    return queue_dir() / "_uploads"


def _write_json(path, payload):
    """Atomic write: a crash mid-write must not leave an unreadable record."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2) + "\n")
    tmp.replace(path)
    return path


def _read_json(path):
    try:
        return json.loads(Path(path).read_text())
    except (OSError, ValueError):
        return None


@contextlib.contextmanager
def _queue_lock():
    """Serialise every mutation of the queue and the registry.

    Claiming is read-modify-write across files: without this, two requests
    arriving together could both hand out the same job, and the CLI could
    cancel a job in the instant between a claim's read and its write.
    Blocking rather than refusing - these critical sections are milliseconds.
    Never nest it: a second acquisition in the same process opens a second
    file description and would wait on itself forever.
    """
    queue_dir().mkdir(parents=True, exist_ok=True)
    handle = (queue_dir() / ".queue.lock").open("w")
    try:
        fcntl.flock(handle, fcntl.LOCK_EX)
        yield
    finally:
        handle.close()


# --- Worker registry -------------------------------------------------------

def _worker_path(worker_id):
    return workers_dir() / f"{worker_id}.json"


def _validate_worker_id(worker_id):
    if not _SAFE_WORKER_ID.match(worker_id or ""):
        raise WorkerError(
            f"invalid worker id {worker_id!r}: lowercase letters, digits and "
            "hyphens, 2-64 characters")
    return worker_id


def _validate_capabilities(capabilities):
    caps = sorted({c.strip().lower() for c in capabilities if c and c.strip()})
    if not caps:
        raise WorkerError("a worker must declare at least one capability")
    for cap in caps:
        if not _SAFE_CAPABILITY.match(cap):
            raise WorkerError(f"invalid capability {cap!r}")
    return caps


def _token_hash(token):
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def enroll(worker_id, capabilities, rotate=False):
    """Register a worker and mint its token.

    The token is returned exactly once and never stored - only its SHA-256
    is written, so a leaked registry file does not leak the credential and
    nothing in this repository ever contains a usable secret.

    Re-enrolling an existing worker requires ``rotate``, because silently
    minting a second token would invalidate the one already deployed on the
    PC and look like an authentication bug.
    """
    _validate_worker_id(worker_id)
    caps = _validate_capabilities(capabilities)
    with _queue_lock():
        path = _worker_path(worker_id)
        existing = _read_json(path)
        if existing and not rotate:
            raise WorkerError(
                f"worker {worker_id} is already enrolled; pass --rotate to "
                "issue a new token (this invalidates the current one)")
        token = secrets.token_urlsafe(32)
        record = {
            "worker_id": worker_id,
            "capabilities": caps,
            "token_sha256": _token_hash(token),
            "enrolled_at": (existing or {}).get("enrolled_at") or utc_now(),
            "token_issued_at": utc_now(),
            "revoked": False,
            "last_heartbeat_at": (existing or {}).get("last_heartbeat_at"),
            "last_heartbeat_epoch": (existing or {}).get("last_heartbeat_epoch"),
            "heartbeats": (existing or {}).get("heartbeats", 0),
            "status": (existing or {}).get("status") or {},
        }
        _write_json(path, record)
    return record, token


def set_revoked(worker_id, revoked=True):
    with _queue_lock():
        path = _worker_path(worker_id)
        record = _read_json(path)
        if record is None:
            raise WorkerError(f"no such worker: {worker_id}", 404)
        record["revoked"] = bool(revoked)
        record["revoked_at"] = utc_now() if revoked else None
        _write_json(path, record)
    return record


def load_workers():
    directory = workers_dir()
    if not directory.is_dir():
        return []
    records = []
    for path in sorted(directory.glob("*.json")):
        record = _read_json(path)
        if record and record.get("worker_id"):
            records.append(record)
    return records


def worker_state(record, now=None):
    """ONLINE / STALE / OFFLINE, derived from heartbeat age.

    A revoked worker is OFFLINE whatever it last said: the answer to "can
    this machine be given work" must be one value, not two that can disagree.
    """
    if record.get("revoked"):
        return OFFLINE
    last = record.get("last_heartbeat_epoch")
    if not last:
        return OFFLINE
    age = (now if now is not None else time.time()) - last
    if age <= online_seconds():
        return ONLINE
    if age <= _env_number("WORKER_STALE_SECONDS", DEFAULT_STALE_SECONDS):
        return STALE
    return OFFLINE


def worker_view(record, now=None):
    now = now if now is not None else time.time()
    last = record.get("last_heartbeat_epoch")
    return {
        "worker_id": record["worker_id"],
        "state": worker_state(record, now),
        "capabilities": record.get("capabilities", []),
        "revoked": bool(record.get("revoked")),
        "enrolled_at": record.get("enrolled_at"),
        "last_heartbeat_at": record.get("last_heartbeat_at"),
        "heartbeat_age_seconds": round(now - last, 1) if last else None,
        "heartbeats": record.get("heartbeats", 0),
        "status": record.get("status") or {},
    }


def authenticate(token):
    """Resolve a bearer token to a worker, or refuse.

    Identity comes from the token alone - a request never asserts who it is -
    so a worker cannot act as another by relabelling itself. Comparison is
    constant-time, and a revoked worker is refused even with a valid token.
    """
    if not token:
        raise WorkerError("missing bearer token", 401)
    presented = _token_hash(token)
    for record in load_workers():
        stored = record.get("token_sha256") or ""
        if stored and hmac.compare_digest(stored, presented):
            if record.get("revoked"):
                raise WorkerError(f"worker {record['worker_id']} is revoked", 403)
            return record
    raise WorkerError("unrecognised worker token", 401)


def heartbeat(worker_id, status=None):
    """Record liveness. Deliberately does NOT renew a job lease.

    Liveness and progress are different claims. A worker whose agent is alive
    but whose render has hung should keep reporting ONLINE *and* lose its
    lease, so the job returns to the queue instead of being held forever by a
    process that is merely breathing.
    """
    with _queue_lock():
        path = _worker_path(worker_id)
        record = _read_json(path)
        if record is None:
            raise WorkerError(f"no such worker: {worker_id}", 404)
        record["last_heartbeat_at"] = utc_now()
        record["last_heartbeat_epoch"] = time.time()
        record["heartbeats"] = record.get("heartbeats", 0) + 1
        if status:
            record["status"] = {k: v for k, v in status.items()
                               if k in ("comfyui", "gpu", "vram_mb",
                                        "agent_version", "current_job",
                                        "host", "detail")}
        _write_json(path, record)
    return record


# --- Queue -----------------------------------------------------------------

def _job_path(job_id):
    return queue_dir() / f"{job_id}.json"


def load_job(job_id):
    if not _SAFE_JOB_ID.match(job_id or ""):
        raise WorkerError(f"invalid job id {job_id!r}", 400)
    job = _read_json(_job_path(job_id))
    if job is None:
        raise WorkerError(f"no such job: {job_id}", 404)
    return job


def load_jobs():
    directory = queue_dir()
    if not directory.is_dir():
        return []
    jobs = []
    for path in sorted(directory.glob("*.json")):
        if path.name.startswith("_"):
            continue
        job = _read_json(path)
        if job and job.get("job_id"):
            jobs.append(job)
    return sorted(jobs, key=lambda j: j.get("created_at", ""))


def _save_job(job):
    job["updated_at"] = utc_now()
    return _write_json(_job_path(job["job_id"]), job)


def _transition(job, to_state, actor, detail=None):
    """Move a job, or refuse and say why. Every move is appended to the log."""
    from_state = job["state"]
    if to_state not in TRANSITIONS.get(from_state, frozenset()):
        raise WorkerError(
            f"illegal transition {from_state} -> {to_state} for job "
            f"{job['job_id']}", 409)
    job["state"] = to_state
    entry = {
        "at": utc_now(),
        "from": from_state,
        "to": to_state,
        "actor": actor,
        "attempt": job.get("attempt", 0),
    }
    if detail:
        entry["detail"] = str(detail)[:500]
    job.setdefault("transitions", []).append(entry)
    return job


def enqueue(request, out_dir, capabilities=("comfyui",), project_id=None,
            max_attempts=None, prompt_label=None):
    """Queue a generation request for a remote worker.

    Idempotent on the request digest, exactly like ``generation.Router``: the
    same request queued twice is one job. An already-finished generation is
    not re-queued either - the router would reuse it, so queueing it would
    spend a GPU render on an asset that already exists.
    """
    if not isinstance(request, GenerationRequest):
        raise WorkerError("enqueue needs a GenerationRequest")
    caps = _validate_capabilities(capabilities)
    out_dir = Path(out_dir).resolve()
    # The control plane writes uploaded bytes into this directory. Confining
    # it to the repository keeps a queue record from becoming an
    # arbitrary-file-write primitive if one is ever edited or corrupted.
    if not out_dir.is_relative_to(ROOT):
        raise WorkerError(f"out_dir must be inside {ROOT}: {out_dir}")

    job_id = request.digest()
    with _queue_lock():
        existing = _read_json(_job_path(job_id))
        if existing:
            return existing
        completed = generation.load_job(job_id)
        if completed and completed.get("status") == generation.COMPLETED \
                and all(Path(a).is_file() for a in completed.get("assets") or []):
            raise WorkerError(
                f"job {job_id} is already generated ({len(completed['assets'])} "
                "asset(s) on disk); the router will reuse it")
        job = {
            "job_id": job_id,
            "state": QUEUED,
            "request": request.to_dict(),
            "required_capabilities": caps,
            "out_dir": str(out_dir),
            "project_id": project_id,
            "label": prompt_label or request.prompt[:80],
            "attempt": 0,
            "max_attempts": int(max_attempts or _env_number(
                "WORKER_MAX_ATTEMPTS", DEFAULT_MAX_ATTEMPTS)),
            "created_at": utc_now(),
            "not_before_epoch": 0.0,
            "lease": None,
            "worker_id": None,
            "assets": [],
            "manifest": None,
            "error": None,
            "transitions": [{"at": utc_now(), "from": None, "to": QUEUED,
                             "actor": "operator", "attempt": 0}],
        }
        _save_job(job)
    return job


def capable_workers(job, workers=None, now=None):
    """Workers that could run this job right now, best (freshest) first."""
    required = set(job.get("required_capabilities") or [])
    now = now if now is not None else time.time()
    matches = []
    for record in (workers if workers is not None else load_workers()):
        if worker_state(record, now) != ONLINE:
            continue
        if not required.issubset(set(record.get("capabilities") or [])):
            continue
        matches.append(record)
    return matches


def wait_reason(job, workers=None, now=None):
    """Why this job is not finished. None once it is terminal or in flight."""
    now = now if now is not None else time.time()
    state = job["state"]
    if state == RETRY_WAIT:
        remaining = (job.get("not_before_epoch") or 0) - now
        return f"{RETRY_BACKOFF} ({max(0.0, remaining):.0f}s)"
    if state != QUEUED:
        return None
    if (job.get("not_before_epoch") or 0) > now:
        return f"{RETRY_BACKOFF} ({job['not_before_epoch'] - now:.0f}s)"
    if capable_workers(job, workers, now):
        return READY_TO_CLAIM
    return WAITING_FOR_CAPABLE_WORKER


def job_view(job, workers=None, now=None):
    now = now if now is not None else time.time()
    lease = job.get("lease") or {}
    return {
        "job_id": job["job_id"],
        "state": job["state"],
        "wait_reason": wait_reason(job, workers, now),
        "attempt": job.get("attempt", 0),
        "max_attempts": job.get("max_attempts"),
        "required_capabilities": job.get("required_capabilities", []),
        "project_id": job.get("project_id"),
        "label": job.get("label"),
        "worker_id": job.get("worker_id"),
        "lease_expires_in_seconds": (
            round(lease["expires_at_epoch"] - now, 1)
            if lease.get("expires_at_epoch") else None),
        "assets": job.get("assets", []),
        "error": job.get("error"),
        "created_at": job.get("created_at"),
        "updated_at": job.get("updated_at"),
    }


# --- Leases ----------------------------------------------------------------

def lease_seconds():
    """How long a claim is good for. Read live, so a restart is not needed."""
    return float(_env_number("WORKER_LEASE_SECONDS", DEFAULT_LEASE_SECONDS))


def poll_seconds():
    """Idle cadence handed to the agent: how often to ask for work."""
    return float(_env_number("WORKER_POLL_SECONDS", DEFAULT_POLL_SECONDS))


def online_seconds():
    return float(_env_number("WORKER_ONLINE_SECONDS", DEFAULT_ONLINE_SECONDS))


def heartbeat_seconds():
    """Liveness cadence handed to the agent, derived from the ONLINE window.

    A worker mid-render does not call /claim, so without this it would go
    STALE while it was in fact working - and the next queued job would report
    WAITING_FOR_CAPABLE_WORKER about a machine that is alive. A third of the
    window leaves room for two lost beats.
    """
    return online_seconds() / 3


def max_asset_bytes():
    return int(_env_number("WORKER_MAX_ASSET_BYTES", DEFAULT_MAX_ASSET_BYTES))


def api_bind():
    return _env("WORKER_API_BIND", DEFAULT_API_BIND)


def api_port():
    return int(_env_number("WORKER_API_PORT", DEFAULT_API_PORT))


def _grant_lease(job, worker_id):
    expires = time.time() + lease_seconds()
    job["lease"] = {
        "worker_id": worker_id,
        "lease_id": secrets.token_hex(16),
        "granted_at": utc_now(),
        "expires_at_epoch": expires,
        "expires_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(expires)),
    }
    job["worker_id"] = worker_id
    return job["lease"]


def _check_lease(job, worker_id, lease_id, now=None):
    """Refuse anything that is not the current lease holder.

    A new ``lease_id`` per claim is what makes lease expiry safe: once a job
    has been reaped and handed to someone else, the previous holder's late
    upload or status report is rejected instead of overwriting work in
    progress. An expired-but-unreaped lease is refused for the same reason.
    """
    lease = job.get("lease") or {}
    if not lease:
        raise WorkerError(f"job {job['job_id']} is not leased", 409)
    if lease.get("worker_id") != worker_id:
        raise WorkerError(
            f"job {job['job_id']} is leased to {lease.get('worker_id')}, "
            f"not {worker_id}", 409)
    if not lease_id or not hmac.compare_digest(str(lease.get("lease_id")),
                                               str(lease_id)):
        raise WorkerError(f"stale or wrong lease for job {job['job_id']}", 409)
    now = now if now is not None else time.time()
    if lease.get("expires_at_epoch", 0) < now:
        raise WorkerError(
            f"lease on job {job['job_id']} expired at {lease.get('expires_at')}",
            409)
    return lease


def _renew_lease(job):
    lease = job.get("lease") or {}
    lease["expires_at_epoch"] = time.time() + lease_seconds()
    lease["expires_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ",
                                        time.gmtime(lease["expires_at_epoch"]))
    lease["renewed_at"] = utc_now()
    return lease


def _backoff_seconds(attempt):
    base = float(_env_number("WORKER_BACKOFF_SECONDS", DEFAULT_BACKOFF_SECONDS))
    cap = float(_env_number("WORKER_BACKOFF_CAP", DEFAULT_BACKOFF_CAP))
    return min(base * (2 ** max(0, attempt - 1)), cap)


def _defer_or_fail(job, actor, detail, permanent=False):
    """Send a failed attempt to RETRY_WAIT, or to FAILED when it is spent.

    ``permanent`` exists so a worker can say "retrying cannot help" - a
    missing checkpoint or an invalid workflow template is a configuration
    fault, and burning two more GPU attempts on it only delays the operator
    finding out.
    """
    attempt = job.get("attempt", 0)
    job["lease"] = None
    job["error"] = str(detail)[:1000]
    if permanent or attempt >= job.get("max_attempts", DEFAULT_MAX_ATTEMPTS):
        reason = "permanent failure" if permanent else (
            f"attempt {attempt}/{job.get('max_attempts')} exhausted")
        _transition(job, FAILED, actor, f"{reason}: {detail}")
    else:
        backoff = _backoff_seconds(attempt)
        job["not_before_epoch"] = time.time() + backoff
        _transition(job, RETRY_WAIT, actor,
                    f"attempt {attempt}/{job.get('max_attempts')} failed, "
                    f"retry in {backoff:.0f}s: {detail}")
    _save_job(job)
    return job


def reap(now=None):
    """Return abandoned work to the queue and release finished backoffs.

    Called at the top of every API request and available as a CLI command, so
    recovery needs no background timer: the next thing that happens performs
    it. Two duties, both about time passing rather than anything a worker did.
    """
    now = now if now is not None else time.time()
    actions = []
    with _queue_lock():
        for job in load_jobs():
            state = job["state"]
            if state in LEASED:
                lease = job.get("lease") or {}
                if lease.get("expires_at_epoch", 0) >= now:
                    continue
                # The attempt is already counted (claiming counts it), so an
                # abandoned job cannot loop forever: max_attempts still binds.
                _defer_or_fail(
                    job, "control-plane",
                    f"lease held by {lease.get('worker_id')} expired at "
                    f"{lease.get('expires_at')} in state {state}")
                actions.append((job["job_id"], state, job["state"]))
            elif state == RETRY_WAIT and (job.get("not_before_epoch") or 0) <= now:
                # The gate has been honoured, so it is spent. Leaving it set
                # would make a ready job look like it were still backing off.
                job["not_before_epoch"] = 0.0
                _transition(job, QUEUED, "control-plane", "retry backoff elapsed")
                _save_job(job)
                actions.append((job["job_id"], RETRY_WAIT, QUEUED))
    return actions


# --- Worker-facing operations ---------------------------------------------

def claim(worker_id, now=None):
    """Hand the oldest ready job this worker can run to it, under a lease.

    Returns ``(job, reason)``; ``job`` is None when there is nothing for this
    worker, with ``reason`` saying which. Claiming is the moment an attempt
    is spent - so a job nobody can run spends nothing, however long it waits.
    """
    now = now if now is not None else time.time()
    with _queue_lock():
        record = _read_json(_worker_path(worker_id))
        if record is None:
            raise WorkerError(f"no such worker: {worker_id}", 404)
        if record.get("revoked"):
            raise WorkerError(f"worker {worker_id} is revoked", 403)
        capabilities = set(record.get("capabilities") or [])

        eligible = []
        blocked = 0
        for job in load_jobs():
            if job["state"] != QUEUED:
                continue
            if (job.get("not_before_epoch") or 0) > now:
                continue
            if not set(job.get("required_capabilities") or []).issubset(capabilities):
                blocked += 1
                continue
            eligible.append(job)
        if not eligible:
            return None, ("NO_CAPABLE_JOB" if blocked else "NO_JOB_READY")

        job = min(eligible, key=lambda j: j.get("created_at", ""))
        job["attempt"] = job.get("attempt", 0) + 1
        lease = _grant_lease(job, worker_id)
        _transition(job, CLAIMED, f"worker:{worker_id}",
                    f"lease {lease['lease_id'][:8]} until {lease['expires_at']}")
        _save_job(job)
    return job, "CLAIMED"


def report_progress(job_id, worker_id, lease_id, state=None, detail=None,
                    provider_job_id=None):
    """Advance a leased job and renew its lease.

    Renewal happens here rather than on heartbeat because this is the only
    signal that the *job* is alive. Re-reporting the state the job is already
    in is a renewal and records no transition, so a long render polling every
    few seconds keeps its lease without producing a useless audit trail.
    """
    with _queue_lock():
        job = load_job(job_id)
        _check_lease(job, worker_id, lease_id)
        if state is not None:
            if state not in WORKER_REPORTABLE:
                raise WorkerError(
                    f"a worker may report {sorted(WORKER_REPORTABLE)}, not {state}",
                    400)
            if state != job["state"]:
                _transition(job, state, f"worker:{worker_id}", detail)
        if provider_job_id:
            job["provider_job_id"] = str(provider_job_id)[:200]
        _renew_lease(job)
        _save_job(job)
    return job


def report_failure(job_id, worker_id, lease_id, detail, permanent=False):
    with _queue_lock():
        job = load_job(job_id)
        _check_lease(job, worker_id, lease_id)
        return _defer_or_fail(job, f"worker:{worker_id}", detail, permanent)


def cancel(job_id, actor="operator", reason=None):
    """Withdraw a job. The one transition a human initiates."""
    with _queue_lock():
        job = load_job(job_id)
        if job["state"] in TERMINAL:
            raise WorkerError(
                f"job {job_id} is already {job['state']}", 409)
        job["lease"] = None
        _transition(job, CANCELLED, actor, reason or "cancelled by operator")
        _save_job(job)
        shutil.rmtree(uploads_dir() / job_id, ignore_errors=True)
    return job


# --- Asset upload and manifests -------------------------------------------

def _safe_asset_name(filename):
    """Validate a filename rather than sanitising one.

    Taking the basename of ``../../evil.png`` would also be safe, but a
    worker that sends a path is a worker doing something wrong, and silently
    rewriting the name it reported would leave a manifest describing a file
    by a name nobody sent. A refusal is both safer and legible.
    """
    name = str(filename or "")
    if not _SAFE_FILENAME.match(name):
        raise WorkerError(f"unsafe asset filename {filename!r}", 400)
    if Path(name).suffix.lower() not in ALLOWED_ASSET_SUFFIXES:
        raise WorkerError(
            f"asset {name} is not a readable image type "
            f"({', '.join(sorted(ALLOWED_ASSET_SUFFIXES))})", 415)
    return name


def stage_asset(job_id, worker_id, lease_id, filename, data, sha256_claimed=None):
    """Accept one uploaded asset into a per-job staging area.

    The claimed digest is verified against the bytes actually received before
    anything is written, so a truncated upload is a refusal rather than a
    corrupt asset that only fails later in QC.
    """
    limit = max_asset_bytes()
    if not data:
        raise WorkerError("empty asset upload", 400)
    if len(data) > limit:
        raise WorkerError(
            f"asset is {len(data)} bytes, over the {limit}-byte limit", 413)
    digest = hashlib.sha256(data).hexdigest()
    if sha256_claimed and not hmac.compare_digest(digest, str(sha256_claimed).lower()):
        raise WorkerError(
            f"sha256 mismatch: received bytes hash to {digest}, "
            f"upload claimed {sha256_claimed}", 422)
    with _queue_lock():
        job = load_job(job_id)
        _check_lease(job, worker_id, lease_id)
        if job["state"] not in (SUBMITTED, RUNNING, UPLOADING, CLAIMED):
            raise WorkerError(
                f"job {job_id} is {job['state']}; not accepting assets", 409)
        name = _safe_asset_name(filename)
        staging = uploads_dir() / job_id
        staging.mkdir(parents=True, exist_ok=True)
        (staging / name).write_bytes(data)
        _renew_lease(job)
        _save_job(job)
    return {"filename": name, "sha256": digest, "bytes": len(data)}


def _verify_manifest(job_id, manifest):
    """Re-derive every hash from disk and refuse any discrepancy.

    The worker's report is a claim; the staged bytes are the artefact. An
    entry the worker forgot to upload, a file it uploaded but left out of the
    manifest, or a hash that does not match what is on disk all mean the
    manifest does not describe reality - which is the whole point of having
    one, so none of them may pass.
    """
    staging = uploads_dir() / job_id
    if not isinstance(manifest, list) or not manifest:
        raise WorkerError("manifest must be a non-empty list", 400)
    verified, seen = [], set()
    for entry in manifest:
        if not isinstance(entry, dict):
            raise WorkerError("manifest entries must be objects", 400)
        name = _safe_asset_name(entry.get("filename"))
        if name in seen:
            raise WorkerError(f"manifest lists {name} twice", 400)
        seen.add(name)
        path = staging / name
        if not path.is_file():
            raise WorkerError(
                f"manifest lists {name} but it was never uploaded", 422)
        data = path.read_bytes()
        digest = hashlib.sha256(data).hexdigest()
        claimed = str(entry.get("sha256") or "").lower()
        if claimed and not hmac.compare_digest(digest, claimed):
            raise WorkerError(
                f"manifest sha256 for {name} does not match the staged file", 422)
        verified.append({"filename": name, "sha256": digest, "bytes": len(data)})
    staged = {p.name for p in staging.iterdir() if p.is_file()}
    unaccounted = sorted(staged - seen)
    if unaccounted:
        raise WorkerError(
            f"uploaded but absent from the manifest: {', '.join(unaccounted)}",
            422)
    return verified


def complete(job_id, worker_id, lease_id, manifest, provider_job_id=None,
             model=None, notes=None, elapsed_seconds=None):
    """Verify the manifest, publish the assets, and record the generation.

    The last step is the integration seam: a completed-job record in the
    ordinary generation job store means the next ``visuals`` run reuses this
    result. Nothing above generation learns that a remote worker exists, and
    nothing here claims the assets are production-grade - that record has no
    such field, and only a human writes one.
    """
    with _queue_lock():
        job = load_job(job_id)
        _check_lease(job, worker_id, lease_id)
        if job["state"] != UPLOADING:
            _transition(job, UPLOADING, f"worker:{worker_id}",
                        "completion reported")
        verified = _verify_manifest(job_id, manifest)
        out_dir = Path(job["out_dir"])
        if not out_dir.resolve().is_relative_to(ROOT):
            raise WorkerError(f"out_dir escaped the repository: {out_dir}", 500)
        out_dir.mkdir(parents=True, exist_ok=True)

        staging = uploads_dir() / job_id
        assets = []
        # Same naming as a local ComfyUI render, so a remote result is
        # indistinguishable downstream from one produced on a local GPU.
        for index, entry in enumerate(verified, start=1):
            suffix = Path(entry["filename"]).suffix.lower()
            target = out_dir / f"gen_{job_id[:8]}_{index:02d}{suffix}"
            (staging / entry["filename"]).replace(target)
            entry["path"] = str(target)
            assets.append(target)

        job["assets"] = [str(a) for a in assets]
        job["manifest"] = verified
        if provider_job_id:
            job["provider_job_id"] = str(provider_job_id)[:200]
        job["model"] = str(model)[:200] if model else None
        job["completed_at"] = utc_now()
        job["lease"] = None
        _transition(job, SUCCEEDED, f"worker:{worker_id}",
                    f"{len(assets)} asset(s) verified and published")
        _save_job(job)
        shutil.rmtree(staging, ignore_errors=True)
        _write_generation_job(job, worker_id, notes, elapsed_seconds)
    return job


def _write_generation_job(job, worker_id, notes, elapsed_seconds):
    """Record the remote render as a completed generation job.

    Shaped exactly like a local one because it is one: the request digest is
    the same idempotency key, so ``Router.generate`` finds it and reuses it.
    ``produces_depicted`` is true - ComfyUI does produce depicted imagery -
    and that is the strongest claim any machine may make here. Whether the
    result is good enough to publish is the human judgement the publication
    gate waits for, and the gate re-inspects the artefacts themselves, so a
    worker that uploads a flat placeholder is still caught downstream.
    """
    record = generation.load_job(job["job_id"]) or {
        "job_id": job["job_id"],
        "request": job["request"],
        "created_at": job.get("created_at"),
        "attempts": [],
    }
    record.update({
        "status": generation.COMPLETED,
        "provider": "comfyui",
        "worker_id": worker_id,
        "provider_job_id": job.get("provider_job_id"),
        "model": job.get("model"),
        "cost_usd": 0.0,
        "produces_depicted": True,
        "notes": (notes or "")[:500] or (
            f"generated on remote ComfyUI worker {worker_id}"),
        "assets": job["assets"],
        "manifest": job["manifest"],
        "out_dir": job["out_dir"],
        "worker_job": {"attempt": job.get("attempt"),
                       "transitions": job.get("transitions", [])},
        "elapsed_seconds": elapsed_seconds,
        "completed_at": job.get("completed_at"),
    })
    record.setdefault("attempts", []).append({
        "provider": "comfyui", "at": utc_now(), "outcome": "completed",
        "attempt": job.get("attempt"), "worker_id": worker_id,
        "assets": len(job["assets"])})
    generation.save_job(record)
    return record


# --- Enqueue from a project ------------------------------------------------

def enqueue_project(video_id, prompt=None, count=None, capabilities=None,
                    max_attempts=None):
    """Queue a project's visual plan for the remote GPU.

    The request is built from the same fields ``project.py`` uses, and the
    digest therefore matches what a local run would compute - which is what
    makes the result reusable rather than a parallel asset set.
    """
    import project  # local import: project.py imports generation, not worker

    pdir = project.project_dir(video_id)
    spec_path = pdir / "video_spec.json"
    metadata_path = pdir / "metadata.json"
    if not spec_path.is_file() or not metadata_path.is_file():
        raise WorkerError(f"not a project (missing spec/metadata): {pdir}", 404)
    spec = json.loads(spec_path.read_text())
    metadata = json.loads(metadata_path.read_text())
    plan = metadata.get("visual_plan") or {}
    text = prompt or plan.get("prompt")
    if not text:
        raise WorkerError(
            "no prompt: pass --prompt or set metadata.visual_plan.prompt")

    request = GenerationRequest(
        prompt=text,
        negative_prompt=plan.get("negative_prompt"),
        width=spec.get("width", 1920),
        height=spec.get("height", 1080),
        count=count or plan.get("count", 1),
        seed=plan.get("seed", 20260827),
        model=plan.get("model"),
        style=plan.get("style", "deep-night"),
        # A remote ComfyUI render is a depicted-imagery route by definition.
        require_depicted=True,
    )
    return enqueue(request, pdir / "images",
                   capabilities=capabilities or ("comfyui",),
                   project_id=video_id, max_attempts=max_attempts,
                   prompt_label=text[:80])


# --- CLI -------------------------------------------------------------------

def cmd_enroll(args):
    record, token = enroll(args.worker_id,
                           [c for c in (args.capabilities or "").split(",")],
                           rotate=args.rotate)
    print(f"enrolled {record['worker_id']} "
          f"[{', '.join(record['capabilities'])}]")
    print()
    print("Token (shown once - only its SHA-256 is stored):")
    print(f"  {token}")
    print()
    print("On the worker machine, put it in that machine's .env (gitignored):")
    print(f"  WORKER_TOKEN={token}")
    print("  CONTROL_PLANE_URL=https://<this-host>")
    print("  COMFYUI_URL=http://127.0.0.1:8188")
    print()
    print("Then run:  ./content-machine worker agent")
    return 0


def cmd_revoke(args):
    set_revoked(args.worker_id, True)
    print(f"revoked {args.worker_id}: it can no longer claim or report")
    return 0


def cmd_workers(args):
    workers = load_workers()
    if not workers:
        print("no workers enrolled")
        print("enroll one: ./content-machine worker enroll home-gpu-01 "
              "--capabilities comfyui,sd15")
        return 0
    for record in workers:
        view = worker_view(record)
        age = ("never" if view["heartbeat_age_seconds"] is None
               else f"{view['heartbeat_age_seconds']:.0f}s ago")
        flags = " [REVOKED]" if view["revoked"] else ""
        print(f"{view['state']:<8} {view['worker_id']:<16} "
              f"{','.join(view['capabilities']):<22} heartbeat {age}{flags}")
        comfy = (view["status"] or {}).get("comfyui")
        if comfy:
            print(f"         comfyui: {comfy}")
    return 0


def cmd_enqueue(args):
    if args.video_id:
        job = enqueue_project(args.video_id, prompt=args.prompt,
                              count=args.count,
                              capabilities=_caps(args.capabilities),
                              max_attempts=args.max_attempts)
    else:
        if not args.prompt or not args.out:
            raise WorkerError("pass a video-id, or both --prompt and --out")
        request = GenerationRequest(
            prompt=args.prompt, negative_prompt=args.negative,
            width=args.width, height=args.height, count=args.count or 1,
            seed=args.seed, model=args.model, require_depicted=True)
        job = enqueue(request, args.out, capabilities=_caps(args.capabilities),
                      max_attempts=args.max_attempts)
    view = job_view(job)
    print(f"job {view['job_id']}  {view['state']}  {view['wait_reason']}")
    if view["wait_reason"] == WAITING_FOR_CAPABLE_WORKER:
        print("no capable worker is online. The job waits - it will not fail "
              "and it consumes no attempts.")
    return 0


def cmd_jobs(args):
    jobs = load_jobs()
    if args.state:
        wanted = {s.strip().upper() for s in args.state.split(",")}
        jobs = [j for j in jobs if j["state"] in wanted]
    if not jobs:
        print("no jobs in the worker queue")
        return 0
    workers, now = load_workers(), time.time()
    for job in jobs:
        view = job_view(job, workers, now)
        print(f"{view['job_id']:<18} {view['state']:<11} "
              f"attempt {view['attempt']}/{view['max_attempts']}  "
              f"{view['wait_reason'] or view['worker_id'] or ''}")
        if view["label"]:
            print(f"                   {view['label']}")
    return 0


def cmd_show(args):
    job = load_job(args.job_id)
    view = job_view(job)
    print(json.dumps(view, indent=2))
    print("\ntransitions:")
    for entry in job.get("transitions", []):
        detail = f"  {entry.get('detail')}" if entry.get("detail") else ""
        print(f"  {entry['at']}  {str(entry['from']):<11} -> "
              f"{entry['to']:<11} {entry['actor']}{detail}")
    if job.get("manifest"):
        print("\nmanifest:")
        for entry in job["manifest"]:
            print(f"  {entry['filename']}  {entry['bytes']} bytes  "
                  f"sha256={entry['sha256'][:16]}...")
    return 0


def cmd_cancel(args):
    job = cancel(args.job_id, reason=args.reason)
    print(f"job {job['job_id']} is now {job['state']}")
    return 0


def cmd_reap(args):
    actions = reap()
    if not actions:
        print("nothing to reap")
        return 0
    for job_id, before, after in actions:
        print(f"{job_id}  {before} -> {after}")
    return 0


def cmd_serve(args):
    import worker_api
    return worker_api.serve(host=args.bind, port=args.port)


def cmd_agent(args):
    import worker_agent
    return worker_agent.run(once=args.once, max_iterations=args.max_iterations)


def _caps(raw):
    return tuple(c for c in (raw or "comfyui").split(",") if c.strip())


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Remote GPU worker control plane.")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("enroll", help="register a worker and mint its token")
    p.add_argument("worker_id")
    p.add_argument("--capabilities", default="comfyui",
                   help="comma-separated, e.g. comfyui,sd15")
    p.add_argument("--rotate", action="store_true",
                   help="re-issue a token, invalidating the current one")
    p.set_defaults(func=cmd_enroll)

    p = sub.add_parser("revoke", help="disable a worker's token")
    p.add_argument("worker_id")
    p.set_defaults(func=cmd_revoke)

    p = sub.add_parser("workers", help="show enrolled workers and liveness")
    p.set_defaults(func=cmd_workers)

    p = sub.add_parser("enqueue", help="queue a render for a remote GPU")
    p.add_argument("video_id", nargs="?", help="queue this project's visual plan")
    p.add_argument("--prompt", default=None)
    p.add_argument("--negative", default=None)
    p.add_argument("--out", default=None, help="output directory (without a video-id)")
    p.add_argument("--count", type=int, default=None)
    p.add_argument("--width", type=int, default=1920)
    p.add_argument("--height", type=int, default=1080)
    p.add_argument("--seed", type=int, default=20260827)
    p.add_argument("--model", default=None)
    p.add_argument("--capabilities", default="comfyui")
    p.add_argument("--max-attempts", type=int, default=None)
    p.set_defaults(func=cmd_enqueue)

    p = sub.add_parser("jobs", help="list queued work")
    p.add_argument("--state", default=None, help="filter, comma-separated")
    p.set_defaults(func=cmd_jobs)

    p = sub.add_parser("show", help="one job with its full audit trail")
    p.add_argument("job_id")
    p.set_defaults(func=cmd_show)

    p = sub.add_parser("cancel", help="withdraw a job")
    p.add_argument("job_id")
    p.add_argument("--reason", default=None)
    p.set_defaults(func=cmd_cancel)

    p = sub.add_parser("reap", help="expire leases and release retry backoffs")
    p.set_defaults(func=cmd_reap)

    p = sub.add_parser("serve", help="run the control-plane HTTP API")
    p.add_argument("--bind", default=None,
                   help=f"default {DEFAULT_API_BIND} (WORKER_API_BIND)")
    p.add_argument("--port", type=int, default=None,
                   help=f"default {DEFAULT_API_PORT} (WORKER_API_PORT)")
    p.set_defaults(func=cmd_serve)

    p = sub.add_parser("agent", help="run the worker agent (on the GPU machine)")
    p.add_argument("--once", action="store_true", help="one poll, then exit")
    p.add_argument("--max-iterations", type=int, default=None)
    p.set_defaults(func=cmd_agent)

    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except WorkerError as e:
        log.error("%s", e)
        return 1


if __name__ == "__main__":
    sys.exit(main())
