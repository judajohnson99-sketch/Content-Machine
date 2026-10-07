"""Reference-only media catalog. Source bytes are never changed or copied.

Technical inspection is distinct from editorial annotation and rights evidence.
Content hashes identify media; paths are replaceable locations, never identity.
"""
import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import socket
import shutil
import subprocess
import tarfile
import tempfile
import zlib


ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CATALOG = ROOT / "library" / "catalog.json"
EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".tif", ".tiff", ".bmp",
              ".gif", ".mp4", ".mov", ".mkv", ".webm", ".avi", ".m4v",
              ".wav", ".mp3", ".flac", ".ogg", ".opus", ".aac", ".m4a"}
IMAGE_FORMATS = {"image2", "png_pipe", "jpeg_pipe", "webp_pipe", "bmp_pipe", "tiff_pipe"}


class MediaError(ValueError):
    pass


def host_id():
    """Stable machine binding: a PC path is never interpreted as a VPS path."""
    machine_id = Path("/etc/machine-id")
    identity = machine_id.read_text().strip() if machine_id.is_file() else socket.gethostname()
    return hashlib.sha256(identity.encode()).hexdigest()


def digest(path):
    value = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


def analyze_if_available(asset, path):
    """Optional inferred pixel evidence never changes an owner's annotations."""
    if asset.get("analysis"):
        return
    try:
        from . import media_intelligence
    except ImportError:
        import media_intelligence
    if (media_intelligence.audio_available() if asset["technical"]["kind"] == "audio" else media_intelligence.available()):
        try:
            analysis = media_intelligence.analyze_record(asset, path)
            if digest(path) != asset["id"]:
                raise MediaError("Media changed during analysis; rescan it first")
            asset["analysis"] = analysis
            asset.pop("analysis_error", None)
        except Exception as exc:
            # Inspection failure is visible and retryable; no invented meaning.
            asset["analysis_error"] = str(exc)[:300]


def embedded_png_metadata(path):
    """Read PNG text chunks without changing the source or requiring Pillow."""
    if Path(path).suffix.lower() != ".png":
        return {}
    result = {}
    try:
        with Path(path).open("rb") as stream:
            if stream.read(8) != b"\x89PNG\r\n\x1a\n":
                return {}
            while True:
                header = stream.read(8)
                if len(header) != 8:
                    break
                size, kind = int.from_bytes(header[:4], "big"), header[4:]
                chunk = stream.read(size)
                stream.read(4)
                if kind == b"IEND":
                    break
                if kind == b"tEXt" and b"\0" in chunk:
                    key, value = chunk.split(b"\0", 1)
                    result[key.decode("latin-1", "replace")] = value.decode("latin-1", "replace")
                elif kind == b"zTXt" and b"\0" in chunk:
                    key, value = chunk.split(b"\0", 1)
                    if value[:1] == b"\0":
                        result[key.decode("latin-1", "replace")] = zlib.decompress(value[1:]).decode("latin-1", "replace")
                elif kind == b"iTXt":
                    parts = chunk.split(b"\0", 5)
                    if len(parts) == 6:
                        key, _, flag, _, _, value = parts
                        if flag == b"\x01":
                            value = zlib.decompress(value)
                        result[key.decode("utf-8", "replace")] = value.decode("utf-8", "replace")
    except (OSError, ValueError, zlib.error):
        return {}
    return result


def load(catalog=DEFAULT_CATALOG):
    path = Path(catalog)
    if not path.exists():
        return {"version": 1, "assets": {}, "issues": {}}
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError) as exc:
        raise MediaError(f"Cannot read catalog {path}: {exc}") from exc
    if (not isinstance(data, dict) or data.get("version") != 1
            or not isinstance(data.get("assets"), dict)
            or not isinstance(data.get("issues"), dict)):
        raise MediaError("Unsupported or invalid media catalog")
    for identity, asset in data["assets"].items():
        if (not isinstance(asset, dict) or len(identity) != 64
                or any(c not in "0123456789abcdef" for c in identity)
                or asset.get("id") != identity
                or not isinstance(asset.get("technical"), dict)
                or asset["technical"].get("kind") not in {"image", "video", "audio"}
                or not isinstance(asset.get("locations"), list)
                or not isinstance(asset.get("description"), str)
                or not isinstance(asset.get("source"), str)
                or asset.get("origin") not in {"owner", "generated", "unknown"}
                or not isinstance(asset.get("tags"), list)
                or not all(isinstance(t, str) for t in asset["tags"])
                or (asset.get("rights") is not None and not isinstance(asset["rights"], dict))):
            raise MediaError(f"Invalid catalog asset: {identity}")
        for location in asset["locations"]:
            if (not isinstance(location, dict) or not isinstance(location.get("host_id"), str)
                    or not isinstance(location.get("path"), str)
                    or not Path(location["path"]).is_absolute()):
                raise MediaError(f"Invalid source location: {identity}")
    return data


def atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w") as stream:
            json.dump(value, stream, indent=2, allow_nan=False)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


@contextmanager
def catalog_lock(catalog):
    path = Path(catalog)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.with_suffix(path.suffix + ".lock").open("a") as stream:
        try:
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise MediaError("Another operation is updating this catalog") from exc
        try:
            yield
        finally:
            fcntl.flock(stream, fcntl.LOCK_UN)


def inspect_media(path):
    """Probe actual streams and fully decode them; a header alone proves little."""
    path = Path(path).resolve()
    try:
        before = path.stat()
        identity = digest(path)
        probe = subprocess.run(
            ["ffprobe", "-v", "error", "-protocol_whitelist", "file,pipe", "-show_streams", "-show_format",
             "-of", "json", str(path)], capture_output=True, text=True, timeout=60)
        if probe.returncode:
            raise MediaError(f"probe failed: {probe.stderr[-1000:]}")
        data = json.loads(probe.stdout)
        streams = [s for s in data.get("streams", [])
                   if s.get("codec_type") in ("audio", "video")
                   and not s.get("disposition", {}).get("attached_pic")]
        if not streams:
            raise MediaError("no usable audio or video stream")
        command = ["ffmpeg", "-nostdin", "-v", "error", "-xerror",
                   "-protocol_whitelist", "file,pipe", "-i", str(path)]
        for stream in streams:
            command += ["-map", f"0:{stream['index']}"]
        decoded = subprocess.run(command + ["-f", "null", "-"],
                                 capture_output=True, text=True, timeout=3600)
        if decoded.returncode or decoded.stderr.strip():
            raise MediaError(f"decode failed: {decoded.stderr[-1000:]}")
        after = path.stat()
        if (before.st_ino, before.st_size, before.st_mtime_ns) != (
                after.st_ino, after.st_size, after.st_mtime_ns):
            raise MediaError("source changed during inspection; retry when stable")
        fmt = data.get("format", {})
        video = any(s["codec_type"] == "video" for s in streams)
        is_image = video and fmt.get("format_name") in IMAGE_FORMATS
        kind = "image" if is_image else "video" if video else "audio"
        fields = ("index", "codec_type", "codec_name", "width", "height", "pix_fmt",
                  "r_frame_rate", "avg_frame_rate", "time_base", "duration",
                  "sample_rate", "channels", "channel_layout", "color_space",
                  "color_transfer", "color_primaries", "side_data_list")
        tags = dict(fmt.get("tags") or {})
        for stream in streams:
            tags.update({f"stream{stream['index']}:{k}": v
                         for k, v in (stream.get("tags") or {}).items()})
        embedded = embedded_png_metadata(path)
        parsed = {}
        for key in ("parameters", "prompt", "workflow"):
            if key in embedded:
                try:
                    parsed[key] = json.loads(embedded[key])
                except (ValueError, TypeError):
                    parsed[key] = embedded[key]
        return {"id": identity, "bytes": after.st_size, "kind": kind,
                "format": fmt.get("format_name"), "duration_seconds": fmt.get("duration"),
                "streams": [{k: s[k] for k in fields if k in s} for s in streams],
                "embedded_metadata": {"text": embedded, "parsed": parsed,
                                       "ffprobe_tags": tags},
                "inspection": "full_decode", "inspected_utc": datetime.now(timezone.utc).isoformat()}
    except (OSError, ValueError, subprocess.TimeoutExpired) as exc:
        raise MediaError(str(exc)) from exc


