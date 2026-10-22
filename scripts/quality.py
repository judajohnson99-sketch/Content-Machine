#!/usr/bin/env python3
"""Perceptual quality: does the output look and sound right for what it is?

``qc.py`` answers whether a render is technically valid. This module answers
a different question - whether a technically valid render has problems a
viewer or listener would notice - and it answers it *relative to the
production's own intent*. Darkness, near-stillness and an unchanging bed are
defects in an explainer and the whole point of a sleep video, so every
threshold here is chosen by an intent profile derived from the concept, the
visual direction document and the listening context, never by one global
standard.

Seven evaluations, each reported separately with how it was done:

- ``scene_intent`` - does each distinct scene image use the direction's
  palette, sit in the lighting key the direction asks for, and stay within
  the brightness and vividness the viewing context tolerates;
- ``scene_depiction`` - does the image depict its prompt, judged by a local
  vision model. Optional, off unless configured, and never a paid call;
- ``visual_continuity`` - do consecutive scenes change look gently enough
  for the transition between them, and does any scene look unlike the rest;
- ``rendered_continuity`` - does the delivered MP4 contain flash frames or
  abrupt steps that the edit never planned;
- ``soundtrack_delivery`` - does the soundtrack actually muxed into the MP4
  still meet the loudness/peak criteria the composed track was built to;
- ``soundtrack_dynamics`` - startle spikes, dropouts and loudness drift
  across the whole programme, not one excerpt;
- ``soundtrack_repetition`` - an audible loop: the bed repeating on a short
  period.

Three rules carry the design:

**Measured, never assumed.** An evaluation that could not run is
``UNAVAILABLE`` with the reason, never a pass. ``NOT_APPLICABLE`` is used
only when the question genuinely does not arise (one held frame has no
neighbour to be continuous with).

**Subjective findings inform; only objective defects block.** Findings use
the three severities the rest of the codebase uses. ``block`` is reserved
for defects with no legitimate reading (the delivered soundtrack clips, or
is silent); everything taste-dependent is ``warn`` or ``info`` - surfaced to
the reviewer, never a veto on a deliberate choice.

**Passing is not "good".** An OK verdict means nothing measurable is wrong
for this intent. Whether the video is worth publishing stays a human's call.

Standard library plus ffmpeg, like the rest of the render path.
"""
import base64
import cmath
import hashlib
import json
import logging
import math
import os
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import audio as audio_mod  # noqa: E402
import motion as motion_mod  # noqa: E402

log = logging.getLogger("quality")

QUALITY_VERSION = 1
REPORT_FILENAME = "quality_report.json"
CACHE_FILENAME = "quality_cache.json"

OK, REVIEW, DEFECT, INCOMPLETE = "OK", "REVIEW", "DEFECT", "INCOMPLETE"
PASS, UNAVAILABLE, NOT_APPLICABLE = "PASS", "UNAVAILABLE", "NOT_APPLICABLE"

PASS_NOTE = ("An OK verdict means nothing measurable is wrong for this "
             "production's intent. It is not a judgement that the video is "
             "good - only a person watching and listening can make that.")


def utc_now():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _env(name, default=None):
    value = os.environ.get(name)
    return value if value not in (None, "") else default


def _env_float(name, default):
    try:
        return float(_env(name, default))
    except (TypeError, ValueError):
        return float(default)


def finding(code, severity, detail, **extra):
    out = {"code": code, "severity": severity, "detail": detail}
    out.update({k: v for k, v in extra.items() if v is not None})
    return out


