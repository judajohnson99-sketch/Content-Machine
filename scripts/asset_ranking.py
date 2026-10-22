#!/usr/bin/env python3
"""One explainable ranking for every visual a scene could use.

Stock photos, stock footage, assets already in the owner's library and
pictures yet to be generated are scored on the same factors, so "which of
these is best for this scene" has one answer with its reasoning attached
rather than four incomparable lists.

What the numbers are, and are not:

* Every factor is **derived from evidence the candidate carries** - words,
  dimensions, licence, cost, history, and where a local file exists, the
  pixels. None of it is a judgement that a picture is good. A candidate's
  ``confidence`` says how much evidence there was, and ``limitations`` says
  what could not be measured, so a high score on thin metadata reads as the
  guess it is.
* **Licensing is a gate, then a factor.** An ``ineligible`` candidate scores
  zero and is never recommended; ``review_required`` is penalised and says
  why. Eligibility is read from the rights record, never from a score.
* **Model similarity is advisory.** A CLIP rank passed in as a hint is shown
  as a factor note and never lifts confidence: no vision model here is
  calibrated, and an uncalibrated result must not look like a measurement.
* **Spending is a tiebreak, not a verdict.** Reuse and zero-cost sources get
  a bonus, and a within-margin library or stock match is recommended over a
  fresh generation - but a clearly better generated picture still wins.

Standard library only (qc/quality are used for pixels when a file exists).
"""
import re

try:
    import asset_licensing as licensing
except ImportError:                                   # package-style import
    from . import asset_licensing as licensing

WEIGHTS = {
    "relevance": 0.28,
    "subject": 0.10,
    "direction": 0.10,
    "format": 0.14,
    "technical": 0.08,
    "motion": 0.06,
    "licensing": 0.10,
    "cost": 0.06,
    "reuse": 0.08,
}
assert abs(sum(WEIGHTS.values()) - 1.0) < 1e-9

LABELS = {
    "relevance": "Matches the scene",
    "subject": "Shows the subject",
    "direction": "Fits the visual direction",
    "format": "Aspect ratio and resolution",
    "technical": "Technical quality",
    "motion": "Suits the motion",
    "licensing": "Licence",
    "cost": "Cost and effort",
    "reuse": "Reuse",
}

# A reusable or free candidate within this much of the best generated one is
# preferred; beyond it, quality wins and the generation is recommended.
SPEND_MARGIN = 0.10
MIN_USEFUL_SCORE = 0.35

STOPWORDS = frozenset("""
a an and are as at be by for from in into is it its of on or over that the their
this to under with without very more most while near behind above below
""".split())

# Generator boilerplate: words that steer a diffusion model and mean nothing
# to a stock library's search box.
PROMPT_BOILERPLATE = frozenset("""
cinematic ultra detailed highly hyperdetailed intricate masterpiece photorealistic
realistic hyperrealistic 4k 8k 16k hd uhd quality best sharp focus render rendered
rendering octane unreal engine trending artstation award winning professional
dramatic stunning beautiful amazing epic concept art illustration painting digital
""".split())


def tokens(text):
    """Lower-cased content words, lightly stemmed so 'forests' meets 'forest'."""
    out = []
    for word in re.findall(r"[a-z0-9]+", str(text or "").lower()):
        if word in STOPWORDS or len(word) < 3 or word.isdigit():
            continue
        for suffix in ("ing", "ed", "es", "s"):
            if word.endswith(suffix) and len(word) - len(suffix) >= 4:
                word = word[:-len(suffix)]
                break
        out.append(word)
    return out


def search_terms(text, limit=6):
    """A short keyword query from prose: content words, boilerplate removed,
    order kept, duplicates dropped."""
    seen, words = set(), []
    for word in re.findall(r"[A-Za-z0-9']+", str(text or "")):
        low = word.lower().strip("'")
        if (low in STOPWORDS or low in PROMPT_BOILERPLATE or len(low) < 3
                or low.isdigit() or low in seen):
            continue
        seen.add(low)
        words.append(low)
        if len(words) >= limit:
            break
    return words


def _clamp(value):
    return max(0.0, min(1.0, float(value)))


def _candidate_text(candidate):
    parts = [candidate.get("title"), candidate.get("description"),
             " ".join(candidate.get("tags") or [])]
    generation = candidate.get("generation") or {}
    parts.append(generation.get("prompt"))
    return " ".join(str(p) for p in parts if p)