def scan(source, catalog=DEFAULT_CATALOG):
    """Explicit file/folder discovery. Commit each result so interrupted scans resume safely.

    Directory symlinks are not traversed. Re-scanning decodes again, preserving
    annotations only when the source bytes have the same content identity.
    """
    source = Path(source).expanduser().absolute()
    if not source.exists():
        raise MediaError(f"Source is not accessible: {source}")
    if not source.is_file() and not source.is_dir():
        raise MediaError(f"Source is not a regular file or directory: {source}")
    paths = []
    if source.is_file():
        paths.append(source)
    else:
        def walk_error(exc):
            raise MediaError(f"Cannot enumerate source: {exc}")
        for base, dirs, files in os.walk(source, onerror=walk_error, followlinks=False):
            dirs.sort()
            paths.extend(Path(base) / name for name in sorted(files))
    report = {"indexed": 0, "issues": []}
    with catalog_lock(catalog):
        data = load(catalog)
        for path in paths:
            if path.resolve() in {Path(catalog).resolve(), Path(str(catalog) + ".lock").resolve()}:
                continue
            location = str(path.absolute())
            if path.suffix.lower() not in EXTENSIONS:
                issue = "unsupported extension (not decoded)"
            elif not path.is_file():
                issue = "missing or non-regular source"
            else:
                try:
                    inspected = inspect_media(path)
                    identity = inspected.pop("id")
                    asset = data["assets"].setdefault(identity, {
                        "id": identity, "locations": [], "description": "", "tags": [],
                        "origin": "unknown", "source": "", "rights": None,
                    })
                    asset["technical"] = inspected
                    analyze_if_available(asset, path)
                    reference = {"host_id": host_id(), "path": location}
                    if reference not in asset["locations"]:
                        asset["locations"].append(reference)
                    data["issues"].pop(location, None)
                    report["indexed"] += 1
                    atomic_json(catalog, data)
                    continue
                except MediaError as exc:
                    issue = str(exc)
            data["issues"][location] = issue
            report["issues"].append({"path": location, "problem": issue})
            atomic_json(catalog, data)
        # A removed source remains in the catalog so references do not disappear.
        for asset in data["assets"].values():
            for location in asset["locations"]:
                if location["host_id"] != host_id():
                    continue
                path = Path(location["path"])
                if (path == source or source in path.parents) and not path.is_file():
                    data["issues"][str(path)] = "missing source"
        atomic_json(catalog, data)
    return report


def import_generated(path, *, job_id, asset_index, prompt, negative_prompt=None,
                     model=None, provider="comfyui", width=None, height=None,
                     seed=None, settings=None, label=None, generated_utc=None,
                     catalog=DEFAULT_CATALOG):
    """Register one completed Image Lab result in the normal media catalog.

    The generated file remains in the worker job store; the catalog records
    that existing location and its hash. Repeating the same job/index is
    idempotent, while a new job that happens to produce identical bytes is
    de-duplicated by the content identity and gains another location.
    """
    source_path = Path(path).resolve()
    if not source_path.is_file():
        raise MediaError(f"Generated result is no longer available: {source_path}")
    inspected = inspect_media(source_path)
    if inspected.get("kind") != "image":
        raise MediaError("Only generated image results can be saved to the asset library")
    identity = inspected.pop("id")
    location = {"host_id": host_id(), "path": str(source_path)}
    generation = {
        "source": "Content Machine Image Lab",
        "provider": provider,
        "generation_job_id": job_id,
        "asset_index": int(asset_index),
        "prompt": prompt,
        "negative_prompt": negative_prompt,
        "model": model,
        "width": width,
        "height": height,
        "seed": seed,
        "settings": settings or {},
        "generated_utc": generated_utc or inspected.get("inspected_utc"),
    }
    with catalog_lock(catalog):
        data = load(catalog)
        for existing in data["assets"].values():
            provenance = existing.get("provenance") or {}
            if (provenance.get("generation_job_id") == job_id and
                    provenance.get("asset_index") == int(asset_index)):
                if location not in existing["locations"]:
                    existing["locations"].append(location)
                    atomic_json(catalog, data)
                return existing
        asset = data["assets"].setdefault(identity, {
            "id": identity, "locations": [],
            "description": label or "Generated Image Lab result",
            "tags": ["generated", "image-lab"], "origin": "generated",
            "source": "Content Machine Image Lab",
            "rights": {"source": "Owner-generated locally", "license": "Owner-created",
                        "commercial_use": True, "attribution_required": False,
                        "attribution_text": None, "evidence": "Generated by the owner's Content Machine worker",
                        "status": "DECLARED"},
        })
        asset["technical"] = inspected
        analyze_if_available(asset, source_path)
        asset["origin"] = "generated"
        asset["source"] = "Content Machine Image Lab"
        if not isinstance(asset.get("rights"), dict):
            asset["rights"] = {
                "source": "Owner-generated locally", "license": "Owner-created",
                "commercial_use": True, "attribution_required": False,
                "attribution_text": None,
                "evidence": "Generated by the owner's Content Machine worker",
                "status": "DECLARED",
            }
        if not (asset.get("description") or "").strip():
            asset["description"] = label or "Generated Image Lab result"
        asset["provenance"] = {**(asset.get("provenance") or {}), **generation}
        tags = set(asset.get("tags") or [])
        tags.update(("generated", "image-lab"))
        asset["tags"] = sorted(tags)
        if location not in asset["locations"]:
            asset["locations"].append(location)
        atomic_json(catalog, data)
        return asset