def timestamp(seconds):
    seconds = max(0.0, float(seconds))
    h, rem = divmod(int(seconds), 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


# --------------------------------------------------------------------------
# colour science - small, stdlib, CIELAB because distances there are roughly
# perceptual and lightness is separable from hue
# --------------------------------------------------------------------------

_LINEAR = [((c / 255.0) / 12.92) if c <= 10 else (((c / 255.0) + 0.055) / 1.055) ** 2.4
           for c in range(256)]


def _f_lab(t):
    return t ** (1.0 / 3.0) if t > 0.008856 else 7.787 * t + 16.0 / 116.0


def rgb_to_lab(r, g, b):
    """sRGB 0-255 -> CIELAB (D65)."""
    rl = _LINEAR[max(0, min(255, int(round(r))))]
    gl = _LINEAR[max(0, min(255, int(round(g))))]
    bl = _LINEAR[max(0, min(255, int(round(b))))]
    x = (0.4124564 * rl + 0.3575761 * gl + 0.1804375 * bl) / 0.95047
    y = 0.2126729 * rl + 0.7151522 * gl + 0.0721750 * bl
    z = (0.0193339 * rl + 0.1191920 * gl + 0.9503041 * bl) / 1.08883
    fx, fy, fz = _f_lab(x), _f_lab(y), _f_lab(z)
    return 116.0 * fy - 16.0, 500.0 * (fx - fy), 200.0 * (fy - fz)


def yuv_to_lab(y, u, v):
    """Mean limited-range BT.601 YUV (what ffmpeg's default scaler writes for
    an RGB source) -> approximate CIELAB."""
    c, d, e = y - 16.0, u - 128.0, v - 128.0
    r = 1.164 * c + 1.596 * e
    g = 1.164 * c - 0.392 * d - 0.813 * e
    b = 1.164 * c + 2.017 * d
    return rgb_to_lab(max(0.0, min(255.0, r)), max(0.0, min(255.0, g)),
                      max(0.0, min(255.0, b)))


def _hex_lab(value):
    value = value.lstrip("#")
    return rgb_to_lab(int(value[0:2], 16), int(value[2:4], 16), int(value[4:6], 16))


def chroma(lab):
    return math.hypot(lab[1], lab[2])


def hue(lab):
    return math.degrees(math.atan2(lab[2], lab[1])) % 360.0


def hue_distance(a, b):
    d = abs(a - b) % 360.0
    return min(d, 360.0 - d)


def delta_e(a, b):
    return math.sqrt(sum((x - y) ** 2 for x, y in zip(a, b)))


def vividness(lab):
    """Chroma weighted by lightness (equal to chroma at L*=50).

    Raw chroma calls a deep indigo as "saturated" as a bright magenta, but
    they look nothing alike on a screen in a dark room: perceived
    colourfulness falls with luminance, so a dark jewel tone is restful and
    a bright saturated field glares. This is the crude, stdlib form of that.
    """
    return chroma(lab) * lab[0] / 50.0


# The colour words visual direction documents actually use (palette terms
# like "deep indigo", "silver mist", "restrained brass warmth"), resolved to
# a reference colour. Only the hue family matters for palette conformance -
# lighting decides lightness - so these need to be the right *kind* of
# colour, not exact swatches.
COLOUR_NAMES = {
    "black": "#050505", "obsidian": "#1b1b22", "ink": "#1c2333", "charcoal": "#36454f",
    "graphite": "#41424c", "grey": "#808080", "gray": "#808080", "ash": "#b2beb5",
    "slate": "#708090", "pewter": "#8e9196", "stone": "#9a9488", "smoke": "#848884",
    "fog": "#c8ccd0", "mist": "#c4ccd6", "silver": "#c0c0c0", "pearl": "#eae0c8",
    "ivory": "#fffff0", "cream": "#fffdd0", "bone": "#e3dac9", "white": "#f4f4f4",
    "moonlight": "#c9d6e8", "oat": "#d8c8a8", "linen": "#faf0e6", "parchment": "#f1e9d2",
    "sand": "#c2b280", "beige": "#d8c8a8", "taupe": "#8b7d6b",
    "brown": "#6b4423", "umber": "#635147", "sienna": "#a0522d", "chestnut": "#954535",
    "walnut": "#5d432c", "mahogany": "#6c2e1f", "espresso": "#3c2a21", "rust": "#b7410e",
    "terracotta": "#e2725b", "brick": "#a6463a", "copper": "#b87333", "bronze": "#cd7f32",
    "brass": "#b5a642", "ochre": "#cc7722", "amber": "#ffbf00", "honey": "#eba937",
    "gold": "#d4af37", "golden": "#d4af37", "mustard": "#e1ad01", "saffron": "#f4c430",
    "yellow": "#f2d43b", "lemon": "#fff44f", "peach": "#ffcba4", "apricot": "#fbceb1",
    "orange": "#ff8c1a", "tangerine": "#f28500", "coral": "#ff7f50", "salmon": "#fa8072",
    "candlelight": "#ffb46b", "firelight": "#ff9a3c", "ember": "#d2502a",
    "red": "#c0282d", "crimson": "#b0172d", "scarlet": "#ff2400", "burgundy": "#800020",
    "maroon": "#800000", "wine": "#722f37", "rose": "#d9718a", "blush": "#de5d83",
    "pink": "#f4a6c0", "magenta": "#c2187b", "fuchsia": "#c71585",
    "plum": "#673147", "aubergine": "#472c4c", "mauve": "#b784a7", "lavender": "#b4a7d6",
    "lilac": "#c8a2c8", "violet": "#7f5fbf", "purple": "#6a3d9a", "amethyst": "#9966cc",
    "orchid": "#da70d6", "periwinkle": "#8c9fe6", "indigo": "#3b3486",
    "navy": "#1f2f5a", "midnight": "#191d3a", "sapphire": "#0f52ba", "cobalt": "#0047ab",
    "blue": "#2f6fd0", "azure": "#3a8ee6", "cerulean": "#2a7bbd", "sky": "#87ceeb",
    "denim": "#1560bd", "steel": "#4682b4",
    "teal": "#2a8080", "aqua": "#5fd3d3", "turquoise": "#40e0d0", "cyan": "#2ad4e0",
    "seafoam": "#93e9be", "mint": "#98ff98",
    "green": "#3a8a3a", "emerald": "#2e8b57", "jade": "#00a86b", "forest": "#2f5d3a",
    "moss": "#8a9a5b", "sage": "#9caf88", "olive": "#708238", "fern": "#4f7942",
    "pine": "#2c5545", "lime": "#a4d64e", "chartreuse": "#b5d334",
}
_COLOUR_LAB = {name: _hex_lab(value) for name, value in COLOUR_NAMES.items()}

# A palette colour or pixel below this chroma reads as a neutral (grey,
# silver, mist, stone) rather than as a hue.
NEUTRAL_CHROMA = 12.0
# Pixels darker than this are shadow: they carry no hue a viewer reads.
SHADOW_LIGHTNESS = 10.0
# How far a pixel's hue may sit from a palette hue and still be that colour.
HUE_TOLERANCE_DEGREES = 35.0

# Named hue families for explaining what an off-palette region actually is.
_FAMILY_ANCHORS = {name: hue(_COLOUR_LAB[name]) for name in
                   ("red", "orange", "yellow", "green", "cyan", "blue", "violet", "magenta")}


def hue_family(angle):
    return min(_FAMILY_ANCHORS, key=lambda n: hue_distance(_FAMILY_ANCHORS[n], angle))


def resolve_palette(terms):
    """Palette terms -> reference colours. The head colour word (the last one
    in the term: "midnight *blue*", "silver *mist*") names the colour."""
    resolved, unresolved = [], []
    for term in terms or []:
        words = re.findall(r"[a-z]+", str(term).lower())
        known = [w for w in words if w in _COLOUR_LAB]
        if not known:
            unresolved.append(term)
            continue
        name = known[-1]
        lab = _COLOUR_LAB[name]
        resolved.append({"term": term, "colour": name,
                         "neutral": chroma(lab) < NEUTRAL_CHROMA,
                         "hue": round(hue(lab), 1)})
    return resolved, unresolved


# --------------------------------------------------------------------------
# intent - what "right" means for this production
# --------------------------------------------------------------------------

# Thresholds per visual viewing profile, in CIELAB units (lightness L* 0-100,
# chroma C*ab, colour difference dE76). These are the boundaries of
# "a viewer in this context would notice", calibrated against the
# pipeline's own plates and the good/bad fixtures in tests/test_quality.py.
VISUAL_PROFILES = {
    "sleep": {
        "label": "sleep / wind-down viewing, usually in a dark room",
        "max_mean_lightness": 45.0, "max_vividness": 40.0,
        "cut_lightness": 10.0, "lightness_per_second": 4.0,
        "cut_colour": 20.0, "colour_per_second": 6.0,
        # Darkening is gentler on a viewer in a dark room than brightening.
        "darkening_allowance": 1.5,
        "outlier_colour": 24.0, "flash_lightness": 6.0, "step_lightness": 5.0,
    },
    "calm": {
        "label": "calm background or ambient viewing",
        "max_mean_lightness": 65.0, "max_vividness": 50.0,
        "cut_lightness": 14.0, "lightness_per_second": 5.0,
        "cut_colour": 25.0, "colour_per_second": 7.0,
        "darkening_allowance": 1.25,
        "outlier_colour": 30.0, "flash_lightness": 9.0, "step_lightness": 8.0,
    },
    "narrative": {
        "label": "watched, narrated or explanatory viewing",
        "max_mean_lightness": None, "max_vividness": 65.0,
        "cut_lightness": 24.0, "lightness_per_second": 7.0,
        "cut_colour": 32.0, "colour_per_second": 8.0,
        "darkening_allowance": 1.0,
        "outlier_colour": 40.0, "flash_lightness": 14.0, "step_lightness": 14.0,
    },
}

# At most this many findings of one kind are listed individually; the rest
# are counted. Fourteen near-identical warnings teach a reviewer to skim.
MAX_LISTED = 6

# Soundtrack dynamics per listening context (the same contexts audio.py's
# criteria use). Spikes are momentary loudness above the running median;
# dropouts are momentary loudness this far under the programme median. The
# sleep spike threshold follows sound_design.ROLE_GAIN_LIMITS, which keeps
# every event at least 8-10 dB under the bed: a 6 LU jump is already outside
# anything the pipeline designs. Measured on the Dreamdrip production: its
# largest natural swell is 3.9 LU, an injected door slam 8.8 LU.
DYNAMICS = {
    "background_sleep": {"spike_lu": 6.0, "dropout_lu": 18.0, "drift_lu": 3.0,
                         "dropout_severity": "warn"},
    "background_focus": {"spike_lu": 8.0, "dropout_lu": 18.0, "drift_lu": 4.0,
                         "dropout_severity": "warn"},
    "ambient_watch": {"spike_lu": 10.0, "dropout_lu": 20.0, "drift_lu": 5.0,
                      "dropout_severity": "warn"},
    "narration": {"spike_lu": 15.0, "dropout_lu": 25.0, "drift_lu": 6.0,
                  "dropout_severity": "info"},
}

LOW_KEY_WORDS = ("moonlit", "moonlight", "night", "nocturnal", "midnight", "dim",
                 "dimly", "low-key", "candlelit", "candle", "lamplight", "lamplit",
                 "dusk", "twilight", "shadowy", "darkness", "dark", "starlit",
                 "starlight", "firelight", "ember", "gloom", "noir")
HIGH_KEY_WORDS = ("bright", "sunlit", "sunny", "sunshine", "daylight", "daytime",
                  "midday", "noon", "airy", "high-key", "overexposed")
# Mean L* past which a low-key image no longer reads as low-key, and under
# which a high-key one no longer reads as high-key.
LOW_KEY_MAX_LIGHTNESS = 50.0
HIGH_KEY_MIN_LIGHTNESS = 40.0

_SLEEP_WORDS = ("sleep", "bedtime", "lullaby", "insomnia", "nap", "wind down", "wind-down")


def _words_in(text, words):
    text = f" {text.lower()} "
    return sorted({w for w in words if re.search(r"(?<![a-z])" + re.escape(w) + r"(?![a-z])", text)})


def intent_profile(metadata, storyboard, audio_manifest, concept, holds_one_frame=False):
    """What this production is meant to look and sound like, with where each
    answer came from. Every threshold below is chosen from this."""
    metadata, storyboard = metadata or {}, storyboard or {}
    audio_manifest, concept = audio_manifest or {}, concept or {}

    context = ((audio_manifest.get("assessment") or {}).get("criteria") or {}).get(
        "listening_context")
    context_source = "audio manifest"
    if not context:
        context = (metadata.get("audio_plan") or {}).get("listening_context")
        context_source = "audio plan"
    if context not in audio_mod.LISTENING_CONTEXTS:
        context, context_source = audio_mod.DEFAULT_LISTENING_CONTEXT, "default"

    concept_text = " ".join(str(concept.get(k) or "") for k in (
        "niche", "content_format", "visual_concept", "target_audience"))
    sleepy = _words_in(concept_text, _SLEEP_WORDS)
    if context == "background_sleep" or sleepy:
        profile = "sleep"
        profile_source = ("listening context" if context == "background_sleep"
                          else f"concept says: {', '.join(sleepy)}")
    elif context in ("background_focus", "ambient_watch"):
        profile, profile_source = "calm", "listening context"
    elif context == "narration":
        profile, profile_source = "narrative", "listening context"
    else:
        profile, profile_source = "calm", "default"

    direction = storyboard.get("visual_direction") or (
        (metadata.get("visual_plan") or {}).get("direction")) or {}
    identity = direction.get("identity") or {}
    concept_direction = concept.get("visual_direction") or {}
    palette_terms = list(identity.get("palette") or concept_direction.get("palette") or [])
    palette_source = ("visual direction" if identity.get("palette")
                      else "concept" if concept_direction.get("palette") else None)
    resolved, unresolved = resolve_palette(palette_terms)

    light_text = " ".join(str(x or "") for x in (
        identity.get("lighting"), identity.get("atmosphere"), identity.get("render_intent"),
        concept_direction.get("atmosphere"), concept_direction.get("lighting"),
        concept.get("visual_concept")))
    low, high = _words_in(light_text, LOW_KEY_WORDS), _words_in(light_text, HIGH_KEY_WORDS)
    if low and not high:
        key = {"value": "low", "evidence": low}
    elif high and not low:
        key = {"value": "high", "evidence": high}
    else:
        key = {"value": None, "evidence": low + high}

    return {
        "listening_context": {"value": context, "source": context_source},
        "visual_profile": {"value": profile, "label": VISUAL_PROFILES[profile]["label"],
                           "source": profile_source},
        "expected_key": key,
        "palette": {"terms": palette_terms, "source": palette_source,
                    "resolved": resolved, "unresolved": unresolved},
        "holds_one_frame": bool(holds_one_frame),
    }


# --------------------------------------------------------------------------
# image measurement
# --------------------------------------------------------------------------

_SAMPLE_W, _SAMPLE_H = 48, 27


def measure_image(path):
    """Per-pixel CIELAB of a small area-averaged downsample, plus summaries.
    None when ffmpeg cannot read the file."""
    result = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", str(path), "-frames:v", "1",
         "-vf", f"scale={_SAMPLE_W}:{_SAMPLE_H}:flags=area,format=rgb24",
         "-f", "rawvideo", "-"], capture_output=True)
    data = result.stdout
    need = _SAMPLE_W * _SAMPLE_H * 3
    if result.returncode != 0 or len(data) < need:
        return None
    pixels = [rgb_to_lab(data[i], data[i + 1], data[i + 2]) for i in range(0, need, 3)]
    n = len(pixels)
    mean = tuple(sum(p[k] for p in pixels) / n for k in range(3))
    lit = [p for p in pixels if p[0] >= SHADOW_LIGHTNESS]
    return {
        "pixels": pixels,
        "mean_lab": mean,
        "mean_lightness": mean[0],
        "mean_chroma": (sum(chroma(p) for p in lit) / len(lit)) if lit else 0.0,
        "mean_vividness": (sum(vividness(p) for p in lit) / len(lit)) if lit else 0.0,
        "shadow_share": 1.0 - len(lit) / n,
    }