def _coverage(wanted, have):
    """Weighted share of ``wanted`` (token -> weight) found in ``have``."""
    if not wanted:
        return None
    total = sum(wanted.values())
    hit = sum(weight for token, weight in wanted.items() if token in have)
    return hit / total if total else None


# --------------------------------------------------------------------------
# factors
# --------------------------------------------------------------------------

def _f_relevance(candidate, req, notes):
    text = set(tokens(_candidate_text(candidate)))
    wanted = {}
    for token in tokens(req.get("query")):
        wanted[token] = wanted.get(token, 0) + 1.0
    for token in req.get("terms") or []:
        for t in tokens(token):
            wanted.setdefault(t, 1.0)
    coverage = _coverage(wanted, text)
    if coverage is None:
        notes["limitations"].append("The scene gave no words to match against.")
        return 0.4, "no search words for this scene"
    depth = len(text)
    if depth < 4:
        notes["thin_text"] = True
        notes["limitations"].append(
            "Little descriptive text came with this media, so the match is a weak guess.")
    hint = candidate.get("semantic_rank")
    detail = f"{round(coverage * 100)}% of the scene's key words appear in its description"
    score = coverage
    if hint:
        # Advisory: blends in, never lifts confidence, and says so.
        score = max(coverage, 0.6 * coverage + 0.4 * _clamp(hint.get("score", 0)))
        detail += f"; image-text similarity ({hint.get('method', 'model')}) agrees to {round(_clamp(hint.get('score', 0)) * 100)}% (advisory, uncalibrated)"
        notes["advisory"].append(
            f"{hint.get('method', 'A vision model')} similarity is an uncalibrated hint, not a measurement.")
    if candidate.get("source") == "generated":
        # A prompt matches itself. What the render shows is unknown until it
        # exists, so a generation is credited with a fair expectation, not
        # with certainty - otherwise it would win every scene by construction.
        score = min(score, 0.75)
        detail += "; discounted because the picture does not exist yet"
    return _clamp(score), detail


def _f_subject(candidate, req, notes):
    subjects = {t: 1.0 for s in req.get("subjects") or [] for t in tokens(s)}
    if not subjects:
        return None, "no distinct subject named for this scene"
    text = set(tokens(_candidate_text(candidate)))
    coverage = _coverage(subjects, text)
    missing = sorted(t for t in subjects if t not in text)
    detail = ("all named subjects present" if not missing
              else "missing from its description: " + ", ".join(missing[:4]))
    if candidate.get("source") == "generated":
        detail = "built from this scene's own prompt, not yet seen"
        notes["limitations"].append(
            "A generated picture matches its prompt by construction; whether the render does is only known once it exists.")
        return 0.7, detail
    return _clamp(coverage), detail


def _f_direction(candidate, req, notes):
    text = set(tokens(_candidate_text(candidate)))
    wanted = {t: 1.0 for term in req.get("direction_terms") or [] for t in tokens(term)}
    avoid = {t for term in req.get("avoid") or [] for t in tokens(term)}
    clash = sorted(avoid & text)
    parts, score = [], None
    palette = (candidate.get("pixels") or {}).get("palette_coverage")
    coverage = _coverage(wanted, text)
    if coverage is not None:
        # Palette words rarely appear in a stock caption, so their absence is
        # weak evidence; the pixels (once downloaded) are the real test.
        score = 0.5 + 0.4 * coverage
        parts.append(f"{round(coverage * 100)}% of the direction's palette/mood words appear in its text")
        if candidate.get("source") != "generated" and palette is None:
            notes["limitations"].append(
                "Colour and mood were judged from its caption only; the picture itself has not been compared to the direction.")
    if palette is not None:
        score = palette if score is None else (score + palette) / 2
        parts.append(f"{round(palette * 100)}% of its colour sits in the palette")
    if score is None:
        notes["limitations"].append("No visual direction to compare against.")
        return 0.5, "no visual direction recorded for this production"
    if clash:
        score = max(0.0, score - 0.4)
        parts.append("mentions things the direction avoids: " + ", ".join(clash[:3]))
    return _clamp(score), "; ".join(parts)