def merge(inventory, catalog=DEFAULT_CATALOG):
    """Bring a local-worker inventory to the control plane, without media bytes.

    Existing annotations win; blank records inherit incoming annotations.
    New locations supplement old ones by content id.
    Remote paths remain remote until the selected bytes are staged and scanned
    on this host. This does not authorize transfer or assert rights.
    """
    if not Path(inventory).is_file():
        raise MediaError(f"Inventory is not accessible: {inventory}")
    incoming = load(inventory)
    with catalog_lock(catalog):
        data = load(catalog)
        for identity, asset in incoming["assets"].items():
            if identity not in data["assets"]:
                data["assets"][identity] = asset
            else:
                known = data["assets"][identity]
                if not known.get("description"):
                    for key in ("description", "tags", "origin", "source"):
                        known[key] = asset[key]
                if known.get("rights") is None:
                    known["rights"] = asset.get("rights")
                analysis = asset.get("analysis")
                if (isinstance(analysis, dict) and analysis.get("source_sha256") == identity
                        and isinstance(analysis.get("model"), str)
                        and isinstance(analysis.get("embedding"), list)
                        and len(analysis["embedding"]) == 512
                        and all(isinstance(v, (float, int)) and -1 <= v <= 1 for v in analysis["embedding"])):
                    known["analysis"] = analysis
                for location in asset["locations"]:
                    if location not in known["locations"]:
                        known["locations"].append(location)
        atomic_json(catalog, data)
    return {"merged_assets": len(incoming["assets"]), "media_transferred": 0}


def annotate(identity, *, description, tags, origin, source, rights=None, catalog=DEFAULT_CATALOG):
    """Explicit editorial/source evidence, never inferred from a filename."""
    if origin not in {"owner", "generated", "unknown"}:
        raise MediaError("origin must be owner, generated or unknown")
    if not isinstance(description, str) or not description.strip():
        raise MediaError("description must describe inspected content")
    if not isinstance(tags, list) or not all(isinstance(t, str) and t.strip() for t in tags):
        raise MediaError("tags must be nonempty strings")
    if not isinstance(source, str) or not source.strip():
        raise MediaError("source must identify the source/provenance record")
    if rights is not None and not isinstance(rights, dict):
        raise MediaError("rights must be an object")
    with catalog_lock(catalog):
        data = load(catalog)
        if identity not in data["assets"]:
            raise MediaError(f"Unknown asset: {identity}")
        data["assets"][identity].update(description=description.strip(), tags=tags,
                                       origin=origin, source=source, rights=rights)
        atomic_json(catalog, data)
        return data["assets"][identity]


