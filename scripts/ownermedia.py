#!/usr/bin/env python3
"""Owner library media selected for one production's stages.

``scripts/media.py`` is the catalog: what the owner has, what its bytes are,
and what rights record a person supplied for it. This module is the
*selection*: which of those assets a particular production uses, and for
what - the visuals its scenes are built from, or the music, ambience and
sound-effect layers underneath them.

Three rules the rest of the pipeline relies on:

* **A selection is a reference plus evidence.** Every entry records the
  asset's content identity, the rights record the owner supplied, and the
  path the bytes were resolved from. The bytes are staged into the project
  so the production travels as a unit, and the staged copy is re-verified
  against that identity before anything is published.
* **Nothing here grades anything.** Choosing your own footage removes the
  reason a visual is *known* to be a placeholder; it does not make it
  production-grade. That claim stays a person's, recorded elsewhere.
* **Generated media stays the fallback.** A role nobody selected for is
  produced exactly as it was before - same routing, same providers, same
  order.
"""
import json
import os
import shutil
from datetime import datetime, timezone
from pathlib import Path

try:
    import media
    import render
except ImportError:                                   # package-style import
    from . import media, render

ROOT = Path(__file__).resolve().parent.parent

# The stages an owner asset can be assigned to. "visuals" feeds the scene
# images (or scene footage); the three audio roles become layers in the
# audio plan. Deliberately a small closed set: a role is a promise about
# where the asset ends up, and an open-ended one could not be kept.
ROLES = ("visuals", "music", "ambience", "sfx")
# How chosen visuals meet a storyboard: "all" deals them across every shot
# (cycled); "mixed" places each once, spread evenly, and leaves the other
# shots to the generators - your photographs among generated scenes.
VISUAL_MODES = ("all", "mixed")
ROLE_KINDS = {
    "visuals": ("image", "video"),
    "music": ("audio",),
    "ambience": ("audio",),
    "sfx": ("audio",),
}
ROLE_LABELS = {
    "visuals": "Visuals",
    "music": "Music",
    "ambience": "Ambience",
    "sfx": "Sound effects",
}

# Where an owner layer sits in the mix when it replaces generated material.
# The level a replaced generated layer was mixed at wins over these, so a
# soundscape designed with a quiet ambience bed keeps that balance.
ROLE_GAIN_DB = {"music": 0.0, "ambience": -8.0, "sfx": -14.0}

# Which generated layers an owner role stands in for. Selecting your own
# music replaces the synthesised music/pad/tone bed - that is the whole
# point of selecting it - and leaves narration and everything else alone.
REPLACES_PROVIDERS = {
    "music": ("music", "pad", "tone"),
    "ambience": ("ambience", "rain", "noise"),
    "sfx": ("events",),
}

# Seam from one loop of an owner layer to the next, so a track shorter than
# the video does not click every time it repeats.
ROLE_LOOP_CROSSFADE_SECONDS = {"music": 6.0, "ambience": 4.0, "sfx": 0.0}

STAGED_DIRNAME = "owner-media"
SELECTION_VERSION = 1

SUPPORTED_IMAGE_SUFFIXES = tuple(render.SUPPORTED_IMAGE_EXTENSIONS)
SUPPORTED_VIDEO_SUFFIXES = tuple(render.SUPPORTED_VIDEO_EXTENSIONS)
SUPPORTED_AUDIO_SUFFIXES = (".wav", ".flac", ".mp3", ".m4a", ".aac", ".ogg", ".opus")


class OwnerMediaError(ValueError):
    """A selection that cannot be honoured, with the reason a person needs."""


def utc_now():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


# --------------------------------------------------------------------------
# browsing the catalog
# --------------------------------------------------------------------------

def _stream_summary(asset):
    technical = asset.get("technical") or {}
    video = next((s for s in technical.get("streams") or []
                  if s.get("codec_type") == "video"), None)
    audio = next((s for s in technical.get("streams") or []
                  if s.get("codec_type") == "audio"), None)
    duration = technical.get("duration_seconds")
    try:
        duration = float(duration) if duration is not None else None
    except (TypeError, ValueError):
        duration = None
    return {
        "width": (video or {}).get("width"),
        "height": (video or {}).get("height"),
        "video_codec": (video or {}).get("codec_name"),
        "audio_codec": (audio or {}).get("codec_name"),
        "sample_rate": (audio or {}).get("sample_rate"),
        "channels": (audio or {}).get("channels"),
        "duration_seconds": duration,
        "bytes": technical.get("bytes"),
    }