def _f_format(candidate, req, notes):
    width, height = candidate.get("width"), candidate.get("height")
    if not (width and height):
        notes["limitations"].append("Its dimensions are unknown.")
        return 0.4, "dimensions unknown"
    target_w, target_h = req.get("target_width") or 1920, req.get("target_height") or 1080
    aspect, target = width / height, target_w / target_h
    closeness = 1.0 - min(1.0, abs(aspect - target) / target)
    # Landscape sources crop into 16:9 gracefully; portrait sources lose most
    # of the frame, so the aspect term is steep below ~0.8 of the target.
    aspect_score = closeness ** 2
    delivered = (candidate.get("full") or {}).get("width") or width
    res = _clamp(delivered / target_w)
    detail = f"{width}x{height}, {aspect:.2f}:1 against a {target:.2f}:1 frame"
    if delivered < target_w:
        detail += f"; delivered at {delivered}px wide, so it is scaled up"
        notes["limitations"].append(
            f"Delivered at {delivered}px wide for a {target_w}px frame; it will be upscaled.")
    if candidate.get("source") == "generated":
        # A generation is made at the GPU's native size and upscaled by design.
        res = max(res, 0.6)
        detail = (f"generated at {width}x{height} native and finished to the frame "
                  f"({target_w}x{target_h}); a 16:9 plan fits it exactly")
    return _clamp(0.55 * aspect_score + 0.45 * res), detail


def _f_technical(candidate, req, notes):
    pixels = candidate.get("pixels") or {}
    verdict = pixels.get("verdict")
    if verdict == "BLOCKED":
        return 0.0, "an objective defect was measured: " + (pixels.get("detail") or "unusable frame")
    if verdict in ("WARN", "WARNING", "REVIEW"):
        return 0.6, "measured, with a warning: " + (pixels.get("detail") or "see findings")
    if verdict in ("OK", "PASS"):
        return 0.85, "no measurable fault (that is not the same as good)"
    notes["limitations"].append(
        "Technical quality was not measured; it needs the file, which has not been downloaded.")
    notes["unmeasured"] = True
    width = candidate.get("width") or 0
    return (0.5 if width < 1280 else 0.6), "not measured yet; judged on declared size only"


def _f_motion(candidate, req, notes):
    wants = req.get("wants") or "either"
    kind = candidate.get("media_kind")
    if wants == "either":
        return (0.8 if kind == "video" else 0.7), ("footage gives real motion" if kind == "video"
                                                    else "a still, brought to life with a slow camera move")
    if wants == kind:
        score, detail = 1.0, f"the scene calls for {wants}"
    else:
        score, detail = (0.45 if kind == "image" else 0.7), f"the scene calls for {wants}; this is {kind}"
    needed = req.get("duration_seconds")
    length = candidate.get("duration_seconds")
    if kind == "video" and needed and length and length < needed:
        score *= 0.8
        detail += f"; {length:.0f}s of footage for a {needed:.0f}s shot, so it will loop"
    return _clamp(score), detail


def _f_licensing(candidate, req, notes):
    rights = candidate.get("rights") or {}
    source = candidate.get("source")
    if source in ("generated", "cloud"):
        return 1.0, "the owner's own generation"
    eligibility = rights.get("eligibility")
    library = candidate.get("library") or {}
    if (source == "library" and library.get("rights_ok") is not True
            and not library.get("approvable")):
        notes["limitations"].append("Its rights record in the library is incomplete.")
        return 0.3, library.get("rights_problem") or "rights incomplete"
    if eligibility == licensing.INELIGIBLE:
        return 0.0, "licence rules it out"
    if eligibility == licensing.REVIEW_REQUIRED:
        notes["limitations"].append("Rights need a person to review them before use.")
        return 0.4, "rights record incomplete; needs review"
    if licensing.approved(rights) or not licensing.is_external(rights):
        return 1.0, "rights recorded and approved"
    return 0.8, "provider licence allows commercial use; first use needs your approval"


def _f_cost(candidate, req, notes):
    cost = candidate.get("cost") or {}
    if cost.get("money_usd") or cost.get("metered"):
        notes["limitations"].append("Using this costs money.")
        return 0.0, "costs money per image and needs your authorisation"
    resource = cost.get("resource")
    if resource == "none":
        return 1.0, "already in your library"
    if resource == "network":
        return 0.85, "one download, no GPU time"
    if resource == "gpu":
        state = candidate.get("state")
        seconds = cost.get("gpu_seconds_estimate")
        extra = f" (about {seconds:.0f}s)" if seconds else ""
        if state in ("deferred", "queued"):
            return 0.35, "needs the GPU, which is not available right now" + extra
        return 0.6, "uses GPU time" + extra
    return 0.7, "effort unknown"


