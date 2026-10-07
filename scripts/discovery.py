"""Free Openverse discovery, with source/licence evidence retained on import.

API reference: https://api.openverse.org/v1/
Indexed licensing is a lead to inspect, not an owner clearance. Imports remain
blocked until the creator confirms the source's actual terms in the library.
"""
import json
from pathlib import Path
import re
import tempfile
import urllib.error
import urllib.parse
import urllib.request

try:
    from . import media
except ImportError:
    import media

API = "https://api.openverse.org/v1"
DOWNLOAD_HOSTS = ("wikimedia.org", "flickr.com", "staticflickr.com", "freesound.org",
                  "jamendo.com", "archive.org", "nasa.gov", "metmuseum.org")


def _json(url):
    try:
        request = urllib.request.Request(url, headers={"User-Agent": "ContentMachine/1.0 (creator media discovery)"})
        with urllib.request.urlopen(request, timeout=20) as response:
            return json.loads(response.read(2 * 1024 * 1024))
    except (OSError, ValueError) as exc:
        raise media.MediaError("Media search is temporarily unavailable. Retry shortly.") from exc


def _kind(kind):
    if kind not in {"image", "audio"}:
        raise media.MediaError("External discovery currently supports images and audio")
    return "images" if kind == "image" else "audio"


def search(query, kind="image"):
    endpoint = _kind(kind)
    if not isinstance(query, str) or not query.strip():
        raise media.MediaError("Describe the picture or sound you need")
    params = urllib.parse.urlencode({"q": query[:300], "license": "cc0,pdm,by",
                                    "page_size": 20, "mature": "false"})
    data = _json(f"{API}/{endpoint}/?{params}")
    return [{key: item.get(key) for key in ("id", "title", "creator", "foreign_landing_url",
             "thumbnail", "url", "license", "license_url", "attribution", "source")}
            for item in data.get("results", []) if item.get("license") in {"cc0", "pdm", "by"}]


def _safe_download(url):
    try:
        parsed = urllib.parse.urlparse(url)
        host = (parsed.hostname or "").lower()
        safe = (parsed.scheme == "https" and not parsed.username and not parsed.password
                and parsed.port in (None, 443)
                and any(host == allowed or host.endswith("." + allowed) for allowed in DOWNLOAD_HOSTS))
    except (ValueError, TypeError, AttributeError):
        safe = False
    if not safe:
        raise media.MediaError("This source does not support direct import. Open its source page to download and inspect it.")


class _SafeRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, newurl):
        _safe_download(newurl)
        return super().redirect_request(request, fp, code, msg, headers, newurl)


def import_asset(identity, kind="image", catalog=media.DEFAULT_CATALOG):
    endpoint = _kind(kind)
    if not isinstance(identity, str) or not re.fullmatch(r"[a-f0-9-]{36}", identity):
        raise media.MediaError("Invalid external asset identity")
    item = _json(f"{API}/{endpoint}/{identity}/")
    if item.get("license") not in {"cc0", "pdm", "by"} or not item.get("license_url") or not item.get("foreign_landing_url"):
        raise media.MediaError("The source no longer provides supported licence evidence; import refused")
    url = item.get("url") or ""
    _safe_download(url)
    suffix = Path(urllib.parse.urlparse(url).path).suffix.lower()
    if suffix not in media.EXTENSIONS:
        suffix = ".jpg" if kind == "image" else ".mp3"
    destination = Path(catalog).parent / "objects"
    destination.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".discovery-", dir=destination) as temporary:
        path = Path(temporary) / ("download" + suffix)
        try:
            with urllib.request.build_opener(_SafeRedirect()).open(
                    urllib.request.Request(url, headers={"User-Agent": "ContentMachine/1.0"}), timeout=30) as response, path.open("wb") as output:
                total = 0
                while chunk := response.read(1024 * 1024):
                    total += len(chunk)
                    if total > 100 * 1024 * 1024:
                        raise media.MediaError("This asset exceeds the 100 MB direct import limit")
                    output.write(chunk)
        except OSError as exc:
            raise media.MediaError("Download interrupted. Nothing was imported; retry or choose another source.") from exc
        technical = media.inspect_media(path)
        if technical["kind"] != kind:
            raise media.MediaError("Downloaded file does not match the selected media type")
        digest = technical.pop("id")
        target = destination / (digest + suffix)
        with media.catalog_lock(catalog):
            data = media.load(catalog)
            if target.exists() and media.digest(target) != digest:
                raise media.MediaError("Existing library object changed; import refused")
            if not target.exists():
                path.replace(target)
            asset = data["assets"].setdefault(digest, {
                "id": digest, "description": item.get("title") or "Imported media",
                "tags": [tag["name"] for tag in item.get("tags", []) if isinstance(tag, dict) and isinstance(tag.get("name"), str)],
                "origin": "unknown", "source": item["foreign_landing_url"], "locations": [],
                "technical": technical,
                "rights": {"source": item["foreign_landing_url"], "license": item["license_url"],
                           "commercial_use": False, "evidence": f"Openverse record {API}/{endpoint}/{identity}/; verify source terms before use",
                           "attribution_required": item["license"] == "by",
                           "attribution_text": item.get("attribution") or f"{item.get('title', '')} — {item.get('creator', '')}. {item['license_url']}",
                           "status": "NEEDS_SOURCE_REVIEW"},
                "provenance": {"discovery": "Openverse", "external_id": identity,
                               "creator": item.get("creator"), "license_url": item["license_url"],
                               "source_url": item["foreign_landing_url"]},
            })
            location = {"host_id": media.host_id(), "path": str(target)}
            if location not in asset["locations"]:
                asset["locations"].append(location)
            media.atomic_json(catalog, data)
        return asset
