#!/usr/bin/env python3
"""Unified visual asset discovery for one scene: find, rank, choose, reuse.

For a storyboard scene this gathers every way of getting its picture or
footage into one list of *candidates*:

* ``library``   - media already in the owner's catalog (searched first)
* ``stock``     - Pexels / Pixabay / Unsplash through their official APIs
* ``generated`` - a new picture from the owner's GPU (planned, never run here)
* ``cloud``     - a metered generation route, shown only as "needs your OK"

ranks them together (``scripts/asset_ranking.py``), applies the approval
policy (``scripts/asset_licensing.py``) and, when a person chooses one,
turns it into what the rest of the pipeline already understands: a catalog
asset pinned to that scene through ``scripts/ownermedia.py``. From there the
renderer, Review Center, Kdenlive export and package see it exactly as they
see the owner's own media - there is no parallel asset path.

Rules held here:

* **The library is searched before anything is fetched or generated.** A
  strong, approved library match skips the stock search entirely unless the
  caller asks for more, so quota and GPU time are not spent on a picture
  already owned.
* **Discovery never spends and never downloads.** It reads, ranks and
  records. Bytes arrive only on ``select`` of a stock candidate, with a
  person's approval; generation starts only when a person chooses it, and
  when the GPU is off that choice is *deferred*, not failed.
* **Rights come from the stored candidate, not from the caller.** The record
  the person approved is the one saved at discovery, so a client cannot
  supply its own licence.
* **Nothing here grades.** No code path touches ``production_grade``.
"""
import argparse
import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import asset_licensing as licensing  # noqa: E402
import asset_ranking as ranking  # noqa: E402
import media  # noqa: E402
import ownermedia  # noqa: E402
import stock_providers as stock  # noqa: E402
import storyboard as storyboard_mod  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent

STORE_VERSION = 1
STORE_FILENAME = "visual_assets.json"
VISUAL_KINDS = ("image", "video")

# The verified native size for a 16:9 plan on the GTX 1060 3GB: generated
# once at this size and finished by the normal upscale, never at 1920x1080.
GEN_WIDTH, GEN_HEIGHT = 768, 432

MIN_LIBRARY_WIDTH = 640
MIN_STOCK_WIDTH = 1280
STRONG_LIBRARY_SCORE = 0.62
MAX_LIBRARY_CANDIDATES = 10
PIXEL_MEASURE_LIMIT = 6
NEAR_DUPLICATE_DISTANCE = 4
MAX_QUERIES = 3
MIN_STOCK_RESULTS = 6


class VisualAssetError(ValueError):
    """A request that cannot be honoured, with the reason a person needs."""


def utc_now():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _project():
    import project
    return project


# --------------------------------------------------------------------------
# the per-project record
# --------------------------------------------------------------------------

def store_path(video_id):
    return _project().project_dir(video_id) / STORE_FILENAME


def load_store(video_id):
    path = store_path(video_id)
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError):
        data = {}
    if not isinstance(data, dict) or data.get("version") != STORE_VERSION:
        data = {"version": STORE_VERSION, "scenes": {}}
    data.setdefault("scenes", {})
    return data