def set_provenance(*, catalog=DEFAULT_CATALOG, image_source=None,
                   audio_source=None, evidence=None):
    """Apply an operator's source declaration without inventing rights.

    Image generation metadata is filled only by a later local scan when the
    PNG bytes are present. Audio rights remain per-asset and unverified.
    """
    with catalog_lock(catalog):
        data = load(catalog)
        changed = {"images": 0, "audio": 0}
        for asset in data["assets"].values():
            kind = asset["technical"]["kind"]
            if kind == "image" and image_source:
                asset["origin"] = "owner"
                asset["source"] = image_source
                asset["description"] = asset.get("description") or "Owner-generated Dreamdrip image; visual content requires review."
                tags = set(asset.get("tags") or ())
                tags.update(("dreamdrip", "owner-generated", "comfyui"))
                asset["tags"] = sorted(tags)
                asset["provenance"] = {**(asset.get("provenance") or {}),
                                       "generation": "ComfyUI", "metadata_status": "pending_local_bytes",
                                       "evidence": evidence or "Owner declaration received 2026-09-21"}
                asset["rights"] = {"source": "Owner-generated locally",
                                    "license": "Owner-created", "commercial_use": True,
                                    "attribution_required": False, "attribution_text": None,
                                    "evidence": evidence or "Owner declaration received 2026-09-21",
                                    "status": "DECLARED"}
                changed["images"] += 1
            elif kind == "audio" and audio_source:
                asset["origin"] = "owner"
                asset["source"] = audio_source
                asset["description"] = asset.get("description") or "Downloaded audio asset; exact track identity and use terms require verification."
                tags = set(asset.get("tags") or ())
                tags.update(("dreamdrip", "youtube-audio-library", "rights-unverified"))
                asset["tags"] = sorted(tags)
                asset["provenance"] = {**(asset.get("provenance") or {}),
                                       "library": "YouTube Audio Library", "rights_status": "UNVERIFIED",
                                       "evidence": evidence or "Owner declaration received 2026-09-21"}
                asset["rights"] = {"source": "YouTube Audio Library", "license": "unknown",
                                    "commercial_use": None, "attribution_required": None,
                                    "attribution_text": None,
                                    "evidence": evidence or "Owner declaration; no track-level record supplied",
                                    "status": "UNVERIFIED"}
                changed["audio"] += 1
        atomic_json(catalog, data)
    return changed


def search(*, query="", kind=None, catalog=DEFAULT_CATALOG):
    terms = query.casefold().split()
    assets = [asset for asset in load(catalog)["assets"].values()
              if kind is None or asset["technical"]["kind"] == kind]
    if query.strip() and any(asset.get("analysis") for asset in assets):
        try:
            from . import media_intelligence
        except ImportError:
            import media_intelligence
        return media_intelligence.rank(assets, query)
    return [asset for asset in assets
            if (kind is None or asset["technical"]["kind"] == kind)
            and all(term in " ".join([asset["description"], asset["source"], *asset["tags"]]).casefold()
                    for term in terms)]


def resolve(asset):
    """Resolve by bytes; prefer any intact location over a missing/changed alias."""
    problems = []
    for location in asset["locations"]:
        if location["host_id"] != host_id():
            problems.append(f"remote source needs staging: {location['path']}")
            continue
        path = Path(location["path"])
        try:
            if not path.is_file():
                problems.append(f"missing: {path}")
            elif digest(path) != asset["id"]:
                problems.append(f"changed: {path}")
            else:
                return path.resolve()
        except OSError as exc:
            problems.append(f"unreadable: {path}: {exc}")
    raise MediaError(f"No intact source for {asset['id']}: {'; '.join(problems)}")


def rights_record(asset):
    rights = asset.get("rights") or {}
    if (rights.get("commercial_use") is not True
            or any(not isinstance(rights.get(k), str) or not rights[k].strip()
                   for k in ("source", "license", "evidence"))
            or (rights.get("attribution_required") and not rights.get("attribution_text"))):
        raise MediaError(f"Asset {asset['id']} needs explicit rights and evidence before production use")
    # A library name is not a track-level licence. Keep imported YouTube Audio
    # Library files previewable, but require the specific download record or
    # licence terms before they can enter a production.
    if rights.get("source") == "YouTube Audio Library" and rights.get("status") != "VERIFIED":
        raise MediaError(f"Asset {asset['id']} needs track-level YouTube Audio Library terms verified")
    return rights