def palette_conformance(pixels, resolved):
    """How much of an image's colour belongs to the palette.

    Hue-based on purpose: a palette names colour families and the lighting
    decides how light or dark they appear, so "deep indigo" in moonlight and
    indigo in lamplight are the same palette colour. Each chromatic pixel is
    weighted by its chroma, so a vivid off-palette region counts for what it
    looks like and a faint tint barely counts. Shadows and neutrals carry no
    hue and are left out of the share.
    """
    hues = [r["hue"] for r in resolved if not r["neutral"]]
    total = matched = 0.0
    off = {}
    lit = 0
    for p in pixels:
        if p[0] < SHADOW_LIGHTNESS:
            continue
        lit += 1
        c = chroma(p)
        if c < NEUTRAL_CHROMA:
            continue
        h = hue(p)
        total += c
        if any(hue_distance(h, ph) <= HUE_TOLERANCE_DEGREES for ph in hues):
            matched += c
        else:
            family = hue_family(h)
            off[family] = off.get(family, 0.0) + c
    if not lit:
        return {"assessable": False, "reason": "the frame is almost entirely shadow"}
    chromatic_weight = total / lit
    if chromatic_weight < 4.0:
        return {"assessable": False, "chromatic_weight": round(chromatic_weight, 2),
                "reason": "the frame is nearly neutral, so it carries little palette to compare"}
    dominant = max(off, key=off.get) if off else None
    return {
        "assessable": True,
        "coverage": round(matched / total, 3) if total else 1.0,
        "chromatic_weight": round(chromatic_weight, 2),
        "dominant_off_palette": dominant,
        "dominant_off_palette_share": round(off[dominant] / total, 3) if dominant else 0.0,
    }


# Below this share of chromatic weight on-palette, an image is off-palette;
# below the second it is noticeably mixed.
PALETTE_OFF = 0.40
PALETTE_MIXED = 0.65


# --------------------------------------------------------------------------
# inputs: which images, in which order, under which transitions
# --------------------------------------------------------------------------

def _resolve(pdir, rel):
    path = Path(rel)
    return path if path.is_absolute() else Path(pdir) / path


def timeline(pdir, storyboard, spec):
    """Scenes in playback order: image path, start time, outgoing transition."""
    scenes = (storyboard or {}).get("scenes") or []
    if scenes:
        starts = motion_mod.scene_start_times(scenes)
        out = []
        for scene, start in zip(scenes, starts):
            transition = scene.get("transition") or {}
            kind = transition.get("kind", "crossfade")
            out.append({
                "scene_id": scene.get("scene_id"),
                "image": _resolve(pdir, scene["image"]) if scene.get("image") else None,
                "prompt": scene.get("image_prompt") or "",
                "start": float(start),
                "duration": float(scene.get("duration_seconds") or 0.0),
                "transition": kind,
                "transition_seconds": 0.0 if kind == "cut" else float(
                    transition.get("duration_seconds") or 0.0),
            })
        return out
    # Image-cycling projects: the renderer cycles the sorted images with one
    # global crossfade. Reproduce that order so continuity is judged on what
    # the viewer actually sees.
    spec = spec or {}
    images_cfg = spec.get("images") or {}
    source = _resolve(pdir, images_cfg.get("source_dir") or "images")
    exts = {".png", ".jpg", ".jpeg", ".webp", ".bmp"}
    paths = sorted(p for p in source.glob("*") if p.suffix.lower() in exts) if source.is_dir() else []
    if not paths:
        return []
    per = float(images_cfg.get("seconds_per_image") or 10.0)
    fade = spec.get("crossfade") or {}
    d = float(fade.get("duration_seconds") or 0.0) if fade.get("enabled", True) else 0.0
    total = float(spec.get("duration_seconds") or per * len(paths))
    out, t, i = [], 0.0, 0
    while t < total and i < 2000:
        out.append({"scene_id": None, "image": paths[i % len(paths)], "prompt": "",
                    "start": t, "duration": per,
                    "transition": "crossfade" if d else "cut", "transition_seconds": d})
        t += per - d
        i += 1
    return out


# --------------------------------------------------------------------------
# evaluation: scene imagery vs intent
# --------------------------------------------------------------------------

def _distinct_images(entries):
    """Distinct image paths with the scenes that use each, in first-use order."""
    seen = {}
    for entry in entries:
        if entry["image"] is None:
            continue
        key = str(entry["image"])
        if key not in seen:
            seen[key] = {"path": entry["image"], "scene_ids": [], "prompt": entry["prompt"]}
        if entry["scene_id"]:
            seen[key]["scene_ids"].append(entry["scene_id"])
    return list(seen.values())


def _label(item):
    ids = item["scene_ids"]
    if not ids:
        return item["path"].name
    return ids[0] if len(ids) == 1 else f"{ids[0]} (+{len(ids) - 1} more)"


def evaluate_scene_intent(images, intent, measured):
    profile = VISUAL_PROFILES[intent["visual_profile"]["value"]]
    palette = intent["palette"]
    key = intent["expected_key"]["value"]
    findings, rows = [], []
    if not images:
        return {"status": UNAVAILABLE, "reason": "no scene images to evaluate"}
    for item in images:
        m = measured.get(str(item["path"]))
        label = _label(item)
        if m is None:
            findings.append(finding("unreadable", "warn",
                                    f"{label}: {item['path'].name} could not be measured",
                                    scene_ids=item["scene_ids"] or None))
            continue
        row = {"image": item["path"].name, "scene_ids": item["scene_ids"],
               "mean_lightness": round(m["mean_lightness"], 1),
               "mean_chroma": round(m["mean_chroma"], 1),
               "vividness": round(m["mean_vividness"], 1)}
        L = m["mean_lightness"]
        ceiling = profile["max_mean_lightness"]
        if key == "low" and L > LOW_KEY_MAX_LIGHTNESS:
            findings.append(finding(
                "off_key_bright", "warn",
                f"{label} averages {L:.0f} L*, too bright to read as the low-key "
                f"lighting the direction asks for ({', '.join(intent['expected_key']['evidence'])})",
                scene_ids=item["scene_ids"] or None))
        elif ceiling is not None and L > ceiling:
            findings.append(finding(
                "bright_for_context", "warn",
                f"{label} averages {L:.0f} L*, brighter than suits "
                f"{profile['label']} (about {ceiling:.0f} L* or below)",
                scene_ids=item["scene_ids"] or None))
        if key == "high" and L < HIGH_KEY_MIN_LIGHTNESS:
            findings.append(finding(
                "off_key_dark", "warn",
                f"{label} averages {L:.0f} L*, too dark for the high-key lighting "
                f"the direction asks for ({', '.join(intent['expected_key']['evidence'])})",
                scene_ids=item["scene_ids"] or None))
        if m["mean_vividness"] > profile["max_vividness"]:
            findings.append(finding(
                "vivid_for_context", "warn",
                f"{label} is vivid (lightness-weighted chroma {m['mean_vividness']:.0f}): "
                f"bright saturated colour glares in {profile['label']} "
                f"(about {profile['max_vividness']:.0f} or below)",
                scene_ids=item["scene_ids"] or None))
        if palette["resolved"]:
            conf = palette_conformance(m["pixels"], palette["resolved"])
            row["palette"] = conf
            if conf.get("assessable"):
                cov = conf["coverage"]
                if cov < PALETTE_MIXED:
                    severity = "warn" if cov < PALETTE_OFF else "info"
                    findings.append(finding(
                        "off_palette" if cov < PALETTE_OFF else "partly_off_palette",
                        severity,
                        f"{label}: only {cov:.0%} of its colour is in the palette "
                        f"({', '.join(palette['terms'])}); the largest off-palette "
                        f"colour is {conf['dominant_off_palette']} "
                        f"({conf['dominant_off_palette_share']:.0%})",
                        scene_ids=item["scene_ids"] or None))
        rows.append(row)
    checks = ["lighting key" if key else None,
              f"{intent['visual_profile']['value']} brightness/vividness ceilings",
              "palette conformance" if palette["resolved"] else None]
    method = (f"Measured {len(images)} distinct image(s) from a {_SAMPLE_W}x{_SAMPLE_H} "
              f"area-averaged downsample in CIELAB: {', '.join(c for c in checks if c)}.")
    if not palette["resolved"]:
        method += (" Palette not checked: " + ("the direction's palette terms "
                   f"({', '.join(palette['unresolved'])}) name no recognised colour."
                   if palette["unresolved"] else "this production has no palette."))
    return {"findings": findings, "measurements": {"images": rows}, "method": method}