def _rights_summary(asset):
    rights = asset.get("rights")
    if not isinstance(rights, dict):
        return None
    return {
        "source": rights.get("source"),
        "license": rights.get("license"),
        "commercial_use": rights.get("commercial_use"),
        "attribution_required": bool(rights.get("attribution_required")),
        "attribution_text": rights.get("attribution_text"),
        "evidence": rights.get("evidence"),
        "status": rights.get("status"),
    }


def describe(asset):
    """One browsable catalog entry: what it is, and what stands in its way.

    ``selectable`` is the only verdict a caller needs, and ``problems`` says
    why when it is false. Each problem is something a person can act on -
    annotate the asset, supply its rights, bring the source machine online -
    never a bare refusal.
    """
    kind = (asset.get("technical") or {}).get("kind")
    analysis = asset.get("analysis") if isinstance(asset.get("analysis"), dict) else {}
    problems = []
    if not (asset.get("description") or "").strip() or not (asset.get("source") or "").strip():
        problems.append("needs a description of what it actually shows and a "
                        "source record before it can be used")
    try:
        media.rights_record(asset)
    except media.MediaError:
        problems.append("needs a rights record - source, licence, evidence, and "
                        "whether commercial use is allowed")
    path = None
    try:
        path = media.resolve(asset)
    except media.MediaError as exc:
        problems.append(str(exc))
    # The file's own name, as an identifier and nothing more. A person
    # choosing between two undescribed recordings needs to know which file
    # each one is; what it *contains* still comes only from a description
    # somebody wrote after looking at it.
    locations = asset.get("locations") or []
    filename = Path(str(path) if path else
                    (locations[0].get("path") if locations else "")).name
    return {
        "id": asset["id"],
        "kind": kind,
        "filename": filename,
        "origin": asset.get("origin"),
        "analysis": {"available": bool(analysis),
                     "model": analysis.get("model"),
                     "confidence": analysis.get("confidence"),
                     "error": asset.get("analysis_error")},
        "description": asset.get("description") or "",
        "source": asset.get("source") or "",
        "tags": list(asset.get("tags") or []),
        "technical": _stream_summary(asset),
        "rights": _rights_summary(asset),
        "available": path is not None,
        "preview_available": bool((asset.get("preview") or {}).get("path") and Path(asset["preview"]["path"]).is_file()),
        "preview_kind": (asset.get("preview") or {}).get("kind") if path is None else kind,
        "state": ("ready" if path is not None and not problems else
                   "unavailable" if path is None else "blocked"),
        "resolved_path": str(path) if path else None,
        "selectable": not problems,
        "problems": problems,
        "roles": [role for role in ROLES if kind in ROLE_KINDS[role]],
    }


def browse(*, query="", kind=None, catalog=media.DEFAULT_CATALOG):
    """Every catalog asset, described for a person choosing between them."""
    entries = [describe(asset) for asset in
               media.search(query=query, kind=kind, catalog=catalog)]
    # Usable things first, then by kind and description, so the top of the
    # list is what can actually be selected right now.
    if query.strip():
        return entries  # Preserve the domain's relevance ranking.
    return sorted(entries, key=lambda e: (not e["selectable"], e["kind"] or "",
                                          e["description"].casefold(), e["id"]))


def annotate(identity, *, description, source, tags=(), origin="owner",
             rights=None, catalog=media.DEFAULT_CATALOG):
    """Record what an asset shows, where it came from and its rights.

    A pass-through to ``media.annotate`` so the dashboard can supply the
    claims the CLI supplies, by the same single writer. The claims are the
    person's; nothing is inferred from a filename here or there.
    """
    try:
        return media.annotate(identity, description=description, tags=list(tags),
                              origin=origin, source=source, rights=rights,
                              catalog=catalog)
    except media.MediaError as exc:
        raise OwnerMediaError(str(exc)) from exc


# --------------------------------------------------------------------------
# selecting into a production
# --------------------------------------------------------------------------