def select(identity, *, kind, catalog=DEFAULT_CATALOG, require_rights=True):
    asset = load(catalog)["assets"].get(identity)
    if asset is None:
        raise MediaError(f"Unknown asset: {identity}")
    if asset["technical"]["kind"] != kind:
        raise MediaError(f"Asset {identity} is {asset['technical']['kind']}, expected {kind}")
    if not asset.get("description") or not asset.get("source"):
        raise MediaError(f"Asset {identity} needs content annotation and provenance before selection")
    if require_rights:
        rights_record(asset)
    return asset, resolve(asset)


def check(catalog=DEFAULT_CATALOG):
    results = []
    for asset in load(catalog)["assets"].values():
        try:
            results.append({"id": asset["id"], "status": "AVAILABLE", "path": str(resolve(asset))})
        except MediaError as exc:
            results.append({"id": asset["id"], "status": "UNAVAILABLE", "problem": str(exc)})
    return results


def transfer_request(identities, mode, *, catalog=DEFAULT_CATALOG):
    """A bounded selection, owned by the VPS; filenames carry no selection authority."""
    if mode not in {"preview", "source"} or not identities or len(set(identities)) != len(identities):
        raise MediaError("Select unique asset IDs and mode preview or source")
    assets = load(catalog)["assets"]
    if any(identity not in assets for identity in identities):
        raise MediaError("Transfer request contains an unknown asset")
    return {"version": 1, "mode": mode, "asset_ids": list(identities)}


def _load_transfer_request(path, catalog):
    value = json.loads(Path(path).read_text())
    if not isinstance(value, dict) or value.get("version") != 1:
        raise MediaError("Unsupported transfer request")
    return transfer_request(value.get("asset_ids"), value.get("mode"), catalog=catalog)


def export_bundle(request_path, output, *, catalog=DEFAULT_CATALOG):
    """Run on the source host. Stream selected originals, or small inspection derivatives.

    No rights assertion: permission to inspect/transfer is distinct from
    permission to use an asset in a published production. Never writes originals.
    """
    request = _load_transfer_request(request_path, catalog)
    assets = load(catalog)["assets"]
    sources = [(assets[i], resolve(assets[i])) for i in request["asset_ids"]]
    output = Path(output).expanduser().resolve()
    if any(output == path or output.is_relative_to(path.parent) for _, path in sources):
        raise MediaError("Bundle must be outside the original media directories")
    if output.exists():
        raise MediaError(f"Bundle already exists: {output}; use a new output name")
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="cm-transfer-", dir=output.parent) as temporary:
        work = Path(temporary)
        pending = work / "bundle.tar.gz"
        entries = []
        with tarfile.open(pending, "w:gz") as archive:
            for asset, source in sources:
                identity, kind = asset["id"], asset["technical"]["kind"]
                path = source
                if request["mode"] == "preview":
                    if kind in {"image", "video"}:
                        path = work / f"{identity}.jpg"
                        command = ["ffmpeg", "-nostdin", "-v", "error", "-i", str(source),
                                   "-frames:v", "1", "-vf", "scale=480:480:force_original_aspect_ratio=decrease",
                                   "-q:v", "3", str(path)]
                    else:
                        path = work / f"{identity}.mp3"
                        duration = float(asset["technical"].get("duration_seconds") or 0)
                        start = max(0, min(duration / 3, duration - 25))
                        command = ["ffmpeg", "-nostdin", "-v", "error", "-ss", str(start),
                                   "-i", str(source), "-t", "25", "-vn", "-c:a", "libmp3lame",
                                   "-b:a", "128k", str(path)]
                    result = subprocess.run(command, capture_output=True, text=True, timeout=120)
                    if result.returncode:
                        raise MediaError(f"Preview failed for {identity}: {result.stderr[-500:]}")
                filename = identity + path.suffix.lower()
                archive.add(path, arcname=filename, recursive=False)
                entries.append({"asset_id": identity, "filename": filename,
                                "sha256": digest(path), "bytes": path.stat().st_size,
                                "kind": kind, "source_host_id": host_id(),
                                "source_sha256": identity})
                if digest(source) != identity:
                    raise MediaError(f"Source changed during transfer: {source}")
            manifest_path = work / "manifest.json"
            atomic_json(manifest_path, {"request": request, "entries": entries})
            archive.add(manifest_path, arcname="manifest.json")
        os.replace(pending, output)
    return {"bundle": str(output), "assets": len(entries), "bytes": output.stat().st_size}


