"""Outbound owner-media transport over the authenticated worker connection.

Requests contain catalog identities, never arbitrary PC paths. The existing
bundle verifier owns all hash, decode and atomic promotion checks. Generation
jobs and their request digests remain independent of library transfers.
"""
import hashlib
import json
import os
from pathlib import Path
import tempfile
import tarfile
import time
import uuid

try:
    from . import media, worker
except ImportError:
    import media
    import worker

LEASE_SECONDS = 1800


def _directory(catalog):
    return Path(catalog).parent / "transfers"


def _path(identity, catalog):
    if len(identity) != 64 or any(c not in "0123456789abcdef" for c in identity):
        raise media.MediaError("Invalid transfer identity")
    return _directory(catalog) / f"{identity}.json"


def transfers(catalog=media.DEFAULT_CATALOG):
    return [json.loads(p.read_text()) for p in sorted(_directory(catalog).glob("*.json"))]


def request_transfer(identity, mode="source", catalog=media.DEFAULT_CATALOG):
    request = media.transfer_request([identity], mode, catalog=catalog)
    key = hashlib.sha256(json.dumps(request, sort_keys=True).encode()).hexdigest()
    with media.catalog_lock(_directory(catalog) / "state"):
        path = _path(key, catalog)
        if path.exists():
            previous = json.loads(path.read_text())
            if previous["state"] in {"WAITING", "TRANSFERRING"}:
                return previous
        job = {"id": key, "request": request, "state": "WAITING",
               "message": "Waiting for your connected computer", "attempt": 0,
               "lease_id": None, "worker_id": None, "expires": 0,
               "updated_at": time.time()}
        media.atomic_json(path, job)
        return job


def claim(worker_id, host, catalog=media.DEFAULT_CATALOG):
    """Only offer sources this authenticated worker inventoried itself."""
    assets = media.load(catalog)["assets"]
    with media.catalog_lock(_directory(catalog) / "state"):
        for job in sorted(transfers(catalog), key=lambda item: (item["request"]["mode"] != "source", item["updated_at"])):
            if job["state"] == "TRANSFERRING" and job["expires"] < time.time():
                exhausted = job["attempt"] >= 3
                job.update(state="FAILED" if exhausted else "WAITING", lease_id=None,
                           message="Retrieval was interrupted. Retry when your computer is connected." if exhausted else "Waiting for your computer to reconnect")
                media.atomic_json(_path(job["id"], catalog), job)
            if job["state"] != "WAITING":
                continue
            asset = assets[job["request"]["asset_ids"][0]]
            if not any(loc.get("worker_id") == worker_id and loc["host_id"] == host
                       for loc in asset["locations"]):
                continue
            job.update(state="TRANSFERRING", worker_id=worker_id,
                       lease_id=uuid.uuid4().hex, expires=time.time() + LEASE_SECONDS,
                       attempt=job["attempt"] + 1, message="Retrieving your media",
                       updated_at=time.time())
            media.atomic_json(_path(job["id"], catalog), job)
            return job
    return None


def _leased(identity, worker_id, lease_id, catalog):
    path = _path(identity, catalog)
    if not path.exists():
        raise media.MediaError("Transfer no longer exists")
    job = json.loads(path.read_text())
    if (job["state"] != "TRANSFERRING" or job["worker_id"] != worker_id
            or not lease_id or job["lease_id"] != lease_id or job["expires"] < time.time()):
        raise media.MediaError("Transfer expired or belongs to another attempt; retry retrieval")
    return job


def fail(identity, worker_id, lease_id, message, catalog=media.DEFAULT_CATALOG):
    with media.catalog_lock(_directory(catalog) / "state"):
        job = _leased(identity, worker_id, lease_id, catalog)
        job.update(state="FAILED", message=str(message)[:500], lease_id=None, updated_at=time.time())
        media.atomic_json(_path(identity, catalog), job)
    return job


def receive(identity, worker_id, lease_id, bundle, catalog=media.DEFAULT_CATALOG):
    with media.catalog_lock(_directory(catalog) / "state"):
        job = _leased(identity, worker_id, lease_id, catalog)
        # Independent of untrusted catalog sizes and compressed upload length.
        with tarfile.open(bundle, "r:gz") as archive:
            total = 0
            for index, member in enumerate(archive):
                total += member.size
                if index > 1 or total > 512 * 1024 * 1024:
                    raise media.MediaError("Expanded media transfer exceeds the 512 MB limit")
        with tempfile.TemporaryDirectory(prefix="cm-media-request-") as temporary:
            request_path = Path(temporary) / "request.json"
            media.atomic_json(request_path, job["request"])
            result = media.receive_bundle(bundle, request_path, catalog=catalog)
        job.update(state="READY", message="Ready in your library", lease_id=None,
                   result=result, updated_at=time.time())
        media.atomic_json(_path(identity, catalog), job)
    return job


def inventory(worker_id, payload, catalog=media.DEFAULT_CATALOG):
    """Bind remote locations to the token's identity; preserve local rights."""
    host = payload.get("host_id")
    if not isinstance(host, str) or len(host) != 64 or host == media.host_id():
        raise media.MediaError("Inventory needs its own source computer identity")
    data = payload.get("catalog")
    if not isinstance(data, dict):
        raise media.MediaError("Inventory must contain a catalog")
    with tempfile.TemporaryDirectory(prefix="cm-inventory-") as temporary:
        incoming = Path(temporary) / "catalog.json"
        media.atomic_json(incoming, data)
        checked = media.load(incoming)
        for asset in checked["assets"].values():
            asset["locations"] = [dict(loc, worker_id=worker_id)
                                  for loc in asset["locations"] if loc["host_id"] == host]
            if not asset["locations"]:
                raise media.MediaError("Inventory contains media from another computer")
        media.atomic_json(incoming, checked)
        result = media.merge(incoming, catalog=catalog)
    existing = {job["id"] for job in transfers(catalog)}
    for identity in checked["assets"]:
        request = media.transfer_request([identity], "preview", catalog=catalog)
        key = hashlib.sha256(json.dumps(request, sort_keys=True).encode()).hexdigest()
        if key not in existing and not any((Path(catalog).parent / "previews").glob(f"{identity}.*")):
            request_transfer(identity, "preview", catalog=catalog)
    return result


def sources():
    return [{"id": w["worker_id"], "name": w["worker_id"],
             "state": worker.worker_state(w),
             "connected": bool((w.get("status") or {}).get("media_sync")),
             "detail": (w.get("status") or {}).get("media_detail")}
            for w in worker.load_workers()]


def local_roots():
    """Use configured folders, or the owner's established Dreamdrip library."""
    configured = os.environ.get("WORKER_MEDIA_ROOTS", "")
    if configured:
        return [Path(p).expanduser().resolve() for p in configured.split(os.pathsep) if p.strip()]
    established = Path.home() / "Videos/dreamdrip/assets"
    return [established.resolve()] if established.is_dir() else []
