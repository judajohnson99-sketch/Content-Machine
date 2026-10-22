#!/usr/bin/env python3
"""Who may use the GPU right now: ComfyUI renders, or a vision model, never both.

The worker's GPU is small (the verified card is a 3 GB GTX 1060) and a
vision model is a second resident model on it. If Ollama loads one while
ComfyUI is mid-render, one of them runs out of memory, and the render is the
one that matters. This module is the single arbiter:

- ``lease`` is an exclusive, cross-process hold on the GPU (an ``flock`` on a
  lock file). The worker agent holds it for the whole of a render and for the
  vision pass that follows it, so the two cannot overlap by construction.
- ``comfyui_busy`` asks ComfyUI itself whether it has work running or queued,
  which covers an operator using it by hand outside the agent.
- ``free_vram_mb`` reports what is actually free, so a model is not loaded
  into memory that is not there.

Renders win. Vision is opportunistic: when the GPU is not clearly free it
defers ("not evaluated: GPU busy") rather than waiting its turn in front of a
render. Nothing here talks to a paid service, and nothing has a default
endpoint: an unset ``COMFYUI_URL`` means "no ComfyUI on this host".

Stdlib only; POSIX (the worker already requires ``fcntl``).
"""
import contextlib
import fcntl
import json
import os
import shutil
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# A render that is mid-flight holds the lease for as long as it takes; a
# caller that must not wait for it passes wait_seconds=0.
DEFAULT_LEASE_WAIT_SECONDS = 180.0
POLL_SECONDS = 0.25
DEFAULT_PROBE_TIMEOUT = 3.0

BUSY_LEASE = "gpu_leased"
BUSY_COMFYUI = "comfyui_busy"
BUSY_VRAM = "insufficient_vram"


class GpuBusy(Exception):
    """The GPU is in use (or too full) for this caller right now."""

    def __init__(self, reason, detail):
        super().__init__(detail)
        self.reason = reason
        self.detail = detail


def _env(name, default=None):
    value = os.environ.get(name)
    return value if value not in (None, "") else default


def lock_path():
    explicit = _env("GPU_LOCK_PATH")
    if explicit:
        return Path(explicit)
    jobs = Path(_env("CONTENT_MACHINE_JOBS_DIR") or (ROOT / "jobs"))
    return jobs / "_gpu.lock"


def lease_wait_seconds():
    try:
        return float(_env("GPU_LEASE_WAIT_SECONDS", DEFAULT_LEASE_WAIT_SECONDS))
    except (TypeError, ValueError):
        return DEFAULT_LEASE_WAIT_SECONDS


def holder(path=None):
    """Who last took the lease, or None. Informational: it is read without
    the lock, so it describes the most recent holder, not a guarantee."""
    try:
        return json.loads(Path(path or lock_path()).read_text() or "null")
    except (OSError, ValueError):
        return None


@contextlib.contextmanager
def lease(who, wait_seconds=0.0, path=None):
    """Hold the GPU exclusively, or raise ``GpuBusy``.

    ``wait_seconds=0`` is a try-lock. A positive value polls until the holder
    lets go - the worker agent waits (a vision pass is short), vision itself
    never does.
    """
    target = Path(path or lock_path())
    target.parent.mkdir(parents=True, exist_ok=True)
    handle = target.open("a+")
    deadline = time.monotonic() + max(0.0, float(wait_seconds))
    try:
        while True:
            try:
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError:
                if time.monotonic() >= deadline:
                    current = holder(target) or {}
                    raise GpuBusy(
                        BUSY_LEASE,
                        f"the GPU is held by {current.get('who', 'another process')}"
                        + (f" (since {current['since']})" if current.get("since") else ""))
                time.sleep(POLL_SECONDS)
        handle.seek(0)
        handle.truncate()
        handle.write(json.dumps({
            "who": who, "pid": os.getpid(),
            "since": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}))
        handle.flush()
        try:
            yield
        finally:
            handle.seek(0)
            handle.truncate()
            handle.flush()
            fcntl.flock(handle, fcntl.LOCK_UN)
    finally:
        handle.close()


