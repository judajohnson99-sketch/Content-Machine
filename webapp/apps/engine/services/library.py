"""Thin adapter for the owner's media library (architecture plan §1).

No business logic: scripts.media owns the catalog, scripts.ownermedia owns
what "selectable" means and scripts.project owns writing a selection onto a
production. This module only calls them.
"""
from pathlib import Path

import scripts.media as media
import scripts.ownermedia as ownermedia
import scripts.project as project
import scripts.media_sync as media_sync
import scripts.discovery as discovery
import scripts.media_intelligence as media_intelligence


def browse(query="", kind=None):
    return ownermedia.browse(query=query, kind=kind)


def describe(identity):
    """One catalog entry, or None if the catalog has no such asset."""
    asset = media.load()["assets"].get(identity)
    return ownermedia.describe(asset) if asset else None


def annotate(identity, *, description, source, tags=(), origin="owner", rights=None):
    return ownermedia.annotate(identity, description=description, source=source,
                               tags=tags, origin=origin, rights=rights)


def scan(path):
    """Inspect a folder or file of the owner's originals into the catalog."""
    return media.scan(path)


def resolve_file(identity):
    """The local path of an asset's bytes, for previewing it in the browser.

    scripts.media.resolve() is the rule: it serves an intact source whose
    bytes still hash to the asset's identity, and refuses anything missing,
    changed or on another host.
    """
    asset = media.load()["assets"].get(identity)
    if asset is None:
        raise media.MediaError(f"unknown asset: {identity}")
    try:
        return Path(media.resolve(asset))
    except media.MediaError:
        preview = asset.get("preview") or {}
        if isinstance(preview, dict) and preview.get("path"):
            path = Path(preview["path"]).resolve()
            directory = (media.DEFAULT_CATALOG.parent / "previews").resolve()
            if (path.is_relative_to(directory) and path.is_file()
                    and path.stem == identity and media.digest(path) == preview.get("sha256")):
                return path
        raise


def connections():
    return {"sources": media_sync.sources(), "transfers": media_sync.transfers(),
            "visual_analysis_available": media_intelligence.available(),
            "audio_analysis_available": media_intelligence.audio_available()}


def analyze(identity):
    return media_intelligence.analyze(identity)


def retrieve(identity, mode="source"):
    return media_sync.request_transfer(identity, mode)


def discover(query, kind="image"):
    return discovery.search(query, kind)


def import_discovered(identity, kind="image"):
    return ownermedia.describe(discovery.import_asset(identity, kind))


def selection(video_id):
    return project.owner_media_view(video_id)


def suggest(video_id, role):
    try:
        return project.suggest_owner_media(video_id, role)
    except project.ProjectError as exc:
        raise media.MediaError(str(exc)) from exc


def select(video_id, assignments, actor):
    return project.set_owner_media(video_id, assignments, actor)
