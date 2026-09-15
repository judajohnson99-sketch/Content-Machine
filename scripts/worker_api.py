#!/usr/bin/env python3
"""HTTP surface of the worker control plane.

Transport only: every rule lives in ``worker.py``. This layer authenticates a
bearer token, parses a request, calls one domain function and maps a
``WorkerError`` to its status code. It holds no state and makes no decisions,
which is what keeps the state machine testable without a socket.

Every endpoint is called *by the worker*, outbound. There is no route that
reaches into the worker's network, and ComfyUI is never proxied: the only
thing that talks to ComfyUI is the agent on that same machine, over loopback.

    GET  /v1/ping                      liveness, unauthenticated
    POST /v1/heartbeat                 worker is alive (does not renew a lease)
    POST /v1/claim                     ask for work
    POST /v1/jobs/<id>/progress        advance state, renew the lease
    POST /v1/jobs/<id>/assets          upload one asset (raw body)
    POST /v1/jobs/<id>/complete        submit the manifest, publish the assets
    POST /v1/jobs/<id>/fail            report a failed attempt

Binding defaults to 127.0.0.1. A bearer token on plain HTTP is a token on the
wire, so the intended deployment is behind a TLS-terminating reverse proxy or
reached through a tunnel - not a public listener. Serving 0.0.0.0 has to be
asked for explicitly, because it cannot be the safe default.

    ./content-machine worker serve
"""
import json
import logging
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import worker  # noqa: E402
from worker import WorkerError  # noqa: E402

log = logging.getLogger("worker.api")

# Generous for a manifest, small enough that a runaway body cannot exhaust
# the VPS's 3.8 GB. Asset uploads get their own, larger limit.
MAX_JSON_BYTES = 256 * 1024