# --------------------------------------------------------------------------
# evaluation: scene depiction (optional local vision model)
# --------------------------------------------------------------------------

class VisionJudge:
    """A local vision-language model reached over the Ollama HTTP API.

    Optional and inert by default: QUALITY_VISION_URL and
    QUALITY_VISION_MODEL must both be set, there is no default endpoint and
    no default model name, and the adapter speaks only Ollama's local API -
    so it cannot quietly become a metered call.
    """

    name = "ollama"

    def __init__(self, url=None, model=None, timeout=None):
        self.url = (url if url is not None else _env("QUALITY_VISION_URL") or "").rstrip("/")
        self.model = model if model is not None else _env("QUALITY_VISION_MODEL")
        self.timeout = timeout if timeout is not None else _env_float("QUALITY_VISION_TIMEOUT", 90)

    def configured(self):
        return bool(self.url and self.model)

    def unavailable_reason(self):
        return ("no local vision model configured (set QUALITY_VISION_URL and "
                "QUALITY_VISION_MODEL to an Ollama vision model); scene depiction "
                "was not checked")

    PROMPT = (
        "You are checking one still from a video against what it was meant to "
        "show. Intended picture: \"{intent}\".\n"
        "Reply with ONLY a JSON object: {{\"score\": 1-5 (5 = clearly depicts the "
        "intended picture, 1 = shows something else), \"defects\": [visible "
        "generation defects such as text, watermarks, deformed or duplicated "
        "objects, melted edges; empty if none], \"explanation\": one sentence}}")

    def judge(self, image_path, intent):
        with tempfile.TemporaryDirectory(prefix="cm-vision-") as tmp:
            small = Path(tmp) / "frame.jpg"
            subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(image_path),
                            "-frames:v", "1", "-vf", "scale=640:-2", str(small)],
                           capture_output=True)
            data = (small if small.is_file() else Path(image_path)).read_bytes()
        body = json.dumps({
            "model": self.model, "stream": False, "format": "json",
            "prompt": self.PROMPT.format(intent=intent[:600]),
            "images": [base64.b64encode(data).decode("ascii")],
        }).encode("utf-8")
        req = urllib.request.Request(f"{self.url}/api/generate", data=body,
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=self.timeout) as response:
            reply = json.loads(response.read().decode("utf-8"))
        parsed = json.loads(reply.get("response") or "{}")
        score = parsed.get("score")
        if not isinstance(score, (int, float)) or not 1 <= score <= 5:
            raise ValueError(f"vision model returned no usable score: {parsed!r}")
        defects = [str(d) for d in (parsed.get("defects") or []) if str(d).strip()]
        return {"score": float(score), "defects": defects[:6],
                "explanation": str(parsed.get("explanation") or "")[:300]}