def receive_bundle(bundle, request_path, *, catalog=DEFAULT_CATALOG):
    """Verify an outbound HP handoff before publishing local asset locations."""
    request = _load_transfer_request(request_path, catalog)
    data = load(catalog)
    request_id = hashlib.sha256(json.dumps(request, sort_keys=True).encode()).hexdigest()
    destination = Path(catalog).resolve().parent / ("objects" if request["mode"] == "source"
                                                    else "previews")
    destination.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".receiving-", dir=destination) as temporary:
        staging = Path(temporary)
        with tarfile.open(bundle, "r:gz") as archive:
            members = archive.getmembers()
            names = [member.name for member in members]
            if len(names) != len(set(names)) or len(names) != len(request["asset_ids"]) + 1:
                raise MediaError("Bundle has duplicate, extra or missing members")
            if any(not m.isfile() or Path(m.name).name != m.name for m in members):
                raise MediaError("Bundle contains unsafe paths or non-regular members")
            manifest_member = archive.getmember("manifest.json")
            if manifest_member.size > 1024 * 1024:
                raise MediaError("Bundle manifest is too large")
            manifest = json.load(archive.extractfile(manifest_member))
            if manifest.get("request") != request:
                raise MediaError("Bundle does not match the requested selection")
            entries = manifest.get("entries", [])
            if (len(entries) != len(request["asset_ids"])
                    or {entry["asset_id"] for entry in entries} != set(request["asset_ids"])):
                raise MediaError("Bundle manifest differs from selected IDs")
            if set(names) != {entry["filename"] for entry in entries} | {"manifest.json"}:
                raise MediaError("Bundle bytes and manifest disagree")
            for entry in entries:
                identity = entry["asset_id"]
                filename = entry["filename"]
                if Path(filename).stem != identity or Path(filename).suffix not in EXTENSIONS:
                    raise MediaError("Bundle filename must be its selected content ID")
                member = archive.getmember(filename)
                asset = data["assets"][identity]
                limit = asset["technical"]["bytes"] if request["mode"] == "source" else 5 * 1024 * 1024
                if member.size != entry["bytes"] or member.size > limit:
                    raise MediaError("Bundle asset size differs from the declared limit")
                path = staging / filename
                with archive.extractfile(member) as src, path.open("wb") as dst:
                    shutil.copyfileobj(src, dst, 1024 * 1024)
                actual = digest(path)
                if actual != entry["sha256"] or (request["mode"] == "source" and actual != identity):
                    raise MediaError("Bundle asset hash mismatch")
                inspected = inspect_media(path)
                expected_kind = ("image" if request["mode"] == "preview" and asset["technical"]["kind"] == "video" else asset["technical"]["kind"])
                if inspected["kind"] != expected_kind:
                    raise MediaError(
                        f"Bundle asset kind mismatch for {identity}: "
                        f"got {inspected['kind']}, expected {asset['technical']['kind']}"
                    )
                # Keep the receiving-side decode and embedded metadata in the
                # receipt. The catalog remains the authoritative source record;
                # this is evidence that the bytes actually staged are usable.
                entry["received_technical"] = inspected
                entry["path"] = str(destination / filename)
            # Nothing is published until every staged byte and requested ID checks out.
            with catalog_lock(catalog):
                data = load(catalog)
                for entry in entries:
                    target = Path(entry["path"])
                    if target.exists() and digest(target) != entry["sha256"]:
                        raise MediaError(f"Existing asset differs: {target}")
                for entry in entries:
                    target = Path(entry["path"])
                    if not target.exists():
                        os.replace(staging / entry["filename"], target)
                    if request["mode"] == "preview":
                        asset = data["assets"][entry["asset_id"]]
                        asset["preview"] = {"path": str(target), "sha256": entry["sha256"], "kind": entry["received_technical"]["kind"]}
                        try:
                            from . import media_intelligence
                        except ImportError:
                            import media_intelligence
                        supported = media_intelligence.audio_available() if asset["technical"]["kind"] == "audio" else media_intelligence.available()
                        if supported and not asset.get("analysis"):
                            try:
                                proxy = dict(asset, technical=entry["received_technical"])
                                asset["analysis"] = media_intelligence.analyze_record(proxy, target)
                                asset["analysis"]["preview_sha256"] = entry["sha256"]
                            except Exception as exc:
                                asset["analysis_error"] = str(exc)[:300]
                    if request["mode"] == "source":
                        location = {"host_id": host_id(), "path": str(target)}
                        asset = data["assets"][entry["asset_id"]]
                        if location not in asset["locations"]:
                            asset["locations"].append(location)
                        # Replace the remote technical snapshot with the
                        # receiving-side probe. This is where embedded
                        # ComfyUI PNG text becomes durable catalog evidence.
                        received = entry.get("received_technical")
                        if received:
                            asset["technical"] = received
                            analyze_if_available(asset, target)
                            if (asset.get("provenance") or {}).get("generation") == "ComfyUI":
                                asset["provenance"]["metadata_status"] = (
                                    "extracted" if received.get("embedded_metadata", {}).get("text")
                                    else "absent")
                atomic_json(catalog, data)
                atomic_json(destination / f"receipt-{request_id}.json", manifest)
    return {"received": len(entries), "mode": request["mode"], "directory": str(destination)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--catalog", type=Path, default=DEFAULT_CATALOG)
    sub = parser.add_subparsers(dest="command", required=True)
    scan_parser = sub.add_parser("scan", help="read and fully decode a file/folder without modifying it")
    scan_parser.add_argument("source")
    find = sub.add_parser("list", help="search annotations, never guessed filename meanings")
    find.add_argument("--query", default="")
    find.add_argument("--kind", choices=("image", "video", "audio"))
    annotation = sub.add_parser("annotate")
    annotation.add_argument("id")
    annotation.add_argument("--description", required=True)
    annotation.add_argument("--tag", action="append", default=[])
    annotation.add_argument("--origin", choices=("owner", "generated", "unknown"), required=True)
    annotation.add_argument("--source", required=True)
    annotation.add_argument("--rights", type=Path, help="JSON rights/evidence record")
    sub.add_parser("check", help="recheck source existence and full content hashes")
    provenance = sub.add_parser("provenance", help="record owner source declarations; never clears rights automatically")
    provenance.add_argument("--images", default=None, help="source declaration for owner-generated images")
    provenance.add_argument("--audio", default=None, help="source declaration for downloaded audio")
    provenance.add_argument("--evidence", default=None)
    merging = sub.add_parser("merge", help="merge an inventory; remote sources remain unavailable locally")
    merging.add_argument("inventory", type=Path)
    exporting = sub.add_parser("export", help="source-host bundle for an explicit inspection/source request")
    exporting.add_argument("request", type=Path)
    exporting.add_argument("--output", type=Path, required=True)
    receiving = sub.add_parser("receive", help="verify and accept a selected-media bundle on this host")
    receiving.add_argument("bundle", type=Path)
    receiving.add_argument("--request", type=Path, required=True)
    args = parser.parse_args()
    try:
        if args.command == "scan":
            result = scan(args.source, args.catalog)
        elif args.command == "annotate":
            rights = json.loads(args.rights.read_text()) if args.rights else None
            result = annotate(args.id, description=args.description, tags=args.tag,
                              origin=args.origin, source=args.source, rights=rights, catalog=args.catalog)
        elif args.command == "list":
            result = search(query=args.query, kind=args.kind, catalog=args.catalog)
        elif args.command == "merge":
            result = merge(args.inventory, args.catalog)
        elif args.command == "export":
            result = export_bundle(args.request, args.output, catalog=args.catalog)
        elif args.command == "receive":
            result = receive_bundle(args.bundle, args.request, catalog=args.catalog)
        elif args.command == "provenance":
            result = set_provenance(catalog=args.catalog, image_source=args.images,
                                    audio_source=args.audio, evidence=args.evidence)
        else:
            result = check(args.catalog)
        print(json.dumps(result, indent=2))
        return int((args.command == "scan" and bool(result["issues"]))
                   or (args.command == "check" and any(r["status"] != "AVAILABLE" for r in result)))
    except (MediaError, OSError, ValueError, KeyError, tarfile.TarError, subprocess.TimeoutExpired) as exc:
        parser.exit(1, f"Media error: {exc}\n")


if __name__ == "__main__":
    raise SystemExit(main())