class ControlPlaneHandler(BaseHTTPRequestHandler):
    """One handler, one request. Authentication first, always."""

    server_version = "content-machine-worker-api"
    sys_version = ""
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        # Never the headers: the bearer token travels in one of them.
        log.info("%s %s", self.address_string(), fmt % args)

    # --- plumbing ---------------------------------------------------------

    def _send(self, code, payload):
        body = json.dumps(payload, indent=2).encode("utf-8")
        if code >= 400:
            # A refusal can happen before the request body was read - an
            # oversized upload is rejected on its Content-Length alone. Those
            # undrained bytes would be parsed as the next request on a
            # keep-alive connection, so this one ends here.
            self.close_connection = True
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        if code >= 400:
            self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(body)

    def _body(self, limit):
        try:
            length = int(self.headers.get("Content-Length", 0))
        except ValueError:
            raise WorkerError("Content-Length is not a number", 411)
        if length < 0:
            raise WorkerError("negative Content-Length", 400)
        if length > limit:
            raise WorkerError(f"body over the {limit}-byte limit", 413)
        return self.rfile.read(length) if length else b""

    def _json_body(self):
        raw = self._body(MAX_JSON_BYTES)
        if not raw:
            return {}
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (ValueError, UnicodeDecodeError) as e:
            raise WorkerError(f"body is not valid JSON: {e}", 400)
        if not isinstance(payload, dict):
            raise WorkerError("body must be a JSON object", 400)
        return payload

    def _authenticate(self):
        header = self.headers.get("Authorization", "")
        scheme, _, token = header.partition(" ")
        if scheme.lower() != "bearer":
            raise WorkerError("expected an Authorization: Bearer header", 401)
        return worker.authenticate(token.strip())

    def _lease_id(self, payload):
        lease_id = (payload.get("lease_id")
                    or self.headers.get("X-Lease-Id") or "").strip()
        if not lease_id:
            raise WorkerError("lease_id is required", 400)
        return lease_id

    # --- routing ----------------------------------------------------------

    def do_GET(self):
        path = self.path.split("?", 1)[0].rstrip("/")
        if path == "/v1/ping":
            # Deliberately says nothing beyond "a control plane is here":
            # this is the one unauthenticated route.
            self._send(200, {"ok": True, "service": "content-machine-worker-api"})
            return
        self._send(404, {"error": "not found"})

    def do_POST(self):
        path = self.path.split("?", 1)[0].rstrip("/")
        try:
            # Recovery is driven by traffic rather than a timer: whatever the
            # worker does next first returns any abandoned work to the queue.
            worker.reap()
            record = self._authenticate()
            self._send(*self._dispatch(path, record))
        except WorkerError as e:
            self._send(e.status, {"error": str(e)})
        except Exception as e:  # noqa: BLE001 - a handler must not die silently
            log.exception("unhandled error serving %s", path)
            self._send(500, {"error": f"{type(e).__name__}: {e}"})

    def _dispatch(self, path, record):
        worker_id = record["worker_id"]
        parts = [p for p in path.split("/") if p]

        if parts == ["v1", "heartbeat"]:
            payload = self._json_body()
            updated = worker.heartbeat(worker_id, payload.get("status"))
            return 200, {
                "worker": worker.worker_view(updated),
                "poll_seconds": worker.poll_seconds(),
                "heartbeat_seconds": worker.heartbeat_seconds(),
                "lease_seconds": worker.lease_seconds(),
            }

        if parts == ["v1", "claim"]:
            payload = self._json_body()
            # Asking for work is itself proof of life, so one round trip
            # serves both - the agent does not need two.
            worker.heartbeat(worker_id, payload.get("status"))
            job, reason = worker.claim(worker_id)
            if job is None:
                return 200, {
                    "job": None, "reason": reason,
                    "poll_seconds": worker.poll_seconds(),
                    "heartbeat_seconds": worker.heartbeat_seconds(),
                }
            return 200, {
                "job": {
                    "job_id": job["job_id"],
                    "lease_id": job["lease"]["lease_id"],
                    "lease_expires_at": job["lease"]["expires_at"],
                    "lease_seconds": worker.lease_seconds(),
                    "heartbeat_seconds": worker.heartbeat_seconds(),
                    "request": job["request"],
                    "attempt": job["attempt"],
                    "max_attempts": job["max_attempts"],
                    "project_id": job.get("project_id"),
                },
                "reason": reason,
            }

        if len(parts) == 4 and parts[:2] == ["v1", "jobs"]:
            job_id, action = parts[2], parts[3]
            if action == "progress":
                payload = self._json_body()
                job = worker.report_progress(
                    job_id, worker_id, self._lease_id(payload),
                    state=(payload.get("state") or None),
                    detail=payload.get("detail"),
                    provider_job_id=payload.get("provider_job_id"))
                return 200, {"state": job["state"],
                             "lease_expires_at": job["lease"]["expires_at"]}
            if action == "assets":
                return self._upload(job_id, worker_id)
            if action == "complete":
                payload = self._json_body()
                job = worker.complete(
                    job_id, worker_id, self._lease_id(payload),
                    payload.get("manifest"),
                    provider_job_id=payload.get("provider_job_id"),
                    model=payload.get("model"), notes=payload.get("notes"),
                    elapsed_seconds=payload.get("elapsed_seconds"))
                return 200, {"state": job["state"], "assets": job["assets"],
                             "manifest": job["manifest"]}
            if action == "fail":
                payload = self._json_body()
                job = worker.report_failure(
                    job_id, worker_id, self._lease_id(payload),
                    payload.get("detail") or "worker reported a failure",
                    permanent=bool(payload.get("permanent")))
                return 200, {"state": job["state"],
                             "attempt": job["attempt"],
                             "max_attempts": job["max_attempts"]}

        raise WorkerError("not found", 404)

    def _upload(self, job_id, worker_id):
        """One asset per request, raw bytes, digest declared in a header.

        Raw rather than multipart or base64: the bytes are the payload, and
        streaming them as-is avoids a 33% base64 tax on a VPS with 3.8 GB.
        """
        limit = worker.max_asset_bytes()
        lease_id = (self.headers.get("X-Lease-Id") or "").strip()
        if not lease_id:
            raise WorkerError("X-Lease-Id header is required", 400)
        filename = (self.headers.get("X-Asset-Filename") or "").strip()
        claimed = (self.headers.get("X-Asset-Sha256") or "").strip()
        data = self._body(limit)
        entry = worker.stage_asset(job_id, worker_id, lease_id, filename,
                                   data, claimed or None)
        return 200, {"staged": entry}


def serve(host=None, port=None):
    host = host or worker.api_bind()
    port = int(port or worker.api_port())
    if not worker.load_workers():
        log.warning("no workers are enrolled; every request will be refused. "
                    "Enrol one: ./content-machine worker enroll home-gpu-01 "
                    "--capabilities comfyui,sd15")
    try:
        httpd = ThreadingHTTPServer((host, port), ControlPlaneHandler)
    except OSError as e:
        log.error("cannot listen on %s:%d: %s. Set WORKER_API_PORT or pass "
                  "--port if something else already has it.", host, port, e)
        return 1
    log.info("worker control plane on http://%s:%d (queue: %s)",
             host, port, worker.queue_dir())
    if host not in ("127.0.0.1", "localhost", "::1"):
        log.warning("bound to %s: put TLS in front of this before it is "
                    "reachable from anywhere untrusted - the worker token "
                    "travels in a header", host)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        log.info("stopping")
    finally:
        httpd.server_close()
    return 0


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="[%(levelname)s] %(message)s")
    sys.exit(serve())