def _f_reuse(candidate, req, notes):
    history = (candidate.get("library") or {}).get("usage") or []
    used_here = set(req.get("used_assets") or ())
    asset_id = (candidate.get("library") or {}).get("asset_id")
    if asset_id and asset_id in used_here:
        return 0.3, "already used by another scene in this video"
    if candidate.get("source") == "library":
        approved = licensing.approved(candidate.get("rights")) or not licensing.is_external(candidate.get("rights"))
        if history and approved:
            return 1.0, f"approved and used {len(history)} time(s) before"
        return 0.85, "already in your library; no new download or generation"
    if candidate.get("duplicate_of"):
        return 0.9, "matches something already in your library"
    return 0.5, "new to your library"


FACTORS = {"relevance": _f_relevance, "subject": _f_subject,
           "direction": _f_direction, "format": _f_format,
           "technical": _f_technical, "motion": _f_motion,
           "licensing": _f_licensing, "cost": _f_cost, "reuse": _f_reuse}


# --------------------------------------------------------------------------
# ranking
# --------------------------------------------------------------------------

def score_candidate(candidate, requirement):
    """The scored, explained record for one candidate."""
    notes = {"limitations": [], "advisory": [], "thin_text": False, "unmeasured": False}
    factors, weighted, used_weight = [], 0.0, 0.0
    for factor_id, fn in FACTORS.items():
        value, detail = fn(candidate, requirement, notes)
        weight = WEIGHTS[factor_id]
        if value is None:
            factors.append({"id": factor_id, "label": LABELS[factor_id], "score": None,
                            "weight": weight, "detail": detail, "applied": False})
            continue
        factors.append({"id": factor_id, "label": LABELS[factor_id],
                        "score": round(value, 3), "weight": weight, "detail": detail,
                        "applied": True})
        weighted += value * weight
        used_weight += weight
    score = weighted / used_weight if used_weight else 0.0

    rights = candidate.get("rights") or {}
    eligible = rights.get("eligibility") != licensing.INELIGIBLE
    library = candidate.get("library") or {}
    if (candidate.get("source") == "library" and library.get("rights_ok") is not True
            and not library.get("approvable")):
        eligible = False
    pixels = candidate.get("pixels") or {}
    if pixels.get("verdict") == "BLOCKED":
        eligible = False
    if not eligible:
        score = 0.0

    confidence, reasons = _confidence(candidate, notes)
    return {
        "candidate_id": candidate["candidate_id"],
        "score": round(score, 3),
        "eligible": eligible,
        "factors": factors,
        "confidence": confidence,
        "confidence_reasons": reasons,
        "limitations": list(dict.fromkeys(notes["limitations"])),
        "advisory": list(dict.fromkeys(notes["advisory"])),
    }


def _confidence(candidate, notes):
    """How much evidence stands behind the score. Never 'high' on a guess."""
    points, reasons = 2, []
    if notes["thin_text"]:
        points -= 1
        reasons.append("little descriptive text")
    if notes["unmeasured"]:
        points -= 1
        reasons.append("pixels not measured")
    if candidate.get("source") == "generated" and candidate.get("state") != "ready":
        points -= 1
        reasons.append("not generated yet")
    if candidate.get("source") == "library":
        described = bool((candidate.get("description") or "").strip())
        if described:
            points += 1
            reasons.append("described by a person")
    if (candidate.get("pixels") or {}).get("verdict") in ("OK", "PASS"):
        points += 1
        reasons.append("pixels measured")
    if points >= 3:
        level = "high"
    elif points >= 2:
        level = "medium"
    else:
        level = "low"
    if notes["advisory"] and level == "high":
        level = "medium"
        reasons.append("model similarity is advisory")
    return level, reasons


def _ready(candidate):
    return candidate.get("state", "available") in ("available", "ready", "downloaded")