def _get_json(url, timeout):
    with urllib.request.urlopen(url, timeout=timeout) as response:
        if response.status != 200:
            raise OSError(f"HTTP {response.status}")
        return json.loads(response.read().decode("utf-8"))


def comfyui_busy(url=None, timeout=DEFAULT_PROBE_TIMEOUT):
    """``(busy, detail)``: does ComfyUI have a prompt running or waiting?

    Unreachable or unset counts as not busy - there is nothing there to
    collide with, and an offline ComfyUI must not stop vision from running.
    """
    url = (url if url is not None else _env("COMFYUI_URL") or "").rstrip("/")
    if not url:
        return False, "no ComfyUI configured on this host"
    try:
        queue = _get_json(f"{url}/queue", timeout)
    except (urllib.error.URLError, OSError, ValueError):
        return False, "ComfyUI is not reachable"
    running = len(queue.get("queue_running") or [])
    pending = len(queue.get("queue_pending") or [])
    if running or pending:
        return True, f"ComfyUI has {running} running and {pending} queued prompt(s)"
    return False, "ComfyUI is idle"


def free_vram_mb(comfyui_url=None, timeout=DEFAULT_PROBE_TIMEOUT):
    """Free VRAM in MB from ComfyUI, else nvidia-smi, else ``None``."""
    url = (comfyui_url if comfyui_url is not None else _env("COMFYUI_URL") or "").rstrip("/")
    if url:
        try:
            device = ((_get_json(f"{url}/system_stats", timeout).get("devices") or [{}])[0]) or {}
            if device.get("vram_free") is not None:
                return int(device["vram_free"] / (1024 * 1024))
        except (urllib.error.URLError, OSError, ValueError, TypeError, IndexError):
            pass
    smi = shutil.which("nvidia-smi")
    if smi:
        try:
            out = subprocess.run(
                [smi, "--query-gpu=memory.free", "--format=csv,noheader,nounits"],
                capture_output=True, text=True, timeout=timeout)
            if out.returncode == 0 and out.stdout.strip():
                return int(float(out.stdout.strip().splitlines()[0]))
        except (OSError, ValueError, subprocess.TimeoutExpired):
            pass
    return None


def free_comfyui(url=None, timeout=DEFAULT_PROBE_TIMEOUT):
    """Ask ComfyUI to unload its models and release cached memory.

    ComfyUI keeps the last checkpoint resident after a render, which on a
    3 GB card leaves no room for a vision model. Its ``/free`` endpoint asks
    it to let go (it does so as soon as its queue is idle, and reloads on the
    next render). Best effort: ``True`` if ComfyUI accepted the request,
    ``False`` if there is no ComfyUI here, it is unreachable, or it refused.
    """
    url = (url if url is not None else _env("COMFYUI_URL") or "").rstrip("/")
    if not url:
        return False
    body = json.dumps({"unload_models": True, "free_memory": True}).encode("utf-8")
    req = urllib.request.Request(f"{url}/free", data=body,
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            return 200 <= response.status < 300
    except (urllib.error.URLError, OSError, ValueError):
        return False


def check_clear(min_free_vram_mb=0, comfyui_url=None):
    """Raise ``GpuBusy`` unless the GPU is clearly free for a model load.

    ComfyUI work in flight is a refusal. Known-too-little free VRAM is a
    refusal. VRAM that cannot be measured is not: the caller is expected to
    hold the lease and to unload its model afterwards, and refusing on
    "unknown" would mean never running on a machine without nvidia-smi.
    """
    busy, detail = comfyui_busy(comfyui_url)
    if busy:
        raise GpuBusy(BUSY_COMFYUI, detail)
    if min_free_vram_mb:
        free = free_vram_mb(comfyui_url)
        if free is not None and free < min_free_vram_mb:
            raise GpuBusy(
                BUSY_VRAM,
                f"{free} MB of VRAM free, {min_free_vram_mb} MB needed for the vision model")
    return True