def save_store(video_id, store):
    path = store_path(video_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    pending = path.with_suffix(".pending")
    pending.write_text(json.dumps(store, indent=2) + "\n")
    os.replace(pending, path)


def _load_project(video_id):
    project = _project()
    pdir = project.project_dir(video_id)
    meta_path = pdir / "metadata.json"
    if not meta_path.is_file():
        raise VisualAssetError(f"no such project: {video_id}")
    metadata = json.loads(meta_path.read_text())
    board = storyboard_mod.load(video_id)
    if not board or not board.get("scenes"):
        raise VisualAssetError(
            f"{video_id} has no storyboard yet; build one first (./content-machine storyboard {video_id})")
    return pdir, metadata, board


def _scene(board, scene_id):
    for scene in board["scenes"]:
        if scene["scene_id"] == scene_id:
            return scene
    raise VisualAssetError(f"no such scene in this storyboard: {scene_id}")


# --------------------------------------------------------------------------
# what a scene needs
# --------------------------------------------------------------------------

def _frame_size(pdir):
    try:
        spec = json.loads((pdir / "video_spec.json").read_text())
        return int(spec.get("width") or 1920), int(spec.get("height") or 1080)
    except (OSError, ValueError, TypeError):
        return 1920, 1080


def scene_requirement(pdir, scene, metadata, *, query=None, kind=None, used_assets=()):
    """The scene as search words and constraints.

    Search words come from what the storyboard already says the scene shows
    (its visual intent, then its compiled prompt with generator boilerplate
    removed) - no extra model call, and the same plan that would drive a
    generation drives the stock search.
    """
    direction = (metadata.get("visual_plan") or {}).get("direction") or {}
    identity = direction.get("identity") or {}
    palette = list(identity.get("palette") or [])
    mood = [identity.get("atmosphere"), identity.get("lighting"),
            *(identity.get("materials") or [])]
    intent = scene.get("visual_intent") or ""
    prompt_words = ranking.search_terms(scene.get("image_prompt"), 10)
    primary = ranking.search_terms(intent, 6) or prompt_words[:6]
    width, height = _frame_size(pdir)
    queries = []
    for text in (query, " ".join(primary), " ".join(prompt_words[:4]),
                 " ".join(primary[:2])):
        text = (text or "").strip()
        if text and text not in queries:
            queries.append(text)
    queries = queries[:MAX_QUERIES]
    return {
        "scene_id": scene["scene_id"],
        "query": queries[0] if queries else "",
        "queries": queries,
        "terms": prompt_words,
        "subjects": ranking.search_terms(intent, 4) or prompt_words[:3],
        "direction_terms": [t for t in [*palette, *mood] if t],
        "avoid": list(direction.get("avoid") or []),
        "palette": palette,
        "wants": kind if kind in VISUAL_KINDS else "either",
        "duration_seconds": scene.get("duration_seconds"),
        "target_width": width,
        "target_height": height,
        "min_width": MIN_STOCK_WIDTH,
        "used_assets": sorted(used_assets),
    }


def _used_elsewhere(metadata, scene_id):
    return {e["asset_id"] for e in ownermedia.visuals(metadata)
            if e.get("scene_id") != scene_id}


# --------------------------------------------------------------------------
# candidates: library
# --------------------------------------------------------------------------

def _semantic_scores(assets, query):
    """CLIP similarity per asset id, when the optional model is installed.
    Advisory input only; absent models cost nothing."""
    try:
        import math
        import media_intelligence
        if not media_intelligence.available():
            return {}
        vector = media_intelligence.text_vector(query)
        scores = {}
        for asset in assets:
            analysis = asset.get("analysis") if isinstance(asset.get("analysis"), dict) else {}
            embedding = analysis.get("embedding")
            if (analysis.get("model") == media_intelligence.MODEL_ID
                    and analysis.get("source_sha256") == asset["id"]
                    and isinstance(embedding, list) and len(embedding) == len(vector)
                    and all(isinstance(v, (int, float)) and math.isfinite(v) for v in embedding)):
                scores[asset["id"]] = sum(a * b for a, b in zip(vector, embedding))
        return scores
    except Exception:                              # noqa: BLE001 - optional inference
        return {}


def library_candidate(asset, *, semantic=None):
    """One catalog asset as a candidate, with what stands in its way."""
    entry = ownermedia.describe(asset)
    rights = dict(asset.get("rights") or {})
    kind = entry["kind"]
    technical = entry["technical"]
    provenance = asset.get("provenance") or {}
    try:
        media.rights_record(asset)
        rights_ok, rights_problem = True, None
    except media.MediaError as exc:
        rights_ok, rights_problem = False, str(exc)
    approvable = (not rights_ok and licensing.is_external(rights)
                  and rights.get("eligibility") != licensing.INELIGIBLE
                  and (rights.get("provider_terms") or {}).get("commercial_use") is True)
    description = asset.get("description") or ""
    candidate = {
        "candidate_id": f"library:{asset['id']}",
        "source": "library",
        "provider": "library",
        "external_id": asset["id"],
        "media_kind": kind,
        "title": description.split(".")[0][:90],
        "description": description,
        "tags": list(asset.get("tags") or []),
        "width": technical.get("width"),
        "height": technical.get("height"),
        "orientation": stock._orientation(technical.get("width"), technical.get("height")),
        "duration_seconds": technical.get("duration_seconds"),
        "creator": {"name": rights.get("creator"), "url": rights.get("creator_url")},
        "source_url": provenance.get("source_url") or asset.get("source"),
        "retrieved_utc": rights.get("retrieved_utc"),
        "preview": {"kind": "library", "asset_id": asset["id"]},
        "full": {"asset_id": asset["id"], "width": technical.get("width"),
                 "height": technical.get("height")},
        "rights": rights,
        "library": {
            "asset_id": asset["id"],
            "origin": asset.get("origin"),
            "rights_ok": rights_ok,
            "rights_problem": rights_problem,
            "approvable": approvable,
            "usage": list(asset.get("usage") or []),
            "problems": entry["problems"],
        },
        "cost": {"money_usd": 0.0, "resource": "none"},
        "state": "available" if entry["available"] else "unavailable",
        "_path": entry["resolved_path"],
    }
    if semantic is not None:
        candidate["semantic_rank"] = {"method": "CLIP", "score": max(0.0, min(1.0, semantic / 0.35))}
    return candidate


def library_candidates(req, *, catalog, limit=MAX_LIBRARY_CANDIDATES,
                       min_width=MIN_LIBRARY_WIDTH, aspect=None):
    """Catalog media worth considering for this scene.

    Filters by kind, resolution, aspect range (``(low, high)`` width/height)
    and relevance. Rights are *not* a filter: an asset missing its rights
    record is shown with what it needs, because hiding it would make
    "I already own that" look like "I do not".
    """
    assets = [a for a in media.load(catalog)["assets"].values()
              if a["technical"]["kind"] in VISUAL_KINDS
              and req["wants"] in ("either", a["technical"]["kind"])]
    words = set(ranking.tokens(" ".join([req["query"], *req["terms"]])))
    semantic = _semantic_scores(assets, req["query"]) if assets else {}
    picked = []
    for asset in assets:
        streams = ownermedia._stream_summary(asset)
        width, height = streams.get("width"), streams.get("height")
        if width and width < min_width:
            continue
        if aspect and width and height and not (aspect[0] <= width / height <= aspect[1]):
            continue
        text = set(ranking.tokens(" ".join([asset.get("description") or "", *asset.get("tags", []),
                                            str((asset.get("provenance") or {}).get("prompt", ""))])))
        overlap = len(words & text) / len(words) if words else 0.0
        sim = semantic.get(asset["id"])
        if overlap == 0 and (sim is None or sim < 0.18):
            continue
        picked.append((max(overlap, (sim or 0) / 0.35), asset, sim))
    picked.sort(key=lambda item: (-item[0], item[1]["id"]))
    return [library_candidate(asset, semantic=sim) for _, asset, sim in picked[:limit]]


def measure_pixels(candidates, req):
    """Objective evidence from the file, where there is one to read.

    Uses the existing image QC (flat fills and black frames are objective
    defects) and, when the production has a palette, the perceptual
    system's palette conformance. Footage is not sampled here. Near-
    identical pictures are collapsed onto the first, which keeps its
    siblings' ids so the person can see they exist.
    """
    import qc
    try:
        import quality
    except ImportError:                            # pragma: no cover
        quality = None
    resolved = []
    if quality and req.get("palette"):
        resolved, _ = quality.resolve_palette(req["palette"])
    measured, kept = 0, []
    for candidate in candidates:
        path = candidate.get("_path")
        if (candidate["source"] != "library" or candidate["media_kind"] != "image"
                or not path or measured >= PIXEL_MEASURE_LIMIT):
            kept.append(candidate)
            continue
        measured += 1
        assessment = qc.assess_image(path)
        findings = [f["detail"] for f in assessment["findings"] if f["severity"] in ("block", "warn")]
        pixels = {"verdict": assessment["verdict"], "detail": "; ".join(findings)[:240],
                  "hash": (assessment.get("measurement") or {}).get("hash")}
        if resolved:
            sample = quality.measure_image(path)
            if sample:
                palette = quality.palette_conformance(sample["pixels"], resolved)
                if palette.get("assessable"):
                    pixels["palette_coverage"] = palette["coverage"]
        candidate["pixels"] = pixels
        twin = next((k for k in kept if _near(qc, k, pixels)), None)
        if twin:
            twin.setdefault("near_duplicates", []).append(candidate["library"]["asset_id"])
            continue
        kept.append(candidate)
    return kept


def _near(qc, kept, pixels):
    distance = qc.hash_distance((kept.get("pixels") or {}).get("hash"), pixels.get("hash"))
    return distance is not None and distance <= NEAR_DUPLICATE_DISTANCE


# --------------------------------------------------------------------------
# candidates: stock
# --------------------------------------------------------------------------

def _library_index(catalog):
    """``{(provider, external_id): asset_id}`` for stock already downloaded."""
    index = {}
    for asset in media.load(catalog)["assets"].values():
        provenance = asset.get("provenance") or {}
        for record in [provenance, *provenance.get("external_sources", [])]:
            if record.get("provider") and record.get("external_id"):
                index[(record["provider"], str(record["external_id"]))] = asset["id"]
    return index


def stock_candidates(req, hub, *, catalog, pages=None, per_page=stock.DEFAULT_PER_PAGE):
    """Stock results for the scene, with provider health, and the library
    assets they duplicate swapped for the copy already owned."""
    kinds = ["image", "video"] if req["wants"] == "either" else [req["wants"]]
    found, reports, next_pages = [], [], {}
    for query in req["queries"] or [req["query"]]:
        if not query:
            continue
        for kind in kinds:
            result = hub.search(query, kind=kind, orientation="landscape",
                                min_width=req["min_width"], pages=pages, per_page=per_page)
            found.extend(result["candidates"])
            next_pages.update(result["next_pages"])
            reports.extend(result["providers"])
        if len(found) >= MIN_STOCK_RESULTS:
            break
    found = stock.dedupe([c for c in found
                          if not c.get("width") or c["width"] >= req["min_width"]])
    index = _library_index(catalog)
    fresh, owned = [], []
    for candidate in found:
        asset_id = index.get((candidate["provider"], candidate["external_id"]))
        if asset_id:
            owned.append(asset_id)
        else:
            fresh.append(candidate)
    return {"candidates": fresh, "owned": owned,
            "providers": _provider_summary(reports), "next_pages": next_pages}


def _provider_summary(reports):
    """One line per provider: its count summed over every search made, and the
    first problem it reported (a provider that failed once is shown failed)."""
    by_provider = {}
    for entry in reports:
        current = by_provider.get(entry["provider"])
        if current is None:
            by_provider[entry["provider"]] = dict(entry)
            continue
        current["count"] += entry["count"]
        if current["state"] in ("ready", "unsupported") and entry["state"] not in ("ready", "unsupported"):
            current.update(state=entry["state"], detail=entry["detail"])
    return list(by_provider.values())


# --------------------------------------------------------------------------
# candidates: generated and cloud
# --------------------------------------------------------------------------

def gpu_state(request=None):
    """Whether a generation could start now, and where it would run.

    Cheap and read-only. 'deferred' is not an error: it means the work would
    be queued (a worker is enrolled but off) or has nowhere to go yet, and
    discovery carries on without it.
    """
    detail = []
    try:
        import generation
        router = generation.Router(order=("comfyui",))
        probe = request or generation.GenerationRequest(prompt="", require_depicted=True)
        for entry in router.status(probe):
            if entry["configured"] and entry["healthy"] and not entry["in_cooldown"]:
                return {"state": "ready", "where": "local", "detail": "ComfyUI is answering"}
            if entry["configured"]:
                detail.append(f"local ComfyUI: {entry['detail']}")
    except Exception as exc:                       # noqa: BLE001
        detail.append(f"local ComfyUI could not be checked: {exc}")
    try:
        import worker
        readiness = worker.depicted_readiness()
        if readiness["state"] in (worker.GPU_READY, worker.GPU_BUSY):
            return {"state": "ready", "where": "remote", "detail": readiness["detail"]}
        detail.append(readiness["detail"])
    except Exception as exc:                       # noqa: BLE001
        detail.append(f"GPU worker could not be checked: {exc}")
    return {"state": "deferred", "where": None,
            "detail": "; ".join(detail) or "No GPU is available."}


def paid_routes():
    """Metered image providers that are configured. Informational only."""
    try:
        import generation
        providers = generation.build_providers()
        return [name for name, p in providers.items()
                if getattr(p, "costs_money", False) and p.configured()]
    except Exception:                              # noqa: BLE001
        return []


def generation_candidate(req, scene, gpu):
    import generation
    request = storyboard_mod.scene_request(scene, require_depicted=True)
    prompt = request.prompt
    width, height = request.width, request.height
    if abs(req["target_width"] / req["target_height"] - 16 / 9) < 0.02:
        width, height = GEN_WIDTH, GEN_HEIGHT
    planned = generation.GenerationRequest(
        prompt=prompt, negative_prompt=request.negative_prompt, width=width,
        height=height, count=1, seed=request.seed, model=request.model,
        style=request.style, require_depicted=True)
    deferred = gpu["state"] != "ready"
    return {
        "candidate_id": f"generated:{scene['scene_id']}:{planned.digest()[:12]}",
        "source": "generated",
        "provider": "comfyui",
        "external_id": planned.digest()[:12],
        "media_kind": "image",
        "title": (scene.get("visual_intent") or "New picture for this scene")[:90],
        "description": scene.get("visual_intent") or "",
        "tags": [],
        "width": width,
        "height": height,
        "orientation": stock._orientation(width, height),
        "duration_seconds": None,
        "creator": {"name": None, "url": None},
        "source_url": None,
        "preview": None,
        "full": {"width": req["target_width"], "height": req["target_height"]},
        "rights": {"source": "Owner-generated locally", "license": "Owner-created",
                   "commercial_use": True, "attribution_required": False},
        "generation": {
            "prompt": planned.prompt,
            "negative_prompt": planned.negative_prompt,
            "seed": planned.seed,
            "model": planned.model,
            "style": planned.style,
            "width": width,
            "height": height,
            "request_digest": planned.digest(),
            # Reserved: reference-guided / image-to-image generation. The
            # ComfyUI workflow templates do not take a reference yet, so the
            # field exists only so a later workflow can fill it without a
            # schema change.
            "reference": None,
        },
        "cost": {"money_usd": 0.0, "resource": "gpu"},
        "state": "deferred" if deferred else "available",
        "state_detail": ("Waiting for the GPU: " + gpu["detail"]) if deferred
        else f"Ready to generate ({gpu['where']})",
    }


def cloud_candidate(scene, providers):
    return {
        "candidate_id": f"cloud:{scene['scene_id']}",
        "source": "cloud", "provider": ", ".join(providers), "external_id": scene["scene_id"],
        "media_kind": "image", "title": "Cloud-generated picture", "description": "",
        "tags": [], "width": None, "height": None, "duration_seconds": None,
        "creator": {"name": None, "url": None}, "source_url": None, "preview": None,
        "full": {}, "rights": {}, "cost": {"money_usd": None, "metered": True, "resource": "money"},
        "state": "authorization_required",
        "state_detail": "Costs money per image. Off until you authorise it.",
    }


# --------------------------------------------------------------------------
# discovery
# --------------------------------------------------------------------------

def _public(candidate):
    return {k: v for k, v in candidate.items() if not k.startswith("_")}


def discover(video_id, scene_id, *, query=None, kind=None, sources=None,
             more=False, pages=None, catalog=media.DEFAULT_CATALOG, hub=None):
    """Find, rank and record the options for one scene.

    ``sources`` limits the search to some of ``library``, ``stock``,
    ``generated`` (default all). ``more`` searches stock even when the
    library already holds a strong match. ``pages`` continues a previous
    stock search. Nothing is downloaded, generated or spent.
    """
    pdir, metadata, board = _load_project(video_id)
    scene = _scene(board, scene_id)
    sources = tuple(sources or ("library", "stock", "generated"))
    unknown = set(sources) - {"library", "stock", "generated"}
    if unknown:
        raise VisualAssetError(f"unknown source(s): {', '.join(sorted(unknown))}")
    req = scene_requirement(pdir, scene, metadata, query=query, kind=kind,
                            used_assets=_used_elsewhere(metadata, scene_id))
    autonomy = licensing.autonomy_level()
    level = autonomy["level"]
    providers = []
    candidates = []
    next_pages = {}

    if "library" in sources:
        candidates = measure_pixels(
            library_candidates(req, catalog=catalog), req)

    strong = False
    if candidates:
        first = ranking.rank(candidates, req)
        for record in first["ranked"]:
            candidate = next(c for c in candidates if c["candidate_id"] == record["candidate_id"])
            policy = licensing.decide_use(candidate, level=level)
            if (record["score"] >= STRONG_LIBRARY_SCORE and record["eligible"]
                    and policy["action"] == "auto" and candidate["state"] == "available"):
                strong = True
                break

    if "stock" in sources:
        hub = hub or stock.StockHub()
        if strong and not more and not pages:
            providers = [dict(p, state="skipped",
                              detail="A strong match is already in your library; search more to look anyway.")
                         for p in hub.status()]
        elif not req["query"]:
            providers = [dict(p, state="skipped", detail="This scene gave no words to search with.")
                         for p in hub.status()]
        else:
            result = stock_candidates(req, hub, catalog=catalog, pages=pages)
            providers = result["providers"]
            next_pages = result["next_pages"]
            candidates.extend(result["candidates"])
            by_id = {c["library"]["asset_id"]: c for c in candidates if c["source"] == "library"}
            catalog_assets = media.load(catalog)["assets"]
            for asset_id in result["owned"]:
                if asset_id not in by_id and asset_id in catalog_assets:
                    owned = library_candidate(catalog_assets[asset_id])
                    owned["also_offered_by"] = "stock"
                    candidates.append(owned)

    gpu = gpu_state()
    if "generated" in sources:
        candidates.append(generation_candidate(req, scene, gpu))
        paid = paid_routes()
        if paid:
            candidates.append(cloud_candidate(scene, paid))

    result = ranking.rank(candidates, req)
    by_id = {c["candidate_id"]: c for c in candidates}
    view = []
    for record in result["ranked"]:
        candidate = by_id[record["candidate_id"]]
        view.append({**_public(candidate), "ranking": record,
                     "policy": licensing.decide_use(candidate, level=level)})
    entry = {
        "scene_id": scene_id,
        "discovered_utc": utc_now(),
        "requirement": {k: req[k] for k in ("query", "queries", "subjects", "wants",
                                            "target_width", "target_height",
                                            "duration_seconds")},
        "candidates": view,
        "recommended": result["recommended"],
        "best": result["best"],
        "summary": result["summary"],
        "providers": providers,
        "next_pages": next_pages,
        "gpu": gpu,
        "autonomy": {"level": level, "note": autonomy["note"]},
        "library_hit": strong,
    }
    store = load_store(video_id)
    previous = store["scenes"].get(scene_id) or {}
    store["scenes"][scene_id] = {**entry, "decision": previous.get("decision")}
    save_store(video_id, store)
    return entry


def status(video_id=None):
    """Provider health, GPU readiness and the autonomy level: what the
    workspace shows as 'what is available right now'."""
    hub = stock.StockHub()
    autonomy = licensing.autonomy_level()
    return {"providers": hub.status(), "gpu": gpu_state(),
            "paid_routes": paid_routes(),
            "autonomy": {"level": autonomy["level"], "note": autonomy["note"],
                         "summary": autonomy["policy"]["summary"]}}


def scene_view(video_id, scene_id):
    """The last discovery for a scene, or None."""
    return load_store(video_id)["scenes"].get(scene_id)


# --------------------------------------------------------------------------
# choosing
# --------------------------------------------------------------------------

def _stored_candidate(video_id, scene_id, candidate_id):
    entry = load_store(video_id)["scenes"].get(scene_id)
    if not entry:
        raise VisualAssetError(
            f"search for scene {scene_id} first; there are no stored candidates to choose from")
    for candidate in entry["candidates"]:
        if candidate["candidate_id"] == candidate_id:
            return candidate, entry
    raise VisualAssetError(f"{candidate_id} is not among the candidates found for {scene_id}")


def _lineage(candidate, entry, actor, approval=None):
    record = candidate.get("ranking") or {}
    lineage = {
        "candidate_id": candidate["candidate_id"],
        "source": candidate["source"],
        "provider": candidate.get("provider"),
        "rank": record.get("rank"),
        "score": record.get("score"),
        "confidence": record.get("confidence"),
        "explanation": record.get("summary"),
        "factors": {f["id"]: f["score"] for f in record.get("factors", []) if f.get("applied")},
        "limitations": record.get("limitations", []),
        "alternatives": [a["candidate_id"] for a in record.get("alternatives", [])],
        "query": (entry.get("requirement") or {}).get("query"),
        "discovered_utc": entry.get("discovered_utc"),
        "chosen_by": actor,
        "chosen_utc": utc_now(),
        "policy": {"level": (entry.get("autonomy") or {}).get("level"),
                   "action": (candidate.get("policy") or {}).get("action")},
    }
    if approval:
        lineage["approval"] = approval
    return lineage


def _stock_provenance(candidate, entry):
    rights = candidate["rights"]
    return {
        "discovery": rights.get("source"),
        "provider": candidate["provider"],
        "external_id": candidate["external_id"],
        "candidate_id": candidate["candidate_id"],
        "source_url": candidate.get("source_url"),
        "creator": (candidate.get("creator") or {}).get("name"),
        "creator_url": (candidate.get("creator") or {}).get("url"),
        "license": rights.get("license"),
        "license_url": rights.get("license_url"),
        "retrieved_utc": rights.get("retrieved_utc"),
        "downloaded_utc": utc_now(),
        "search_query": (entry.get("requirement") or {}).get("query"),
    }


def ingest_stock(candidate, entry, *, actor, approve, note="", catalog, hub, work_dir):
    """Download an approved stock candidate and catalog it with its evidence.

    Returns ``(asset, created, near_duplicates)``. Raises ``VisualAssetError``
    when approval is missing or the provider cannot deliver; nothing is
    written to the catalog in either case.
    """
    rights = dict(candidate["rights"])
    if approve:
        try:
            rights = licensing.approve(rights, actor=actor, note=note)
        except licensing.ApprovalError as exc:
            raise VisualAssetError(str(exc)) from exc
    try:
        path = hub.download(candidate, work_dir)
    except stock.StockError as exc:
        raise VisualAssetError(f"could not download from {candidate['provider']}: {exc}") from exc
    description = (candidate.get("description") or candidate.get("title") or "").strip()
    tags = ["stock", candidate["provider"], *candidate.get("tags", [])]
    if not description:
        description = (f"{candidate['provider'].title()} {candidate['media_kind']}: "
                       f"{', '.join(candidate.get('tags', [])[:6]) or 'untitled'}")
    try:
        asset, created = media.register_external(
            path, description=description, tags=tags,
            source=candidate.get("source_url") or rights.get("source") or candidate["provider"],
            rights=rights, provenance=_stock_provenance(candidate, entry), catalog=catalog)
    except media.MediaError as exc:
        Path(path).unlink(missing_ok=True)
        raise VisualAssetError(str(exc)) from exc
    near = _near_duplicates(asset, catalog)
    return asset, created, near


def _near_duplicates(asset, catalog):
    """Other catalog images that look like this one. Advisory: the person
    sees them; nothing is blocked on a lookalike."""
    if asset["technical"]["kind"] != "image":
        return []
    try:
        import qc
        path = media.resolve(asset)
        mine = qc.image_hash(path)
        if not mine:
            return []
        out = []
        for other in media.load(catalog)["assets"].values():
            if other["id"] == asset["id"] or other["technical"]["kind"] != "image":
                continue
            try:
                theirs = qc.image_hash(media.resolve(other))
            except media.MediaError:
                continue
            distance = qc.hash_distance(mine, theirs)
            if distance is not None and distance <= NEAR_DUPLICATE_DISTANCE:
                out.append(other["id"])
        return out
    except Exception:                              # noqa: BLE001 - advisory only
        return []


def _pin(video_id, scene_id, asset_id, lineage, actor, catalog):
    """Pin one catalog asset to one scene, keeping every other pick."""
    project = _project()
    pdir = project.project_dir(video_id)
    metadata = json.loads((pdir / "metadata.json").read_text())
    items = []
    for entry in ownermedia.visuals(metadata):
        if entry.get("scene_id") == scene_id:
            try:
                media.remove_usage(entry["asset_id"], video_id, scene_id, catalog=catalog)
            except media.MediaError:
                pass
            continue
        items.append({"asset_id": entry["asset_id"], "scene_id": entry.get("scene_id"),
                      "lineage": entry.get("lineage")} if entry.get("scene_id")
                     else entry["asset_id"])
    items.append({"asset_id": asset_id, "scene_id": scene_id, "lineage": lineage})
    try:
        result = project.set_owner_media(video_id, {"visuals": items}, actor, catalog=catalog)
    except project.ProjectBusyError:
        raise                       # a busy project is the caller's 409, not a refusal
    except project.ProjectError as exc:
        raise VisualAssetError(str(exc)) from exc
    media.record_usage(asset_id, {"video_id": video_id, "scene_id": scene_id,
                                  "utc": utc_now(), "actor": actor,
                                  "candidate_id": lineage.get("candidate_id")},
                       catalog=catalog)
    return result


def _decision(video_id, scene_id, decision):
    store = load_store(video_id)
    store["scenes"].setdefault(scene_id, {})["decision"] = decision
    save_store(video_id, store)


def select(video_id, scene_id, candidate_id, actor, *, approve=False, note="",
           catalog=media.DEFAULT_CATALOG, hub=None, work_dir=None):
    """Choose a candidate for a scene.

    Returns a result whose ``status`` is one of:

    * ``selected``            - pinned to the scene; ``scenes`` is now stale
    * ``approval_required``   - a person must approve first use (nothing downloaded)
    * ``generation_requested``- the scene will be (re)generated; may be ``deferred``
    * ``blocked``             - cannot be used, with ``reasons``
    """
    if not (actor or "").strip():
        raise VisualAssetError("a choice records who made it; actor is required")
    candidate, entry = _stored_candidate(video_id, scene_id, candidate_id)
    level = (entry.get("autonomy") or {}).get("level") or licensing.DEFAULT_LEVEL
    policy = licensing.decide_use(candidate, level=level)
    base = {"scene_id": scene_id, "candidate_id": candidate_id, "policy": policy}

    if policy["action"] == "blocked":
        return {**base, "status": "blocked", "reasons": policy["reasons"]}

    if candidate["source"] == "cloud":
        return {**base, "status": "blocked", "reasons": policy["reasons"]}

    if candidate["source"] == "generated":
        return _request_generation(video_id, scene_id, candidate, entry, actor, base)

    if policy["action"] == "approval_required" and not approve:
        rights = candidate["rights"]
        terms = rights.get("provider_terms") or {}
        return {**base, "status": "approval_required", "reasons": policy["reasons"],
                "terms": {"source": rights.get("source"), "license": rights.get("license"),
                          "license_url": rights.get("license_url"),
                          "creator": rights.get("creator") or (candidate.get("creator") or {}).get("name"),
                          "source_url": candidate.get("source_url"),
                          "restrictions": terms.get("restrictions", []),
                          "attribution_text": rights.get("attribution_text"),
                          "needs_note": rights.get("eligibility") == licensing.REVIEW_REQUIRED}}

    approval = None
    near = []
    downloaded = False
    if candidate["source"] == "stock":
        hub = hub or stock.StockHub()
        work_dir = Path(work_dir or Path(catalog).parent / "downloads")
        asset, created, near = ingest_stock(
            candidate, entry, actor=actor, approve=approve, note=note,
            catalog=catalog, hub=hub, work_dir=work_dir)
        downloaded = True
        asset_id = asset["id"]
        rights = asset.get("rights") or {}
        if licensing.approved(rights):
            approval = dict(rights["approval"])
        else:
            # The library already held these bytes with its own rights; the
            # stock approval did not replace them.
            approval = {"actor": actor, "utc": utc_now(), "scope": "first_use",
                        "note": "bytes already in the library; its existing rights record applies"}
    else:
        asset_id = candidate["library"]["asset_id"]
        if policy["action"] == "approval_required":
            catalog_asset = media.load(catalog)["assets"].get(asset_id)
            if catalog_asset is None:
                raise VisualAssetError(f"unknown asset: {asset_id}")
            try:
                approved_rights = licensing.approve(catalog_asset.get("rights") or {},
                                                    actor=actor, note=note)
            except licensing.ApprovalError as exc:
                raise VisualAssetError(str(exc)) from exc
            media.update_rights(asset_id, approved_rights, catalog=catalog)
            approval = dict(approved_rights["approval"])

    lineage = _lineage(candidate, entry, actor, approval)
    result = _pin(video_id, scene_id, asset_id, lineage, actor, catalog)
    _decision(video_id, scene_id, {"status": "selected", "candidate_id": candidate_id,
                                   "asset_id": asset_id, "actor": actor, "utc": utc_now()})
    return {**base, "status": "selected", "asset_id": asset_id, "downloaded": downloaded,
            "approved": bool(approval), "near_duplicates": near,
            "stale_stages": result["stale_stages"]}


def save_to_library(video_id, scene_id, candidate_id, actor, *, approve=False, note="",
                    catalog=media.DEFAULT_CATALOG, hub=None, work_dir=None):
    """Keep a stock candidate in the library without putting it in a scene.

    Without ``approve`` it is saved *unapproved*: present, searchable, and
    refused by the rights gate until someone approves it (which happens on
    first selection). Downloading is the point of this action, so an
    ineligible candidate is still refused.
    """
    candidate, entry = _stored_candidate(video_id, scene_id, candidate_id)
    if candidate["source"] != "stock":
        raise VisualAssetError("only stock candidates need saving; the rest are already yours")
    if (candidate.get("rights") or {}).get("eligibility") == licensing.INELIGIBLE:
        raise VisualAssetError("these terms rule out use; it was not saved")
    hub = hub or stock.StockHub()
    work_dir = Path(work_dir or Path(catalog).parent / "downloads")
    asset, created, near = ingest_stock(candidate, entry, actor=actor, approve=approve,
                                        note=note, catalog=catalog, hub=hub, work_dir=work_dir)
    return {"asset_id": asset["id"], "created": created, "near_duplicates": near,
            "approved": licensing.approved(asset.get("rights"))}


def _request_generation(video_id, scene_id, candidate, entry, actor, base):
    """The scene is to be generated rather than sourced.

    Hands the work to the ordinary ``scenes`` stage: the same router, job
    store, quality re-roll and GPU-worker queue as any other scene. Here we
    only clear the scene's current picture, set its request to the planned
    one (16:9 native size, fresh seed) and say honestly whether the GPU can
    take it now. Nothing is generated by this call.
    """
    plan = candidate["generation"]
    result = regenerate_scene(video_id, scene_id, actor=actor,
                              width=plan["width"], height=plan["height"],
                              planned_seed=plan["seed"], run=False)
    gpu = gpu_state()
    deferred = gpu["state"] != "ready"
    _decision(video_id, scene_id, {
        "status": "generation_deferred" if deferred else "generation_requested",
        "candidate_id": candidate["candidate_id"], "actor": actor, "utc": utc_now(),
        "gpu": gpu, "request_digest": result["request_digest"]})
    return {**base, "status": "generation_requested", "deferred": deferred, "gpu": gpu,
            "request_digest": result["request_digest"], "seed": result["seed"],
            "stale_stages": result["stale_stages"],
            "detail": ("The GPU is not available. The scene will generate when it is on; "
                       "everything else in this production can continue." if deferred else
                       "Run the scenes stage to generate it.")}


def regenerate_scene(video_id, scene_id, *, actor, seed=None, prompt=None,
                     negative_prompt=None, width=None, height=None,
                     planned_seed=None, depicted=True, run=False,
                     catalog=media.DEFAULT_CATALOG):
    """Make one scene's picture be generated again, differently.

    The scene's picture and any pinned stock or library choice are cleared,
    its seed moves by the pipeline's re-roll step (or is set), and its
    request digest is recomputed so the job store treats it as new work.
    Other scenes are untouched. ``run=True`` then runs the scenes stage,
    which generates only the scenes that have no picture.
    """
    project = _project()
    if not (actor or "").strip():
        raise VisualAssetError("regeneration records who asked; actor is required")
    with project.project_lock(video_id):
        pdir, metadata, board = _load_project(video_id)
        scene = _scene(board, scene_id)
        gen = scene.setdefault("generation", {})
        history = list(gen.get("regenerations") or [])
        had_generated = bool(scene.get("image")) and not ownermedia.is_owner_scene(scene)
        if seed is not None:
            new_seed = int(seed)
        elif planned_seed is not None and not had_generated:
            # Nothing was generated for this scene yet, so the plan's own
            # seed is the natural first draw; later calls re-roll.
            new_seed = int(planned_seed)
        else:
            new_seed = int(gen.get("seed", 0)) + project._SCENE_RESEED_STEP * (len(history) + 1)
        if prompt is not None and prompt.strip():
            scene["image_prompt"] = prompt.strip()
        if negative_prompt is not None:
            scene["negative_prompt"] = negative_prompt
        if width and height:
            gen["width"], gen["height"] = int(width), int(height)
        history.append({"utc": utc_now(), "actor": actor, "from_seed": gen.get("seed"),
                        "to_seed": new_seed, "previous_image": scene.get("image"),
                        "previous_provider": gen.get("provider")})
        gen.update({"seed": new_seed, "regenerations": history[-20:], "job_id": None,
                    "provider": None, "provider_job_id": None, "quality": None})
        if depicted:
            # Asked for as a generation: an abstract plate would not be it.
            gen["require_depicted"] = True
        gen.pop("remote_state", None)
        gen.pop("owner_asset_id", None)
        scene["image"] = None
        scene.pop("source", None)
        gen["request_digest"] = storyboard_mod.scene_request(scene).digest()
        storyboard_mod.save(video_id, board)

    # Drop any pin on this scene so the stage generates it instead of
    # re-applying the old choice.
    pinned = [e for e in ownermedia.visuals(metadata) if e.get("scene_id") == scene_id]
    for entry in pinned:
        try:
            media.remove_usage(entry["asset_id"], video_id, scene_id, catalog=catalog)
        except media.MediaError:
            pass
    if pinned:
        remaining = [{"asset_id": e["asset_id"], "scene_id": e["scene_id"], "lineage": e.get("lineage")}
                     if e.get("scene_id") else e["asset_id"]
                     for e in ownermedia.visuals(metadata) if e.get("scene_id") != scene_id]
        project.set_owner_media(video_id, {"visuals": remaining}, actor, catalog=catalog)
    with project.project_lock(video_id):
        metadata = json.loads((pdir / "metadata.json").read_text())
        metadata.setdefault("status", {})[project._visual_stage(video_id)] = "PENDING"
        project.save_metadata(pdir, metadata)
    out = {"scene_id": scene_id, "seed": new_seed,
           "request_digest": gen["request_digest"],
           "stale_stages": project._stale_stages(video_id, metadata)}
    if run:
        stage = project.run_scenes(video_id)
        out["stage"] = {"ok": stage.ok, "message": stage.message,
                        "waiting_for_gpu": bool((stage.data or {}).get("waiting_for_gpu"))}
    return out


# --------------------------------------------------------------------------
# the production's record of where its pictures came from
# --------------------------------------------------------------------------

def sources_for_production(metadata, board):
    """One record per scene: where the picture came from and on what terms.

    Built from the storyboard and the owner-media selection, never from the
    discovery store, so it describes what is *in the production*. Stock and
    library scenes carry their licence, creator, retrieval date and approval;
    generated scenes carry their provider, seed and prompt digest.
    """
    entries = {e.get("scene_id"): e for e in ownermedia.visuals(metadata) if e.get("scene_id")}
    pool = {e["asset_id"]: e for e in ownermedia.visuals(metadata)}
    out = []
    for scene in (board or {}).get("scenes", []):
        source = scene.get("source") or {}
        generation_record = scene.get("generation") or {}
        if source.get("kind") == "owner_media":
            entry = entries.get(scene["scene_id"]) or pool.get(source.get("asset_id")) or {}
            rights = entry.get("rights") or {}
            external = licensing.is_external(rights)
            out.append({
                "scene_id": scene["scene_id"],
                "kind": "stock" if external else "library",
                "media_kind": source.get("media_kind"),
                "asset_id": source.get("asset_id"),
                "staged_path": source.get("staged_path"),
                "provider": rights.get("source") if external else "owner library",
                "creator": rights.get("creator"),
                "source_url": rights.get("source_url") or entry.get("source"),
                "license": rights.get("license"),
                "license_url": rights.get("license_url"),
                "terms_reference": rights.get("terms_reference"),
                "retrieved_utc": rights.get("retrieved_utc"),
                "commercial_use": bool(rights.get("commercial_use")),
                # Raw, not coerced: an unknown requirement (None) on external
                # media must read as unknown, never as "not required".
                "attribution_required": (rights.get("attribution_required") if external
                                         else bool(rights.get("attribution_required"))),
                "attribution_text": rights.get("attribution_text"),
                "restrictions": (rights.get("provider_terms") or {}).get("restrictions", []),
                "modification_allowed": (rights.get("provider_terms") or {}).get("modification_allowed"),
                "redistribution": (rights.get("provider_terms") or {}).get("redistribution"),
                "verification": (rights.get("verification") or {}).get("status")
                or rights.get("status"),
                "approval": rights.get("approval"),
                "lineage": entry.get("lineage"),
            })
        elif scene.get("image"):
            out.append({
                "scene_id": scene["scene_id"],
                "kind": "generated",
                "media_kind": "image",
                "provider": generation_record.get("provider"),
                "job_id": generation_record.get("job_id"),
                "seed": generation_record.get("seed"),
                "model": generation_record.get("model"),
                "request_digest": generation_record.get("request_digest"),
                "width": generation_record.get("width"),
                "height": generation_record.get("height"),
                "license": "Owner-created",
                "commercial_use": True,
                "attribution_required": False,
            })
    return out


def attributions(sources):
    """Credit lines the production owes, deduplicated and in scene order."""
    seen, lines = set(), []
    for record in sources:
        if record.get("attribution_required") and record.get("attribution_text"):
            text = record["attribution_text"]
            if text not in seen:
                seen.add(text)
                lines.append(text)
    return lines


CREDITS_HEADING = "Visual credits:"
YOUTUBE_DESCRIPTION_LIMIT = 5000


def credits_block(sources):
    """The credit lines the video description must carry, or ``""``.

    Only credits the terms require. Courtesy credits (Pixabay) stay in
    ``visual_credits.txt``; they are not a legal condition of use.
    """
    lines = attributions(sources)
    if not lines:
        return ""
    return CREDITS_HEADING + "\n" + "\n".join(f"- {line}" for line in lines)


def description_with_credits(description, sources):
    """The description as published: the editorial text, then the required
    credits. Idempotent - credits already present are not repeated."""
    block = credits_block(sources)
    base = (description or "").rstrip()
    if not block or block in base:
        return base
    return (base + "\n\n" if base else "") + block


def attribution_problems(sources, description=""):
    """Reasons the required credits cannot be satisfied. Fail closed.

    A required credit with no text to print, an external source whose
    requirement is unknown, or credits that push the description past what
    YouTube accepts, each block review: the video would otherwise ship
    something its licence did not allow.
    """
    problems = []
    for record in sources:
        if record["kind"] == "generated":
            continue
        where = f"scene {record['scene_id']} ({record.get('provider') or 'unknown source'})"
        required = record.get("attribution_required")
        if record["kind"] == "stock" and required is None:
            problems.append(f"{where}: whether attribution is required is not recorded")
        elif required and not (record.get("attribution_text") or "").strip():
            problems.append(f"{where}: attribution is required but no credit line is recorded")
    if not problems and len(description_with_credits(description, sources)) > YOUTUBE_DESCRIPTION_LIMIT:
        problems.append("the required visual credits do not fit in a "
                        f"{YOUTUBE_DESCRIPTION_LIMIT}-character description; shorten the description")
    return problems


def credits_text(video_id, sources):
    """A plain-text credits file for the package and the Kdenlive handoff."""
    lines = [f"Visual sources for {video_id}", ""]
    for record in sources:
        if record["kind"] == "generated":
            lines.append(f"{record['scene_id']}: generated ({record.get('provider') or 'unknown provider'}, "
                         f"seed {record.get('seed')})")
            continue
        lines.append(f"{record['scene_id']}: {record.get('provider')} - "
                     f"{record.get('license') or 'licence not recorded'}")
        if record.get("attribution_text"):
            lines.append(f"    credit: {record['attribution_text']}")
        if record.get("source_url"):
            lines.append(f"    source: {record['source_url']}")
        if record.get("retrieved_utc"):
            lines.append(f"    retrieved: {record['retrieved_utc']}")
        if record.get("approval"):
            lines.append(f"    approved: {record['approval'].get('actor')} at {record['approval'].get('utc')}")
        for restriction in record.get("restrictions") or []:
            lines.append(f"    restriction: {restriction}")
    return "\n".join(lines) + "\n"


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

def _print(value):
    print(json.dumps(value, indent=2, default=str))


def main(argv=None):
    import envfile
    envfile.load_env_file()
    parser = argparse.ArgumentParser(prog="visual_assets", description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("status", help="provider health, GPU readiness and autonomy level")
    p = sub.add_parser("search", help="find and rank options for one scene")
    p.add_argument("video_id")
    p.add_argument("scene_id")
    p.add_argument("--query")
    p.add_argument("--kind", choices=VISUAL_KINDS)
    p.add_argument("--source", action="append", choices=("library", "stock", "generated"))
    p.add_argument("--more", action="store_true", help="search stock even when the library has a strong match")
    p = sub.add_parser("select", help="choose a candidate for a scene")
    p.add_argument("video_id")
    p.add_argument("scene_id")
    p.add_argument("candidate_id")
    p.add_argument("--actor", required=True)
    p.add_argument("--approve", action="store_true", help="approve first use of external media")
    p.add_argument("--note", default="")
    p = sub.add_parser("regenerate", help="generate one scene's picture again")
    p.add_argument("video_id")
    p.add_argument("scene_id")
    p.add_argument("--actor", required=True)
    p.add_argument("--seed", type=int)
    p.add_argument("--prompt")
    p.add_argument("--run", action="store_true")
    p = sub.add_parser("sources", help="where this production's pictures came from")
    p.add_argument("video_id")
    args = parser.parse_args(argv)
    try:
        if args.command == "status":
            _print(status())
        elif args.command == "search":
            entry = discover(args.video_id, args.scene_id, query=args.query, kind=args.kind,
                             sources=args.source, more=args.more)
            _print({"summary": entry["summary"], "providers": entry["providers"],
                    "gpu": entry["gpu"],
                    "candidates": [{"id": c["candidate_id"], "score": c["ranking"]["score"],
                                    "confidence": c["ranking"]["confidence"],
                                    "policy": c["policy"]["action"],
                                    "why": c["ranking"]["summary"]} for c in entry["candidates"]]})
        elif args.command == "select":
            _print(select(args.video_id, args.scene_id, args.candidate_id, args.actor,
                          approve=args.approve, note=args.note))
        elif args.command == "regenerate":
            _print(regenerate_scene(args.video_id, args.scene_id, actor=args.actor,
                                    seed=args.seed, prompt=args.prompt, run=args.run))
        elif args.command == "sources":
            pdir, metadata, board = _load_project(args.video_id)
            _print({"sources": sources_for_production(metadata, board),
                    "attributions": attributions(sources_for_production(metadata, board))})
    except (VisualAssetError, media.MediaError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