def _sha256(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def _sample_evenly(items, limit):
    if len(items) <= limit:
        return list(items)
    step = len(items) / float(limit)
    return [items[int(i * step)] for i in range(limit)]


def evaluate_scene_depiction(images, judge, cache):
    if judge is None or not judge.configured():
        return {"status": UNAVAILABLE,
                "reason": (judge or VisionJudge()).unavailable_reason()}
    candidates = [i for i in images if i["prompt"] and i["path"].is_file()]
    if not candidates:
        return {"status": UNAVAILABLE, "reason": "no scene images carry a prompt to judge against"}
    limit = int(_env_float("QUALITY_VISION_MAX_IMAGES", 8))
    sample = _sample_evenly(candidates, max(1, limit))
    findings, rows, errors = [], [], []
    for item in sample:
        key = hashlib.sha256(f"{judge.model}|{item['prompt']}|{_sha256(item['path'])}".encode()).hexdigest()
        result = cache.get(key)
        if result is None:
            try:
                result = judge.judge(item["path"], item["prompt"])
            except (urllib.error.URLError, OSError, ValueError) as e:
                errors.append(f"{_label(item)}: {e}")
                if isinstance(e, urllib.error.URLError) or isinstance(e, ConnectionError):
                    break  # the endpoint is down; do not wait out every timeout
                continue
            cache[key] = result
        label = _label(item)
        rows.append({"image": item["path"].name, "scene_ids": item["scene_ids"], **result})
        if result["score"] <= 2:
            findings.append(finding(
                "does_not_depict_intent", "warn",
                f"{label}: the vision model scores it {result['score']:.0f}/5 against its "
                f"prompt - {result['explanation'] or 'no explanation given'}",
                scene_ids=item["scene_ids"] or None))
        elif result["score"] < 4:
            findings.append(finding(
                "weak_depiction", "info",
                f"{label}: scored {result['score']:.0f}/5 - {result['explanation']}",
                scene_ids=item["scene_ids"] or None))
        if result["defects"]:
            findings.append(finding(
                "visible_generation_defects", "warn",
                f"{label}: {', '.join(result['defects'])}",
                scene_ids=item["scene_ids"] or None))
    if not rows:
        return {"status": UNAVAILABLE,
                "reason": "the vision model could not be reached or gave no usable answer: "
                          + "; ".join(errors[:3])}
    method = (f"{judge.model} via {judge.name} judged {len(rows)} of {len(candidates)} "
              f"distinct prompted image(s) against their prompts"
              + (" (evenly sampled)" if len(candidates) > len(sample) else "")
              + (f"; {len(errors)} could not be judged" if errors else "") + ".")
    return {"findings": findings, "measurements": {"judgements": rows}, "method": method}


# --------------------------------------------------------------------------
# evaluation: planned visual continuity (source images in playback order)
# --------------------------------------------------------------------------

def evaluate_visual_continuity(entries, intent, measured):
    if intent["holds_one_frame"]:
        return {"status": NOT_APPLICABLE,
                "reason": "this production is one deliberately held frame"}
    usable = [e for e in entries if e["image"] is not None and measured.get(str(e["image"]))]
    distinct = {str(e["image"]) for e in usable}
    if len(distinct) < 2:
        return {"status": NOT_APPLICABLE,
                "reason": "fewer than two distinct pictures, so there is no change between scenes to judge"}
    profile = VISUAL_PROFILES[intent["visual_profile"]["value"]]
    findings, worst = [], None
    pairs = 0
    for a, b in zip(usable, usable[1:]):
        if str(a["image"]) == str(b["image"]):
            continue
        pairs += 1
        la, lb = measured[str(a["image"])]["mean_lab"], measured[str(b["image"])]["mean_lab"]
        d = a["transition_seconds"] if a["transition"] != "cut" else 0.0
        dl, de = abs(la[0] - lb[0]), delta_e(la, lb)
        tol_l = profile["cut_lightness"] + profile["lightness_per_second"] * d
        if lb[0] < la[0]:
            tol_l *= profile["darkening_allowance"]
        tol_e = profile["cut_colour"] + profile["colour_per_second"] * d
        excess = max(dl / tol_l, de / tol_e)
        if worst is None or excess > worst[0]:
            worst = (excess, a, b, dl, de)
        if excess <= 1.0:
            continue
        how = f"a cut" if d == 0 else f"a {d:g}s dissolve"
        parts = []
        if dl > tol_l:
            parts.append(f"lightness {'rises' if lb[0] > la[0] else 'drops'} {dl:.0f} L* "
                         f"(tolerated here: {tol_l:.0f})")
        if de > tol_e:
            temp = ""
            if la[2] * lb[2] < 0 and abs(la[2] - lb[2]) > 10:
                temp = ", flipping " + ("warm to cool" if la[2] > 0 else "cool to warm")
            parts.append(f"colour shifts dE {de:.0f}{temp} (tolerated: {tol_e:.0f})")
        ids = [x for x in (a["scene_id"], b["scene_id"]) if x]
        findings.append(finding(
            "jarring_transition", "warn",
            f"{a['scene_id'] or a['image'].name} -> {b['scene_id'] or b['image'].name} "
            f"at {timestamp(b['start'])}: {'; '.join(parts)} across {how} - "
            f"jarring for {profile['label']}",
            at_seconds=round(b["start"], 2), scene_ids=ids or None))

    outliers = []
    if len(distinct) >= 4:
        looks = [measured[k]["mean_lab"] for k in distinct]
        median = tuple(sorted(l[i] for l in looks)[len(looks) // 2] for i in range(3))
        seen = set()
        for e in usable:
            k = str(e["image"])
            if k in seen:
                continue
            seen.add(k)
            dist = delta_e(measured[k]["mean_lab"], median)
            if dist > profile["outlier_colour"]:
                outliers.append(k)
                l = measured[k]["mean_lab"]
                findings.append(finding(
                    "off_look_scene", "warn",
                    f"{e['scene_id'] or e['image'].name} looks unlike the rest of the video "
                    f"(dE {dist:.0f} from its typical look: "
                    f"{'brighter' if l[0] > median[0] else 'darker'}, "
                    f"{hue_family(hue(l)) if chroma(l) >= NEUTRAL_CHROMA else 'neutral'} "
                    f"vs {hue_family(hue(median)) if chroma(median) >= NEUTRAL_CHROMA else 'neutral'})",
                    at_seconds=round(e["start"], 2),
                    scene_ids=[e["scene_id"]] if e["scene_id"] else None))
    measurements = {"transitions_compared": pairs, "distinct_images": len(distinct),
                    "outlier_images": len(outliers)}
    if worst:
        measurements["worst_transition"] = {
            "from": worst[1]["scene_id"] or worst[1]["image"].name,
            "to": worst[2]["scene_id"] or worst[2]["image"].name,
            "lightness_change": round(worst[3], 1), "colour_change": round(worst[4], 1),
            "share_of_tolerance": round(worst[0], 2)}
    method = (f"Compared the mean CIELAB look of {pairs} consecutive pair(s) of different "
              f"pictures in playback order; tolerance widens with dissolve length "
              f"({profile['cut_lightness']:g} L* at a cut + {profile['lightness_per_second']:g} "
              f"L*/s of dissolve for the {intent['visual_profile']['value']} profile)"
              + ("; scenes compared against the video's median look." if len(distinct) >= 4 else "."))
    return {"findings": findings, "measurements": measurements, "method": method}


# --------------------------------------------------------------------------
# evaluation: rendered continuity (frames of the delivered MP4)
# --------------------------------------------------------------------------

# Videos up to this length are scanned frame by frame; longer ones are
# scanned in windows around every planned transition, where seams and flash
# frames come from in this pipeline (each scene is one still under a smooth
# move, so the middle of a shot is not where discontinuities appear).
FULL_SCAN_MAX_SECONDS = 1800.0
MAX_WINDOWS = 120


def _parse_frame_stats(text, offset=0.0):
    frames, current = [], None
    for line in text.splitlines():
        if line.startswith("frame:"):
            match = re.search(r"pts_time:([-\d.e]+)", line)
            current = {"t": offset + float(match.group(1)) if match else None}
            frames.append(current)
        elif current is not None and "=" in line:
            key, _, value = line.partition("=")
            key = key.rsplit(".", 1)[-1]
            if key in ("YAVG", "UAVG", "VAVG"):
                try:
                    current[key] = float(value)
                except ValueError:
                    pass
    return [f for f in frames if f.get("t") is not None and "YAVG" in f]


def _scan_frames(video, start=None, length=None):
    with tempfile.TemporaryDirectory(prefix="cm-frames-") as tmp:
        out = Path(tmp) / "stats.txt"
        cmd = ["ffmpeg", "-v", "error", "-nostats"]
        if start is not None:
            cmd += ["-ss", f"{max(0.0, start):.3f}"]
        if length is not None:
            cmd += ["-t", f"{length:.3f}"]
        cmd += ["-i", str(video), "-an", "-vf",
                f"scale=64:36,signalstats,metadata=mode=print:file={out}",
                "-f", "null", "-"]
        subprocess.run(cmd, capture_output=True)
        text = out.read_text() if out.is_file() else ""
    return _parse_frame_stats(text, offset=start or 0.0)


def _probe_video(video):
    result = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0",
                             "-show_entries", "stream=r_frame_rate:format=duration",
                             "-of", "json", str(video)], capture_output=True, text=True)
    try:
        data = json.loads(result.stdout)
        duration = float(data["format"]["duration"])
        num, _, den = data["streams"][0]["r_frame_rate"].partition("/")
        fps = float(num) / float(den or 1)
        return duration, fps
    except (KeyError, IndexError, ValueError, ZeroDivisionError, json.JSONDecodeError):
        return None, None


def evaluate_rendered_continuity(video, entries, intent):
    if not video or not Path(video).is_file():
        return {"status": UNAVAILABLE, "reason": "no rendered video to scan"}
    duration, fps = _probe_video(video)
    if not duration:
        return {"status": UNAVAILABLE, "reason": "the rendered video could not be probed"}
    profile = VISUAL_PROFILES[intent["visual_profile"]["value"]]
    cuts = [e["start"] for prev, e in zip(entries, entries[1:]) if prev["transition"] == "cut"]
    boundaries = [e["start"] for e in entries[1:]]

    limit = _env_float("QUALITY_FULL_SCAN_MAX_SECONDS", FULL_SCAN_MAX_SECONDS)
    if duration <= limit:
        segments = [_scan_frames(video)]
        coverage = "every frame"
    else:
        windows = _sample_evenly(boundaries, MAX_WINDOWS) if boundaries else []
        windows = windows or [duration * i / 10.0 for i in range(1, 10)]
        tail = max((e["transition_seconds"] for e in entries), default=0.0)
        segments = [_scan_frames(video, start=b - 1.5, length=tail + 3.0) for b in windows]
        coverage = (f"windows around {len(windows)} of {len(boundaries)} planned transitions "
                    f"(video is longer than {limit:.0f}s; mid-shot frames not scanned)")
    frames_total = sum(len(s) for s in segments)
    if frames_total < 3:
        return {"status": UNAVAILABLE, "reason": "no frames could be read from the rendered video"}

    flashes, steps = [], []
    for frames in segments:
        L = [yuv_to_lab(f["YAVG"], f.get("UAVG", 128.0), f.get("VAVG", 128.0))[0] for f in frames]
        in_flash = set()
        for i in range(1, len(L) - 1):
            up, down = L[i] - L[i - 1], L[i] - L[i + 1]
            if up * down > 0 and min(abs(up), abs(down)) > profile["flash_lightness"]:
                flashes.append((frames[i]["t"], max(abs(up), abs(down)), up > 0))
                in_flash.update((i, i + 1))
        for i in range(1, len(L)):
            if i in in_flash:
                continue
            jump = abs(L[i] - L[i - 1])
            if jump <= profile["step_lightness"]:
                continue
            t = frames[i]["t"]
            if any(abs(t - c) <= 0.25 for c in cuts):
                continue  # a planned cut; judged by visual_continuity
            steps.append((t, jump))

    findings = []
    def _merge(events):
        merged = []
        for ev in sorted(events):
            if merged and ev[0] - merged[-1][0] < 1.0:
                if ev[1] > merged[-1][1]:
                    merged[-1] = ev
                continue
            merged.append(ev)
        return merged

    flashes, steps = _merge(flashes), _merge(steps)
    for t, size, bright in flashes:
        findings.append(finding(
            "flash_frame", "warn",
            f"a {'bright' if bright else 'dark'} flash of {size:.0f} L* at {timestamp(t)} "
            f"lasting a frame or two - visible as a flicker",
            at_seconds=round(t, 2)))
    for t, size in steps:
        findings.append(finding(
            "unplanned_step", "warn",
            f"the picture jumps {size:.0f} L* between two frames at {timestamp(t)}, "
            f"where the edit planned no cut - a broken dissolve or a seam between rendered pieces",
            at_seconds=round(t, 2)))
    method = (f"Scanned {frames_total} frame(s) of the delivered MP4 ({coverage}) for "
              f"one-to-two-frame lightness spikes over {profile['flash_lightness']:g} L* and "
              f"frame-to-frame steps over {profile['step_lightness']:g} L* away from planned cuts.")
    return {"findings": findings,
            "measurements": {"frames_scanned": frames_total, "coverage": coverage,
                             "duration_seconds": round(duration, 2),
                             "fps": round(fps, 3) if fps else None,
                             "flash_frames": len(flashes), "unplanned_steps": len(steps)},
            "method": method}


# --------------------------------------------------------------------------
# soundtrack scan: one decode, loudness every 100 ms and band energy every
# 250 ms across the whole programme
# --------------------------------------------------------------------------

SCAN_RATE = 16000
BAND_WINDOW_SECONDS = 0.25


def _parse_metadata_series(text, key):
    values, t = [], None
    for line in text.splitlines():
        if line.startswith("frame:"):
            match = re.search(r"pts_time:([-\d.e]+)", line)
            t = float(match.group(1)) if match else None
        elif t is not None and line.startswith(key + "="):
            raw = line.split("=", 1)[1].strip()
            try:
                value = float(raw)
            except ValueError:
                value = float("-inf") if "inf" in raw else None
            if value is not None:
                values.append((t, value))
    return values


# Band edges (Hz) for the spectral-shape series the repetition check uses.
# Six bands give a loop's timbre enough detail to recognise; the scan runs
# at 16 kHz, so the top band is 5-8 kHz.
SCAN_BANDS = ((None, 150), (150, 400), (400, 1000), (1000, 2500), (2500, 5000), (5000, None))


def scan_soundtrack(path):
    """Time series of the delivered audio, or None if it cannot be decoded.

    One decode: momentary loudness every 100 ms, RMS level per band every
    250 ms, and the full-file sample peak at the native rate.
    """
    window = int(SCAN_RATE * BAND_WINDOW_SECONDS)
    with tempfile.TemporaryDirectory(prefix="cm-audio-scan-") as tmp:
        tmp = Path(tmp)
        momentary = tmp / "m.txt"
        band_files = [tmp / f"b{i}.txt" for i in range(len(SCAN_BANDS))]
        labels = "".join(f"[b{i}]" for i in range(len(SCAN_BANDS)))
        parts = [
            "[0:a]asplit=2[native][scan]",
            "[native]volumedetect,anullsink",
            f"[scan]aresample={SCAN_RATE},aformat=channel_layouts=mono,"
            f"asplit={len(SCAN_BANDS) + 1}[m]{labels}",
            f"[m]ebur128=metadata=1,ametadata=mode=print:key=lavfi.r128.M:file={momentary},anullsink",
        ]
        for i, (low, high) in enumerate(SCAN_BANDS):
            chain = ([f"highpass=f={low}"] if low else []) + ([f"lowpass=f={high}"] if high else [])
            chain.append(f"asetnsamples=n={window}:p=0,astats=metadata=1:reset=1,"
                         f"ametadata=mode=print:key=lavfi.astats.Overall.RMS_level:file={band_files[i]}")
            sink = "[out]" if i == len(SCAN_BANDS) - 1 else ",anullsink"
            parts.append(f"[b{i}]" + ",".join(chain) + sink)
        result = subprocess.run(
            ["ffmpeg", "-hide_banner", "-nostats", "-i", str(path), "-vn",
             "-filter_complex", ";".join(parts), "-map", "[out]", "-f", "null", "-"],
            capture_output=True, text=True)
        if result.returncode != 0 or not momentary.is_file():
            return None
        series = {"momentary": _parse_metadata_series(momentary.read_text(), "lavfi.r128.M"),
                  "bands": []}
        for f in band_files:
            text = f.read_text() if f.is_file() else ""
            series["bands"].append(
                [v for _, v in _parse_metadata_series(text, "lavfi.astats.Overall.RMS_level")])
    max_volume = None
    for line in result.stderr.splitlines():
        if "max_volume:" in line:
            try:
                max_volume = float(line.split("max_volume:")[1].split()[0])
            except (IndexError, ValueError):
                pass
    series["max_volume_db"] = max_volume
    return series


def _median(values):
    ordered = sorted(values)
    if not ordered:
        return None
    mid = len(ordered) // 2
    return ordered[mid] if len(ordered) % 2 else (ordered[mid - 1] + ordered[mid]) / 2.0


def _events(times, flags, min_seconds, gap=1.0):
    """Runs of True in flags, merged across gaps shorter than ``gap``."""
    runs, start, last = [], None, None
    for t, flag in zip(times, flags):
        if flag:
            if start is None:
                start = t
            elif last is not None and t - last > gap:
                runs.append((start, last))
                start = t
            last = t
        elif start is not None and last is not None and t - last > gap:
            runs.append((start, last))
            start = last = None
    if start is not None:
        runs.append((start, last))
    return [(a, b) for a, b in runs if b - a + 0.1 >= min_seconds]


def evaluate_soundtrack_dynamics(scan, intent, head, tail, duration):
    context = intent["listening_context"]["value"]
    rules = DYNAMICS.get(context, DYNAMICS["ambient_watch"])
    lo_t, hi_t = head + 0.5, duration - tail
    points = [(t, m) for t, m in scan["momentary"] if lo_t <= t <= hi_t]
    if len(points) < 50:
        return {"status": UNAVAILABLE,
                "reason": "the programme between the fades is too short to judge its dynamics"}
    times = [t for t, _ in points]
    floor = -70.0
    values = [m if m > -200 else -200.0 for _, m in points]
    audible = [v for v in values if v > floor]
    if not audible:
        return {"status": UNAVAILABLE, "reason": "the soundtrack is silent throughout"}
    programme = _median(audible)

    # Running reference: the median of 1-second blocks over +/-15 s, so a
    # bed that evolves slowly is its own reference and only sudden events
    # stand out against it.
    blocks = {}
    for t, v in zip(times, values):
        blocks.setdefault(int(t), []).append(v)
    block_median = {k: _median(v) for k, v in blocks.items()}
    keys = sorted(block_median)
    running = {}
    for i, k in enumerate(keys):
        near = [block_median[keys[j]] for j in range(max(0, i - 15), min(len(keys), i + 16))]
        running[k] = _median(near)

    spike_flags = [v > running[int(t)] + rules["spike_lu"] for t, v in zip(times, values)]
    spikes = _events(times, spike_flags, 0.1)
    drop_flags = [v < programme - rules["dropout_lu"] or v <= floor for v in values]
    dropouts = _events(times, drop_flags, 1.0, gap=0.3)

    findings = []
    for a, b in spikes:
        peak = max(v - running[int(t)] for t, v in zip(times, values) if a <= t <= b)
        findings.append(finding(
            "startle_spike", "warn",
            f"loudness jumps {peak:.0f} LU above the surrounding bed at {timestamp(a)} - "
            f"loud enough to startle in {context.replace('_', ' ')} listening "
            f"(threshold {rules['spike_lu']:g} LU); fine if it is an intended event "
            f"such as thunder, but check it",
            at_seconds=round(a, 2)))
    for a, b in dropouts:
        findings.append(finding(
            "dropout", rules["dropout_severity"],
            f"the soundtrack drops out for {b - a + 0.1:.1f}s at {timestamp(a)} "
            f"(more than {rules['dropout_lu']:g} LU under the programme level)",
            at_seconds=round(a, 2)))

    span = times[-1] - times[0]
    drift = None
    if span >= 120:
        fifth = span / 5.0
        first = _median([v for t, v in zip(times, values) if t <= times[0] + fifth and v > floor])
        last = _median([v for t, v in zip(times, values) if t >= times[-1] - fifth and v > floor])
        if first is not None and last is not None:
            drift = last - first
            if abs(drift) > rules["drift_lu"]:
                findings.append(finding(
                    "loudness_drift", "warn",
                    f"the soundtrack ends {abs(drift):.1f} LU {'louder' if drift > 0 else 'quieter'} "
                    f"than it starts (over {timestamp(span)}); "
                    + ("rising loudness works against falling asleep"
                       if drift > 0 and context == "background_sleep"
                       else f"more than the {rules['drift_lu']:g} LU this context tolerates")))
    method = (f"Momentary loudness every 100 ms across {timestamp(span)} of programme "
              f"(fades excluded), against a 30-second running median for spikes and the "
              f"programme median for dropouts; thresholds for {context} listening.")
    return {"findings": findings,
            "measurements": {"programme_median_lufs": round(programme, 1),
                             "spikes": len(spikes), "dropouts": len(dropouts),
                             "drift_lu": round(drift, 1) if drift is not None else None,
                             "seconds_scanned": round(span, 1)},
            "method": method}


# --------------------------------------------------------------------------
# evaluation: repetition (an audible loop)
# --------------------------------------------------------------------------

REPEAT_MIN_LAG_SECONDS = 3.0
REPEAT_MAX_LAG_SECONDS = 300.0
REPEAT_EXCERPT_SECONDS = 1200.0
# Mean normalised autocorrelation across the three bands at which the band
# envelopes are repeating rather than merely resembling each other.
REPEAT_SCORE = 0.85
SHORT_LOOP_SECONDS = 60.0
# Windows this far below / above the programme's typical level are masked
# out of the repetition test (see repetition_features).
REPEAT_MASK_BELOW_DB = 12.0
REPEAT_MASK_ABOVE_DB = 9.0


def _fft(values, invert=False):
    n = len(values)
    a = list(values)
    j = 0
    for i in range(1, n):
        bit = n >> 1
        while j & bit:
            j ^= bit
            bit >>= 1
        j |= bit
        if i < j:
            a[i], a[j] = a[j], a[i]
    length = 2
    sign = 1 if invert else -1
    while length <= n:
        w_len = cmath.exp(sign * 2j * math.pi / length)
        half = length // 2
        for i in range(0, n, length):
            w = 1 + 0j
            for k in range(half):
                u, v = a[i + k], a[i + k + half] * w
                a[i + k], a[i + k + half] = u + v, u - v
                w *= w_len
        length <<= 1
    if invert:
        a = [x / n for x in a]
    return a


def autocorrelation(series):
    """Unbiased, normalised autocorrelation of a mean-removed series."""
    n = len(series)
    if n < 4:
        return []
    mean = sum(series) / n
    x = [v - mean for v in series]
    var = sum(v * v for v in x) / n
    if var <= 1e-9:
        return [0.0] * n
    size = 1
    while size < 2 * n:
        size <<= 1
    spectrum = _fft(x + [0.0] * (size - n))
    power = _fft([abs(c) ** 2 for c in spectrum], invert=True)
    return [power[k].real / (n - k) / var for k in range(n)]


def repetition_features(bands):
    """Gain-invariant spectral shape per window, with outlier windows masked.

    Each window's band levels are taken relative to that window's own mean
    level, so a fade, ducking under narration or loudness normalisation -
    all broadband gain changes - leaves the features untouched while the
    timbre a loop repeats is kept. Windows far from the programme level (a
    dropout, a startle event) are masked to the series mean: they do not
    repeat, and left in they dominate the variance and hide a real loop.
    """
    n = min(len(b) for b in bands)
    rows = [[max(v, -120.0) for v in b[:n]] for b in bands]
    level = [sum(r[i] for r in rows) / len(rows) for i in range(n)]
    typical = _median(level)
    keep = [typical - REPEAT_MASK_BELOW_DB <= l <= typical + REPEAT_MASK_ABOVE_DB for l in level]
    kept = max(1, sum(keep))
    features = []
    for r in rows:
        shape = [r[i] - level[i] for i in range(n)]
        mean = sum(v for v, ok in zip(shape, keep) if ok) / kept
        features.append([v if ok else mean for v, ok in zip(shape, keep)])
    return features, n - sum(keep)


def evaluate_soundtrack_repetition(scan, head, tail, duration):
    bands = scan.get("bands") or []
    n = min((len(b) for b in bands), default=0)
    if n * BAND_WINDOW_SECONDS < 3 * REPEAT_MIN_LAG_SECONDS:
        return {"status": UNAVAILABLE,
                "reason": "the soundtrack is too short to test for a repeating loop"}
    first = int((head + 0.5) / BAND_WINDOW_SECONDS)
    last = n - int(tail / BAND_WINDOW_SECONDS)
    if last - first < 3 * REPEAT_MIN_LAG_SECONDS / BAND_WINDOW_SECONDS:
        first, last = 0, n
    limit = int(REPEAT_EXCERPT_SECONDS / BAND_WINDOW_SECONDS)
    if last - first > limit:
        first = first + (last - first - limit) // 2
        last = first + limit
    features, masked = repetition_features([b[first:last] for b in bands])
    corrs = [autocorrelation(f) for f in features]
    length = last - first
    min_lag = int(REPEAT_MIN_LAG_SECONDS / BAND_WINDOW_SECONDS)
    max_lag = min(int(REPEAT_MAX_LAG_SECONDS / BAND_WINDOW_SECONDS), length // 2)
    scores = {lag: sum(c[lag] for c in corrs) / len(corrs)
              for lag in range(min_lag, max_lag + 1)}
    # Every multiple of a loop's period matches as well as the period does,
    # so report the first qualifying peak - the period a listener hears -
    # not whichever multiple scored a hair higher.
    lag = next((k for k in sorted(scores) if scores[k] >= REPEAT_SCORE), None)
    if lag is not None:
        while lag + 1 in scores and scores[lag + 1] > scores[lag]:
            lag += 1
        score = scores[lag]
    else:
        lag = max(scores, key=scores.get) if scores else None
        score = scores[lag] if lag else 0.0
    seconds = lag * BAND_WINDOW_SECONDS if lag else None
    findings = []
    if lag and score >= REPEAT_SCORE:
        if seconds < SHORT_LOOP_SECONDS:
            findings.append(finding(
                "audible_loop", "warn",
                f"the soundtrack repeats itself every {seconds:.1f}s (match {score:.2f}) - "
                f"a short loop a listener will start to hear as a pattern"))
        else:
            findings.append(finding(
                "long_cycle", "info",
                f"the soundtrack repeats on a {seconds / 60:.1f}-minute cycle (match {score:.2f})"))
    excerpt_seconds = length * BAND_WINDOW_SECONDS
    method = (f"Autocorrelation of the soundtrack's spectral shape ({len(bands)} bands, 250 ms "
              f"windows, independent of overall level) over {timestamp(excerpt_seconds)} of "
              f"programme, testing repeat periods from {REPEAT_MIN_LAG_SECONDS:g}s to "
              f"{max_lag * BAND_WINDOW_SECONDS:.0f}s; a mean match of {REPEAT_SCORE} or more "
              f"is a repetition"
              + (f"; {masked * BAND_WINDOW_SECONDS:.1f}s of dropouts or spikes left out." if masked else "."))
    return {"findings": findings,
            "measurements": {"best_period_seconds": seconds,
                             "best_match": round(score, 3),
                             "excerpt_seconds": round(excerpt_seconds, 1),
                             "masked_seconds": round(masked * BAND_WINDOW_SECONDS, 2)},
            "method": method}


# --------------------------------------------------------------------------
# evaluation: the delivered soundtrack against the composed one
# --------------------------------------------------------------------------

def _measure_delivered_audio(video, head, tail):
    """audio.measure on the MP4's own AAC stream, demuxed by stream copy so
    the bytes measured are the bytes delivered - without paying to decode
    the picture alongside it."""
    with tempfile.TemporaryDirectory(prefix="cm-delivered-") as tmp:
        track = Path(tmp) / "delivered.m4a"
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(video), "-vn",
                        "-c:a", "copy", str(track)], capture_output=True)
        if not track.is_file():
            return {}
        return audio_mod.measure(track, skip_head=head, skip_tail=tail)