def _suffix_for(kind, path):
    suffix = path.suffix.lower()
    allowed = {"image": SUPPORTED_IMAGE_SUFFIXES,
               "video": SUPPORTED_VIDEO_SUFFIXES,
               "audio": SUPPORTED_AUDIO_SUFFIXES}[kind]
    if suffix not in allowed:
        raise OwnerMediaError(
            f"{path.name}: the pipeline cannot use a {kind} in '{suffix}' "
            f"format; supported: {', '.join(allowed)}")
    return suffix


def stage(pdir, asset, source_path, role):
    """Put the selected bytes inside the project, by identity-stable name.

    Hardlinked when the filesystem allows it, copied otherwise: the owner's
    original is never moved, renamed or written to. The name carries the
    content identity, so re-staging the same asset is a no-op and a
    different asset can never land on the same path.
    """
    kind = asset["technical"]["kind"]
    suffix = _suffix_for(kind, Path(source_path))
    staged_dir = Path(pdir) / STAGED_DIRNAME
    staged_dir.mkdir(parents=True, exist_ok=True)
    target = staged_dir / f"{role}-{asset['id'][:16]}{suffix}"
    if target.is_file() and media.digest(target) == asset["id"]:
        return target, "present"
    if target.exists():
        target.unlink()
    try:
        os.link(source_path, target)
        return target, "hardlink"
    except OSError:
        shutil.copy2(source_path, target)
        return target, "copy"


def _entry(asset, source_path, staged_path, pdir, role, method):
    rights = media.rights_record(asset)
    return {
        "asset_id": asset["id"],
        "role": role,
        "kind": asset["technical"]["kind"],
        "description": asset.get("description") or "",
        "origin": asset.get("origin"),
        "source": asset.get("source") or "",
        "rights": rights,
        "source_path": str(source_path),
        "staged_path": str(Path(staged_path).relative_to(pdir)),
        "staged_by": method,
        "technical": _stream_summary(asset),
        "selected_utc": utc_now(),
    }


def build_selection(pdir, assignments, *, actor, catalog=media.DEFAULT_CATALOG):
    """Validate an assignment of catalog assets to roles and stage its bytes.

    ``assignments`` is ``{role: [asset_id, ...]}``; a role present with an
    empty list is cleared, a role left out is untouched by the caller. Order
    inside a role is kept - it is the order the visuals are used in.

    Refuses rather than degrades: an asset without a description, without a
    rights record, or whose bytes are not on this host is not silently
    skipped, because a production that quietly used fewer of your shots than
    you chose is worse than one that told you why.
    """
    if not isinstance(assignments, dict):
        raise OwnerMediaError("assignments must be an object of role -> asset ids")
    unknown = sorted(set(assignments) - set(ROLES) - {"visuals_mode"})
    if unknown:
        raise OwnerMediaError(f"unknown role(s): {', '.join(unknown)}; "
                              f"roles are {', '.join(ROLES)}")
    if not (actor or "").strip():
        raise OwnerMediaError("a selection records who made it; actor is required")

    catalog_assets = media.load(catalog)["assets"]
    selection = {}
    if "visuals_mode" in assignments:
        if assignments["visuals_mode"] not in VISUAL_MODES:
            raise OwnerMediaError(f"visuals_mode must be one of {', '.join(VISUAL_MODES)}")
        selection["visuals_mode"] = assignments["visuals_mode"]
    for role in ROLES:
        identities = assignments.get(role)
        if identities is None:
            continue
        if not isinstance(identities, (list, tuple)):
            raise OwnerMediaError(f"{role}: expected a list of asset ids")
        entries, seen = [], set()
        for identity in identities:
            if identity in seen:
                raise OwnerMediaError(
                    f"{role}: asset {identity[:12]} is selected twice")
            seen.add(identity)
            asset = catalog_assets.get(identity)
            if asset is None:
                raise OwnerMediaError(f"unknown asset: {identity}")
            kind = asset["technical"]["kind"]
            if kind not in ROLE_KINDS[role]:
                raise OwnerMediaError(
                    f"{ROLE_LABELS[role]} takes "
                    f"{' or '.join(ROLE_KINDS[role])}, but {identity[:12]} is a {kind}")
            try:
                asset, source_path = media.select(identity, kind=kind, catalog=catalog)
            except media.MediaError as exc:
                raise OwnerMediaError(str(exc)) from exc
            staged_path, method = stage(pdir, asset, source_path, role)
            entries.append(_entry(asset, source_path, staged_path, pdir, role, method))
        selection[role] = entries
    return {
        "version": SELECTION_VERSION,
        "updated_utc": utc_now(),
        "actor": actor,
        "catalog": str(Path(catalog).resolve()),
        **selection,
    }