def rank(candidates, requirement):
    """Candidates best-first, each with its explanation, plus the pick.

    Returns ``{"ranked": [...], "recommended": id|None, "best": id|None,
    "summary": str}``. ``best`` is the highest score of anything;
    ``recommended`` is what to use *now*, after the spending tiebreak and
    skipping anything that is not ready (a generation waiting for the GPU).
    """
    scored = []
    for candidate in candidates:
        record = score_candidate(candidate, requirement)
        scored.append((record, candidate))
    scored.sort(key=lambda pair: (-pair[0]["score"], pair[1]["candidate_id"]))
    for position, (record, candidate) in enumerate(scored, start=1):
        record["rank"] = position
        record["source"] = candidate.get("source")
        record["ready"] = _ready(candidate)

    usable = [(r, c) for r, c in scored if r["eligible"] and r["ready"]
              and r["score"] >= MIN_USEFUL_SCORE]
    best = scored[0][0]["candidate_id"] if scored and scored[0][0]["eligible"] else None
    recommended, reason = None, ""
    if usable:
        top_record, top = usable[0]
        recommended = top_record
        reason = f"the strongest ready match ({round(top_record['score'] * 100)}/100)"
        if top.get("source") in ("generated", "cloud"):
            cheaper = next(((r, c) for r, c in usable
                            if c.get("source") in ("library", "stock")
                            and top_record["score"] - r["score"] <= SPEND_MARGIN), None)
            if cheaper:
                recommended = cheaper[0]
                reason = (f"within {round(SPEND_MARGIN * 100)} points of a fresh generation "
                          f"({round(cheaper[0]['score'] * 100)} vs {round(top_record['score'] * 100)}) "
                          f"without spending GPU time")
        elif top.get("source") == "stock":
            reused = next(((r, c) for r, c in usable
                           if c.get("source") == "library"
                           and top_record["score"] - r["score"] <= SPEND_MARGIN), None)
            if reused:
                recommended = reused[0]
                reason = (f"within {round(SPEND_MARGIN * 100)} points of the best stock match and "
                          "already in your library, so nothing new is downloaded")
    by_id = {c["candidate_id"]: c for _, c in scored}
    for record, candidate in scored:
        record["summary"] = _summary(record, candidate)
    ids = [r["candidate_id"] for r, _ in scored]
    for record, candidate in scored:
        record["alternatives"] = _alternatives(record, scored)
        record["recommended"] = bool(recommended and recommended["candidate_id"] == record["candidate_id"])
    summary = ""
    if recommended:
        pick = by_id[recommended["candidate_id"]]
        summary = f"Recommended: {_name(pick)} - {reason}."
        if best and best != recommended["candidate_id"]:
            summary += (f" The highest-scoring option is {_name(by_id[best])}, "
                        "but it is not ready to use.")
    elif scored:
        summary = ("Nothing here is both eligible and ready to use yet. "
                   "Add a source, free a provider, or generate a picture when the GPU is on.")
    else:
        summary = "No candidates were found."
    return {"ranked": [r for r, _ in scored],
            "recommended": recommended["candidate_id"] if recommended else None,
            "best": best, "summary": summary, "order": ids}


def _name(candidate):
    label = (candidate.get("title") or candidate.get("description") or "").strip()
    where = {"library": "from your library", "stock": f"from {candidate.get('provider', 'stock').title()}",
             "generated": "a new generation", "cloud": "a cloud generation"}.get(candidate.get("source"), "")
    return f"\"{label[:60]}\" {where}".strip() if label else where or candidate["candidate_id"]


def _summary(record, candidate):
    strong = sorted((f for f in record["factors"] if f["applied"] and f["score"] is not None),
                    key=lambda f: f["score"] * f["weight"], reverse=True)
    weak = [f for f in record["factors"] if f["applied"] and f["score"] is not None and f["score"] < 0.5]
    if not record["eligible"]:
        why = next((f["detail"] for f in record["factors"] if f["id"] in ("licensing", "technical") and f["score"] == 0.0), "not usable")
        return f"Not usable: {why}."
    bits = [strong[0]["detail"]] if strong else []
    if weak:
        bits.append("weaker on " + ", ".join(f["label"].lower() for f in weak[:2]))
    return "; ".join(bits).capitalize() + "." if bits else ""


def _alternatives(record, scored):
    """Up to two other options, with the honest reason each is behind or different."""
    out = []
    for other, candidate in scored:
        if other["candidate_id"] == record["candidate_id"] or not other["eligible"]:
            continue
        gap = other["score"] - record["score"]
        if gap > 0:
            why = f"scores {round(gap * 100)} points higher"
        else:
            why = f"scores {round(-gap * 100)} points lower"
        if not other["ready"]:
            why += " but is not ready (needs the GPU)"
        out.append({"candidate_id": other["candidate_id"], "source": candidate.get("source"), "why": why})
        if len(out) == 2:
            break
    return out