DELIVERY_LOUDNESS_TOLERANCE_LU = 1.5
# The only delivered-soundtrack findings allowed to block review: broken
# audio with no legitimate reading. Distance from a loudness *target* is a
# judgement about intent, so it informs the reviewer instead.
OBJECTIVE_AUDIO_DEFECTS = frozenset({"clipping", "silent", "unmeasurable"})


def evaluate_soundtrack_delivery(video, audio_manifest, intent, scan, head, tail):
    if not video or not Path(video).is_file():
        return {"status": UNAVAILABLE, "reason": "no rendered video to measure"}
    delivered = _measure_delivered_audio(video, head, tail)
    if not delivered:
        return {"status": UNAVAILABLE,
                "reason": "the rendered video's soundtrack could not be measured"}
    assessment = (audio_manifest or {}).get("assessment") or {}
    criteria = assessment.get("criteria") or audio_mod.criteria_for(
        intent["listening_context"]["value"])
    structure = {"normalized": (audio_manifest or {}).get("normalized", True),
                 "has_variation": None}
    composed_codes = {f.get("code") for f in assessment.get("findings") or []}
    findings = [dict(f, detail=f"delivered soundtrack: {f['detail']}",
                     severity=f["severity"] if f["code"] in OBJECTIVE_AUDIO_DEFECTS
                     else "warn")
                for f in audio_mod.assess(delivered, criteria, structure)
                if f["code"] not in composed_codes and f["severity"] != "info"]

    max_volume = (scan or {}).get("max_volume_db")
    if (max_volume is not None and max_volume >= 0.0
            and not any(f["code"] == "clipping" for f in findings)):
        findings.append(finding(
            "sample_clipping", "block",
            f"delivered soundtrack: a sample reaches {max_volume:.1f} dBFS - it clips "
            f"somewhere in the file (the composed track did not)"))

    composed = (audio_manifest or {}).get("measurement") or {}
    comparison = None
    if composed.get("integrated_lufs") is not None and delivered.get("integrated_lufs") is not None:
        comparison = round(delivered["integrated_lufs"] - composed["integrated_lufs"], 2)
        if abs(comparison) > DELIVERY_LOUDNESS_TOLERANCE_LU:
            findings.append(finding(
                "delivery_mismatch", "warn",
                f"the delivered soundtrack is {abs(comparison):.1f} LU "
                f"{'louder' if comparison > 0 else 'quieter'} than the composed track - "
                f"the wrong audio was muxed, or the encode changed it"))
    method = ("Re-measured the soundtrack inside the MP4 (EBU R128 loudness, true peak, "
              "spectral balance, full-file sample peak) against the "
              f"{criteria.get('listening_context')} criteria the composed track was built to"
              + ("; only problems the composed track did not already have are reported."
                 if assessment else "; no composed-track manifest, so every finding is reported.")
              + ("" if comparison is not None else
                 " Composed vs delivered loudness not compared: no composed measurement."))
    return {"findings": findings,
            "measurements": {"delivered": {k: delivered.get(k) for k in (
                "integrated_lufs", "true_peak_dbfs", "loudness_range_lu", "brightness_db",
                "measured_window")},
                "sample_peak_dbfs": max_volume,
                "loudness_vs_composed_lu": comparison},
            "method": method}