def merge_selection(existing, update):
    """Previous selection with the roles this update names replaced."""
    merged = {role: list((existing or {}).get(role) or []) for role in ROLES}
    for role in ROLES:
        if role in update:
            merged[role] = list(update[role])
    merged["visuals_mode"] = update.get("visuals_mode") or (existing or {}).get(
        "visuals_mode") or "all"
    return {**{k: v for k, v in update.items() if k not in ROLES}, **merged}


def selection_of(metadata):
    """This production's owner-media selection, normalised and never None."""
    stored = (metadata or {}).get("owner_media") or {}
    return {
        "version": stored.get("version", SELECTION_VERSION),
        "updated_utc": stored.get("updated_utc"),
        "actor": stored.get("actor"),
        "catalog": stored.get("catalog"),
        "visuals_mode": stored.get("visuals_mode") if stored.get("visuals_mode")
        in VISUAL_MODES else "all",
        **{role: list(stored.get(role) or []) for role in ROLES},
    }


def has_any(metadata):
    selection = selection_of(metadata)
    return any(selection[role] for role in ROLES)


def visuals(metadata):
    return selection_of(metadata)["visuals"]


def audio_roles(metadata):
    selection = selection_of(metadata)
    return {role: selection[role] for role in ("music", "ambience", "sfx")
            if selection[role]}


# --------------------------------------------------------------------------
# into the storyboard
# --------------------------------------------------------------------------

def assignment_for_scenes(metadata, scenes):
    """Which owner visual each scene uses: ``{scene_id: entry}``.

    An entry pinned to a ``scene_id`` goes there. The rest are dealt out in
    the order they were selected and then cycled, because a pool smaller
    than the scene count is the normal case - four of your own shots across
    twenty scenes is a deliberate, visible repetition, not an error.
    """
    entries = visuals(metadata)
    if not entries or not scenes:
        return {}
    scene_ids = [scene["scene_id"] for scene in scenes]
    pinned = {entry["scene_id"]: entry for entry in entries
              if entry.get("scene_id") in scene_ids}
    pool = [entry for entry in entries if entry.get("scene_id") not in scene_ids]
    assignment = dict(pinned)
    if not pool:
        return assignment
    open_scenes = [sid for sid in scene_ids if sid not in assignment]
    if selection_of(metadata)["visuals_mode"] == "mixed" and len(pool) < len(open_scenes):
        # Each chosen picture once, spread evenly; the rest are generated.
        step = len(open_scenes) / len(pool)
        for index, entry in enumerate(pool):
            assignment[open_scenes[int(index * step + step / 2)]] = entry
        return assignment
    for index, scene_id in enumerate(open_scenes):
        assignment[scene_id] = pool[index % len(pool)]
    return assignment


def scene_source(entry):
    """What a storyboard scene records about the owner asset behind it."""
    return {
        "kind": "owner_media",
        "asset_id": entry["asset_id"],
        "media_kind": entry["kind"],
        "staged_path": entry["staged_path"],
        "description": entry.get("description") or "",
        "selected_utc": entry.get("selected_utc"),
    }


def is_owner_scene(scene):
    return ((scene.get("source") or {}).get("kind") == "owner_media")


def scene_media_kind(scene):
    """``"image"`` or ``"video"`` for a scene's source, defaulting to image."""
    source = scene.get("source") or {}
    if source.get("kind") == "owner_media":
        return source.get("media_kind") or "image"
    return "image"


# --------------------------------------------------------------------------
# into the audio plan
# --------------------------------------------------------------------------

def _layer_licence(entry):
    rights = entry.get("rights") or {}
    return {
        "source": rights.get("source") or entry.get("source") or "owner library",
        "creator": rights.get("creator"),
        "license": rights.get("license"),
        "commercial_use": bool(rights.get("commercial_use")),
        "status": rights.get("status"),
        "attribution_required": bool(rights.get("attribution_required")),
        "attribution_text": rights.get("attribution_text"),
        "evidence": rights.get("evidence"),
    }


def apply_audio_plan(plan, metadata, pdir):
    """The audio plan with owner tracks standing in for what they replace.

    Returns ``(plan, applied)``. ``applied`` names, per role, the owner
    layers added and the generated layers they displaced, so the dashboard
    can say what changed without re-deriving it from provider names.

    The mix is preserved where it existed: an owner ambience track inherits
    the gain the generated ambience bed was mixed at, and keeps its fades
    and ducking. Narration is never touched.
    """
    roles = audio_roles(metadata)
    if not roles:
        return plan, {}
    plan = json.loads(json.dumps(plan))        # never mutate the caller's plan
    layers = list(plan.get("layers") or [])
    applied = {}
    for role, entries in roles.items():
        replaced = [layer for layer in layers
                    if layer.get("provider") in REPLACES_PROVIDERS[role]]
        template = replaced[0] if replaced else {}
        layers = [layer for layer in layers if layer not in replaced]
        added = []
        for index, entry in enumerate(entries):
            staged = Path(pdir) / entry["staged_path"]
            layer = {
                "id": f"owner-{role}" + (f"-{index + 1}" if len(entries) > 1 else ""),
                "provider": "file",
                "gain_db": template.get("gain_db", ROLE_GAIN_DB[role]),
                "params": {
                    "path": str(staged),
                    "license": _layer_licence(entry),
                    # A track shorter than the video is looped, and the seam
                    # is crossfaded so the repeat is not a click.
                    "crossfade_loop_seconds": ROLE_LOOP_CROSSFADE_SECONDS[role],
                },
                "owner_asset_id": entry["asset_id"],
            }
            for carried in ("fade_in_seconds", "fade_out_seconds", "start_seconds",
                            "highpass_hz", "lowpass_hz", "width"):
                if carried in template:
                    layer[carried] = template[carried]
            layers.append(layer)
            added.append(layer["id"])
        # A layer that ducked under the bed this replaces has to duck under
        # the new one, or the plan would name a layer that no longer exists.
        displaced_ids = {layer.get("id") for layer in replaced if layer.get("id")}
        for layer in layers:
            if layer.get("duck_under") in displaced_ids:
                layer["duck_under"] = added[0] if added else None
                if layer["duck_under"] is None:
                    layer.pop("duck_under")
        applied[role] = {
            "added": added,
            "replaced": [layer.get("id") or layer.get("provider") for layer in replaced],
            "asset_ids": [entry["asset_id"] for entry in entries],
        }
    plan["layers"] = layers
    return plan, applied


# --------------------------------------------------------------------------
# the gate's view
# --------------------------------------------------------------------------

def verification_problems(metadata, pdir):
    """Owner media that is no longer what was selected.

    The artefact decides, not the record: the staged bytes are re-hashed
    against the content identity the selection names. A replaced file, a
    truncated copy or a missing stage is a blocker, because the reviewer
    would otherwise approve something other than what they chose.
    """
    problems = []
    pdir = Path(pdir)
    selection = selection_of(metadata)
    for role in ROLES:
        for entry in selection[role]:
            staged = pdir / entry.get("staged_path", "")
            label = f"{ROLE_LABELS[role]} asset {entry.get('asset_id', '?')[:12]}"
            if not staged.is_file():
                problems.append(f"{label} is selected but its staged file is "
                                f"missing: {entry.get('staged_path')}")
                continue
            try:
                if media.digest(staged) != entry.get("asset_id"):
                    problems.append(
                        f"{label} no longer matches the bytes that were "
                        f"selected: {entry.get('staged_path')}")
            except OSError as exc:
                problems.append(f"{label} could not be verified: {exc}")
            try:
                media.rights_record(entry)
            except media.MediaError as exc:
                problems.append(str(exc))
    return problems


def summary(metadata):
    """Compact per-role view for a dashboard, derived from the selection."""
    selection = selection_of(metadata)
    return {
        "updated_utc": selection["updated_utc"],
        "actor": selection["actor"],
        "visuals_mode": selection["visuals_mode"],
        "roles": {
            role: {
                "label": ROLE_LABELS[role],
                "count": len(selection[role]),
                "entries": selection[role],
            }
            for role in ROLES
        },
    }