# --------------------------------------------------------------------------
# the report
# --------------------------------------------------------------------------

EVALUATIONS = (
    ("scene_intent", "Scene imagery vs creative intent", True),
    ("scene_depiction", "Scene depiction (local vision model)", False),
    ("visual_continuity", "Scene-to-scene continuity", True),
    ("rendered_continuity", "Rendered frames: flashes and seams", True),
    ("soundtrack_delivery", "Delivered soundtrack", True),
    ("soundtrack_dynamics", "Soundtrack dynamics: spikes, dropouts, drift", True),
    ("soundtrack_repetition", "Soundtrack repetition", True),
)


def cap_findings(findings, limit=MAX_LISTED):
    """List at most ``limit`` findings per code; count the rest in one line."""
    kept, extra = [], {}
    seen = {}
    for f in findings:
        seen[f["code"]] = seen.get(f["code"], 0) + 1
        if seen[f["code"]] <= limit:
            kept.append(f)
        else:
            extra.setdefault(f["code"], [f["severity"], 0])[1] += 1
    for code, (severity, n) in extra.items():
        kept.append(finding(code, severity, f"{n} further finding(s) of this kind not listed",
                            unlisted=n))
    return kept


def _status(findings):
    severities = {f["severity"] for f in findings}
    if "block" in severities:
        return DEFECT
    if "warn" in severities:
        return REVIEW
    return PASS


def _run(evaluation_id, title, required, fn):
    try:
        outcome = fn()
    except Exception as e:  # noqa: BLE001 - one evaluator must never sink the rest
        log.exception("quality evaluation %s failed", evaluation_id)
        outcome = {"status": UNAVAILABLE,
                   "reason": f"the evaluation raised {type(e).__name__}: {str(e)[:200]}"}
    findings = cap_findings(outcome.get("findings") or [])
    status = outcome.get("status") or _status(findings)
    summary = outcome.get("reason")
    if summary is None:
        counts = {s: sum(f.get("unlisted") or 1 for f in findings if f["severity"] == s)
                  for s in ("block", "warn", "info")}
        summary = ("nothing measurable is wrong" if status == PASS else
                   ", ".join(f"{n} {word}" for n, word in (
                       (counts["block"], "defect(s)"), (counts["warn"], "to review"),
                       (counts["info"], "note(s)")) if n))
        if status == PASS and counts["info"]:
            summary += f" ({counts['info']} note(s))"
    return {"id": evaluation_id, "title": title, "required": required, "status": status,
            "summary": summary, "method": outcome.get("method"),
            "findings": findings, "measurements": outcome.get("measurements") or {}}


def _video_fingerprint(video):
    video = Path(video) if video else None
    if not video or not video.is_file():
        return None
    stat = video.stat()
    return {"path": video.name, "size": stat.st_size, "mtime_ns": stat.st_mtime_ns,
            "sha256": _sha256(video)}


def _load_cache(pdir):
    path = Path(pdir) / "output" / CACHE_FILENAME
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return {}


def _save_cache(pdir, cache):
    if not cache:
        return
    path = Path(pdir) / "output" / CACHE_FILENAME
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(cache, indent=2) + "\n")


def assess(pdir, *, video_path=None, metadata=None, storyboard=None, audio_manifest=None,
           concept=None, spec=None, holds_one_frame=False, judge=None):
    """Every perceptual evaluation for one production. Never raises for a
    failed evaluation - that evaluation is UNAVAILABLE and says why."""
    pdir = Path(pdir)
    intent = intent_profile(metadata, storyboard, audio_manifest, concept, holds_one_frame)
    entries = timeline(pdir, storyboard, spec)
    images = _distinct_images(entries)
    measured = {str(i["path"]): measure_image(i["path"]) for i in images if i["path"].is_file()}
    master = (audio_manifest or {}).get("master") or {}
    head = float(master.get("fade_in_seconds") or 2.0)
    tail = float(master.get("fade_out_seconds") or 2.0)
    video = Path(video_path) if video_path else None
    scan_cache = {}

    def soundtrack():
        if "scan" not in scan_cache:
            scan_cache["scan"] = scan_soundtrack(video) if video and video.is_file() else None
        return scan_cache["scan"]

    def needs_scan(fn):
        def run():
            scan = soundtrack()
            if scan is None:
                return {"status": UNAVAILABLE,
                        "reason": "no rendered soundtrack could be decoded"}
            duration = scan["momentary"][-1][0] if scan["momentary"] else 0.0
            return fn(scan, duration)
        return run

    cache = _load_cache(pdir)
    plans = {
        "scene_intent": lambda: evaluate_scene_intent(images, intent, measured),
        "scene_depiction": lambda: evaluate_scene_depiction(
            images, judge if judge is not None else VisionJudge(), cache),
        "visual_continuity": lambda: evaluate_visual_continuity(entries, intent, measured),
        "rendered_continuity": lambda: evaluate_rendered_continuity(video, entries, intent),
        "soundtrack_delivery": lambda: evaluate_soundtrack_delivery(
            video, audio_manifest, intent, soundtrack(), head, tail),
        "soundtrack_dynamics": needs_scan(
            lambda scan, d: evaluate_soundtrack_dynamics(scan, intent, head, tail, d)),
        "soundtrack_repetition": needs_scan(
            lambda scan, d: evaluate_soundtrack_repetition(scan, head, tail, d)),
    }
    evaluations = [_run(eid, title, required, plans[eid]) for eid, title, required in EVALUATIONS]
    _save_cache(pdir, cache)

    counts = {s: sum(f.get("unlisted") or 1 for e in evaluations for f in e["findings"]
                     if f["severity"] == s)
              for s in ("block", "warn", "info")}
    unavailable = [{"id": e["id"], "title": e["title"], "required": e["required"],
                    "reason": e["summary"]} for e in evaluations if e["status"] == UNAVAILABLE]
    if any(e["status"] == DEFECT for e in evaluations):
        verdict = DEFECT
    elif any(e["status"] == REVIEW for e in evaluations):
        verdict = REVIEW
    elif any(u["required"] for u in unavailable):
        verdict = INCOMPLETE
    else:
        verdict = OK
    return {
        "quality_version": QUALITY_VERSION,
        "video_id": pdir.name,
        "generated_utc": utc_now(),
        "inputs": {"video": _video_fingerprint(video)},
        "intent": intent,
        "verdict": verdict,
        "counts": counts,
        "coverage": {
            "evaluated": [e["id"] for e in evaluations if e["status"] in (PASS, REVIEW, DEFECT)],
            "unavailable": unavailable,
            "not_applicable": [{"id": e["id"], "title": e["title"], "reason": e["summary"]}
                               for e in evaluations if e["status"] == NOT_APPLICABLE],
        },
        "evaluations": evaluations,
        "note": PASS_NOTE,
    }


def report_path(pdir):
    return Path(pdir) / "output" / REPORT_FILENAME


def write_report(pdir, report):
    path = report_path(pdir)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(report, indent=2) + "\n")
    tmp.replace(path)
    return path


def load_report(pdir):
    try:
        return json.loads(report_path(pdir).read_text())
    except (OSError, ValueError):
        return None


def is_current(report, video_path):
    """Whether a report describes the video on disk now."""
    recorded = (report or {}).get("inputs", {}).get("video")
    video = Path(video_path) if video_path else None
    if not recorded or not video or not video.is_file():
        return False
    stat = video.stat()
    if stat.st_size == recorded.get("size") and stat.st_mtime_ns == recorded.get("mtime_ns"):
        return True
    return _sha256(video) == recorded.get("sha256")


def summary(pdir, video_path):
    """The compact view status reports carry. ``assessed`` is False when no
    report exists - which is "not evaluated", never a pass."""
    report = load_report(pdir)
    if report is None:
        return {"assessed": False, "verdict": None, "current": None, "counts": None,
                "unavailable": []}
    return {"assessed": True, "verdict": report.get("verdict"),
            "current": is_current(report, video_path),
            "counts": report.get("counts"),
            "generated_utc": report.get("generated_utc"),
            "unavailable": [u["id"] for u in (report.get("coverage") or {}).get("unavailable", [])]}


def blockers(pdir, video_path):
    """Review-gate entries: objective defects only, from a current report.

    Taste-dependent findings never appear here - they inform the reviewer
    through the report instead. A report that no longer describes the video
    on disk is a blocker of its own, because its findings (or its silence)
    are about a different file.
    """
    report = load_report(pdir)
    if report is None:
        return []
    if not is_current(report, video_path):
        return ["perceptual quality assessment is stale (the video changed after it ran); "
                "re-run it (./content-machine quality <id>, or the Quality stage)"]
    out = []
    for evaluation in report.get("evaluations") or []:
        for f in evaluation.get("findings") or []:
            if f.get("severity") == "block":
                out.append(f"perceptual quality defect ({evaluation['title']}): {f['detail']}")
    return out
