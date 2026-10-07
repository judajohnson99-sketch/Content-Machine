#!/usr/bin/env python3
"""Sound design: the creative layer above ``scripts/audio.py``.

``creative.route_audio`` answers *where the audio may legally come from*
and what the gate should demand of it. This module answers the separate,
genuinely creative question: given that source, what does this video
actually sound like? Which supporting textures sit under it, what happens
occasionally so the listener is in a place rather than in front of a
signal, how it enters and leaves, how loud it sits, and what would make it
bad enough that nobody should ship it unattended.

Three properties are deliberate:

- **Niche-agnostic.** Nothing here knows what meditation is. The
  vocabulary is listening contexts (how the audio is heard) and a
  capability catalogue (what this build can synthesise). Sleep/ambient is
  simply the first concept to use it.
- **The catalogue is a closed set.** The design pass may only choose
  elements ``scripts/audio.py`` actually has. Anything else it asks for is
  recorded as unmet, never silently substituted - the same rule
  ``route_audio`` follows for music providers.
- **The rights decision is not re-litigated.** The bed comes from
  ``route_audio`` exactly as routed, licence and all. Design adds layers
  around it and never replaces or relabels it.
"""
import json
import logging
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import audio as audio_mod  # noqa: E402

logging.basicConfig(level=logging.INFO, format="[%(levelname)s] %(message)s")
log = logging.getLogger("sound_design")


class SoundDesignError(Exception):
    """Raised when a soundscape cannot be designed or trusted."""


# --------------------------------------------------------------------------
# listening context - how the audio is heard, not what it is about
# --------------------------------------------------------------------------

# Keyword -> context. Read against the concept's own words (niche, format,
# audio concept), never against a hard-coded list of niches. A concept that
# says nothing recognisable gets the neutral default rather than a guess.
_CONTEXT_KEYWORDS = (
    (("sleep", "insomnia", "bedtime", "night-time", "fall asleep", "lullaby",
      "nap", "deep rest", "asmr"), "background_sleep"),
    (("focus", "study", "concentration", "work session", "productivity",
      "reading", "deep work"), "background_focus"),
    (("meditation", "relax", "calm", "unwind", "ambient", "slow tv",
      "wallpaper", "background"), "ambient_watch"),
)


def infer_listening_context(concept, kind=None):
    """The listening context a concept implies, from its own language."""
    if kind == "narration":
        return "narration"
    text = " ".join(str(concept.get(key) or "") for key in (
        "niche", "content_format", "audio_concept", "target_audience",
        "publishing_format", "monetization_hypothesis")).lower()
    for keywords, context in _CONTEXT_KEYWORDS:
        if any(k in text for k in keywords):
            return context
    return audio_mod.DEFAULT_LISTENING_CONTEXT


# --------------------------------------------------------------------------
# the capability catalogue handed to the design pass
# --------------------------------------------------------------------------

# Gain ceilings per role, in dB relative to the bed. Supporting layers that
# come up to the bed's own level stop supporting it and start competing
# with it, and an "occasional" event loud enough to startle a sleeping
# listener is the single worst failure this format has.
ROLE_GAIN_LIMITS = {
    "bed": (-24.0, 0.0),
    "ambience": (-40.0, -8.0),
    "detail": (-42.0, -10.0),
}

# Events rarer than this read as a glitch rather than a feature; more often
# than this and a background listener starts noticing the pattern.
MIN_EVENT_INTERVAL_SECONDS = 8.0
MAX_EVENT_INTERVAL_SECONDS = 600.0

MAX_AMBIENCE_LAYERS = 3
MAX_DETAIL_LAYERS = 2


# What each element actually sounds like, in the words someone would use to
# ask for it. Offering bare slugs made the design pass record "low muffled
# thunder" as unavailable while `distant_thunder` sat in the catalogue - an
# unmet request that was really a naming mismatch.
ELEMENT_DESCRIPTIONS = {
    "room_tone": "the quiet presence of an empty indoor room",
    "wind_low": "low wind against a building, gusting slowly",
    "wind_high": "thinner wind through trees or gaps",
    "ocean_surf": "slow sea swell breaking at a distance",
    "stream": "running water over stones, close and steady",
    "fireplace": "a hearth fire, warm and irregular",
    "night_air": "faint high night-time air, almost inaudible",
    "distant_traffic": "muffled city traffic heard from indoors",
    "cabin_hum": "a deep mechanical hum, a boat or a building at rest",
    "snowfall": "dry falling snow, soft high-frequency hiss",
    "chime": "a bright struck chime with a ringing tail",
    "bell": "a low bell, long decay",
    "drip": "a single water drop landing",
    "bird": "one short bird call",
    "wood_creak": "a floorboard or beam settling",
    "crackle": "a sharp tick or crackle, as in a fire",
    "page_turn": "a soft paper or fabric rustle",
    "distant_thunder": "muffled thunder far away, low and rolling",
    "wind_gust": "a single long gust rising and falling",
}


def _catalogue_lines(names):
    return "; ".join(f"{name} ({ELEMENT_DESCRIPTIONS[name]})"
                     if name in ELEMENT_DESCRIPTIONS else name
                     for name in names)


def capability_catalogue():
    """Everything the design pass is allowed to ask for, as plain data.

    Generated from ``scripts/audio.py`` rather than restated, so a provider
    added there is offered here without a second edit - and an element
    removed there stops being offerable rather than becoming a silent
    render failure.
    """
    return {
        "ambience_elements": sorted(audio_mod.AMBIENCE_ELEMENTS),
        "event_elements": sorted(audio_mod.EVENT_ELEMENTS),
        "listening_contexts": sorted(audio_mod.LISTENING_CONTEXTS),
        "max_ambience_layers": MAX_AMBIENCE_LAYERS,
        "max_detail_layers": MAX_DETAIL_LAYERS,
    }


DESIGN_PROMPT_TEMPLATE = """You are the sound designer for a video. Reply with ONLY a JSON object, no markdown fences, no commentary.

Concept: {content_format}
Niche: {niche}
Audience: {target_audience}
Audio concept: {audio_concept}
Visual/creative direction: {visual_concept}
Mood: {mood}
Duration: {duration_description}
How it is listened to: {listening_context}
The bed is already chosen and licensed; do not replace it: {bed_description}
{research_block}
You may only use these synthesised elements - anything else does not exist in this build:
Ambience beds/textures: {ambience_elements}
Occasional one-shot events: {event_elements}

Design the soundscape around that bed. Decide:
- up to {max_ambience_layers} supporting ambience layers (each must add a distinct place-defining quality; none if the bed is complete on its own)
- up to {max_detail_layers} occasional event layers, each with an average interval in seconds between {min_interval:.0f} and {max_interval:.0f} (none if events would intrude)
- how the track enters and leaves (fade seconds), and whether the bed should breathe (a slow swell)
- gains in dB relative to the bed at 0 dB: ambience between {ambience_min:.0f} and {ambience_max:.0f}, events between {detail_min:.0f} and {detail_max:.0f}

Reason from how this is actually listened to. A track someone falls asleep to must never startle them and must not get brighter over time; a track watched attentively can carry more incident. Silence and restraint are legitimate answers - an empty "ambience" and "detail" list is a real design decision, not a failure.

Return a JSON object with exactly these keys:
- "intent": one sentence describing what this should feel like
- "listening_context": one of {listening_contexts}
- "ambience": a list of objects {{"element": <from the list above>, "gain_db": <number>, "reason": <short>}}
- "detail": a list of objects {{"element": <from the list above>, "every_seconds": <number>, "gain_db": <number>, "reason": <short>}}
- "bed": an object {{"gain_db": <number, usually 0>, "lowpass_hz": <number or null>, "highpass_hz": <number or null>, "width": <number 1.0-1.6>, "swell": {{"rate_hz": <number>, "depth": <0-1>}} or null}}
- "dynamics": an object {{"fade_in_seconds": <number>, "fade_out_seconds": <number>}}
- "wanted_but_unavailable": a list of short strings naming anything you would have used that is not in the lists above (may be empty)"""


def _duration_description(target_seconds):
    if not target_seconds:
        return "unknown"
    minutes = float(target_seconds) / 60.0
    if minutes >= 60:
        return f"{minutes / 60:.1f} hours ({int(minutes)} minutes)"
    return f"{minutes:.0f} minutes"


def _mock_design(concept, context, target_seconds):
    """The TEST_MODE reply: no network, deterministic, same shape as a real
    one. Small on purpose - the point is to exercise compilation, not to
    stand in for a design decision."""
    return {
        "intent": f"[MOCK design] {concept.get('audio_concept', 'a calm bed')}",
        "listening_context": context,
        "ambience": [{"element": "room_tone", "gain_db": -24.0,
                      "reason": "[MOCK] a room around the bed"}],
        "detail": [],
        "bed": {"gain_db": 0.0, "lowpass_hz": None, "highpass_hz": None,
                "width": 1.2, "swell": {"rate_hz": 0.02, "depth": 0.2}},
        "dynamics": {"fade_in_seconds": min(8.0, (target_seconds or 60) / 8),
                     "fade_out_seconds": min(12.0, (target_seconds or 60) / 6)},
        "wanted_but_unavailable": [],
    }


def _clamp(value, low, high, default):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    if number != number:
        return default
    return max(low, min(high, number))


def sanitize_design(design, target_seconds, catalogue=None):
    """Keep only what this build can actually render, and say what was dropped.

    A design pass that names an element we do not have has not failed
    creatively - it has asked for something real that this build cannot
    make. Recording that as an unmet request is more useful than either
    crashing or quietly swapping in the nearest thing we do have, which
    would be exactly the kind of invented capability the rest of this
    codebase refuses.
    """
    catalogue = catalogue or capability_catalogue()
    unmet = [str(x) for x in (design.get("wanted_but_unavailable") or [])]
    clean = {
        "intent": str(design.get("intent") or "").strip(),
        "listening_context": (design.get("listening_context")
                              if design.get("listening_context") in audio_mod.LISTENING_CONTEXTS
                              else audio_mod.DEFAULT_LISTENING_CONTEXT),
        "ambience": [],
        "detail": [],
    }

    lo, hi = ROLE_GAIN_LIMITS["ambience"]
    for entry in (design.get("ambience") or [])[:MAX_AMBIENCE_LAYERS]:
        if not isinstance(entry, dict):
            continue
        element = str(entry.get("element", ""))
        if element not in audio_mod.AMBIENCE_ELEMENTS:
            unmet.append(f"ambience element {element!r} (not in this build)")
            continue
        clean["ambience"].append({
            "element": element,
            "gain_db": _clamp(entry.get("gain_db"), lo, hi, -24.0),
            "reason": str(entry.get("reason") or "").strip(),
        })

    lo, hi = ROLE_GAIN_LIMITS["detail"]
    for entry in (design.get("detail") or [])[:MAX_DETAIL_LAYERS]:
        if not isinstance(entry, dict):
            continue
        element = str(entry.get("element", ""))
        if element not in audio_mod.EVENT_ELEMENTS:
            unmet.append(f"event element {element!r} (not in this build)")
            continue
        clean["detail"].append({
            "element": element,
            "every_seconds": _clamp(entry.get("every_seconds"),
                                    MIN_EVENT_INTERVAL_SECONDS,
                                    MAX_EVENT_INTERVAL_SECONDS, 90.0),
            "gain_db": _clamp(entry.get("gain_db"), lo, hi, -26.0),
            "reason": str(entry.get("reason") or "").strip(),
        })

    bed = design.get("bed") if isinstance(design.get("bed"), dict) else {}
    bed_lo, bed_hi = ROLE_GAIN_LIMITS["bed"]
    swell = bed.get("swell") if isinstance(bed.get("swell"), dict) else None
    clean["bed"] = {
        "gain_db": _clamp(bed.get("gain_db"), bed_lo, bed_hi, 0.0),
        "highpass_hz": _clamp(bed.get("highpass_hz"), 0.0, 400.0, 0.0) if bed.get("highpass_hz") else 0.0,
        "lowpass_hz": _clamp(bed.get("lowpass_hz"), 200.0, 20000.0, 0.0) if bed.get("lowpass_hz") else 0.0,
        "width": _clamp(bed.get("width"), 1.0, 1.6, 1.0),
        "swell": ({"rate_hz": _clamp(swell.get("rate_hz"), 0.005, 0.5, 0.02),
                   "depth": _clamp(swell.get("depth"), 0.0, 0.6, 0.2)}
                  if swell else None),
    }

    # Fades scale with the piece: a four-hour bed that slams in over half a
    # second is as wrong as a ninety-second clip that takes a minute to
    # arrive. Both ends are capped at a sixth of the track either way.
    ceiling = max((target_seconds or 60.0) / 6.0, 1.0)
    dynamics = design.get("dynamics") if isinstance(design.get("dynamics"), dict) else {}
    clean["dynamics"] = {
        "fade_in_seconds": _clamp(dynamics.get("fade_in_seconds"), 0.0, ceiling,
                                  min(6.0, ceiling)),
        "fade_out_seconds": _clamp(dynamics.get("fade_out_seconds"), 0.0, ceiling,
                                   min(10.0, ceiling)),
    }
    clean["wanted_but_unavailable"] = unmet
    return clean


def design_soundscape(concept, target_seconds, kind=None, mood=None,
                      bed_description="", research_block="", llm=None):
    """One LLM call producing this video's sound design document.

    One call per video, not per layer or per scene - the same economy rule
    the scene-motif pass follows. ``llm`` is injectable so callers (and
    tests) are not forced through a network provider.
    """
    context = infer_listening_context(concept, kind)
    if os.environ.get("TEST_MODE") == "1":
        return sanitize_design(_mock_design(concept, context, target_seconds),
                               target_seconds)

    catalogue = capability_catalogue()
    amb_lo, amb_hi = ROLE_GAIN_LIMITS["ambience"]
    det_lo, det_hi = ROLE_GAIN_LIMITS["detail"]
    prompt = DESIGN_PROMPT_TEMPLATE.format(
        content_format=concept.get("content_format", ""),
        niche=concept.get("niche", ""),
        target_audience=concept.get("target_audience", ""),
        audio_concept=concept.get("audio_concept", ""),
        visual_concept=concept.get("visual_concept", ""),
        mood=mood or "(none given)",
        duration_description=_duration_description(target_seconds),
        listening_context=context,
        bed_description=bed_description or "(an already-chosen bed layer)",
        research_block=research_block or "",
        ambience_elements=_catalogue_lines(catalogue["ambience_elements"]),
        event_elements=_catalogue_lines(catalogue["event_elements"]),
        listening_contexts=", ".join(catalogue["listening_contexts"]),
        max_ambience_layers=MAX_AMBIENCE_LAYERS,
        max_detail_layers=MAX_DETAIL_LAYERS,
        min_interval=MIN_EVENT_INTERVAL_SECONDS,
        max_interval=MAX_EVENT_INTERVAL_SECONDS,
        ambience_min=amb_lo, ambience_max=amb_hi,
        detail_min=det_lo, detail_max=det_hi,
    )

    if llm is None:
        import creative as creative_mod
        provider = os.environ.get("LLM_PROVIDER", "anthropic").lower()

        def llm(text):
            return creative_mod._call_llm(provider, text)

    text = llm(prompt)
    try:
        import creative as creative_mod
        reply = creative_mod._extract_json(text)
    except Exception as exc:  # noqa: BLE001 - reported, never swallowed
        raise SoundDesignError(f"sound design reply was not usable JSON: {exc}") from exc
    design = sanitize_design(reply, target_seconds)
    design["listening_context"] = design.get("listening_context") or context
    return design


# --------------------------------------------------------------------------
# compilation - design document + routed bed -> an executable audio plan
# --------------------------------------------------------------------------

BED_LAYER_ID = "bed"


def compile_soundscape(design, routed_composition, target_seconds):
    """Merge a design document into the routed composition.

    Deterministic and side-effect free. The routed layers keep their
    provider, params and licence untouched - this only shapes them and adds
    layers around them, so no design decision can turn a rights-checked
    source into a different one.
    """
    if not routed_composition or not routed_composition.get("layers"):
        return None
    design = design or {}
    plan = {k: v for k, v in routed_composition.items() if k != "layers"}
    plan["target_seconds"] = target_seconds or routed_composition.get("target_seconds")

    bed_shape = design.get("bed") or {}
    dynamics = design.get("dynamics") or {}
    fade_in = float(dynamics.get("fade_in_seconds", 0) or 0)
    fade_out = float(dynamics.get("fade_out_seconds", 0) or 0)

    layers = []
    for index, original in enumerate(routed_composition["layers"]):
        layer = dict(original)
        is_bed = index == 0 and layer.get("provider") != "tts"
        if is_bed:
            layer["gain_db"] = float(layer.get("gain_db", 0) or 0) + float(bed_shape.get("gain_db", 0) or 0)
            for key in ("highpass_hz", "lowpass_hz"):
                if bed_shape.get(key):
                    layer[key] = float(bed_shape[key])
            if bed_shape.get("width"):
                layer["width"] = float(bed_shape["width"])
            if bed_shape.get("swell"):
                layer["swell"] = dict(bed_shape["swell"])
            if fade_in:
                layer["fade_in_seconds"] = fade_in
            if fade_out:
                layer["fade_out_seconds"] = fade_out
        layers.append(layer)

    narration_id = next((l.get("id") for l in layers if l.get("provider") == "tts"), None)

    for n, entry in enumerate(design.get("ambience") or []):
        layer = {
            "id": f"ambience{n + 1}",
            "provider": "ambience",
            "params": {"element": entry["element"]},
            "gain_db": float(entry.get("gain_db", -24.0)),
            "width": 1.3,
            "fade_in_seconds": fade_in,
            "fade_out_seconds": fade_out,
        }
        if narration_id:
            # Narration is the content; everything under it steps aside for
            # it rather than being mixed so low it stops being audible.
            layer["duck_under"] = narration_id
        layers.append(layer)

    for n, entry in enumerate(design.get("detail") or []):
        layer = {
            "id": f"detail{n + 1}",
            "provider": "events",
            "params": {"element": entry["element"],
                       "every_seconds": float(entry.get("every_seconds", 90.0))},
            "gain_db": float(entry.get("gain_db", -26.0)),
            "fade_in_seconds": fade_in,
            "fade_out_seconds": fade_out,
        }
        if narration_id:
            layer["duck_under"] = narration_id
        layers.append(layer)

    plan["layers"] = layers
    # The gains above are a mix balance. They only are one if every layer
    # starts from the same level first - see audio.stage_gain.
    plan["gain_staging"] = True
    context = design.get("listening_context") or audio_mod.DEFAULT_LISTENING_CONTEXT
    criteria = audio_mod.LISTENING_CONTEXTS.get(context, {})
    plan["listening_context"] = context
    plan["master"] = {
        "normalize": True,
        "lufs": criteria.get("integrated_lufs_target", audio_mod.DEFAULT_LUFS),
        "true_peak_dbfs": criteria.get("true_peak_ceiling_dbfs", -1.5),
        "loudness_range_lu": criteria.get("max_loudness_range_lu", 11.0),
        # Nothing below ~20 Hz is audible on the devices this is watched on;
        # leaving it in only eats headroom the limiter then gives away.
        "highpass_hz": 20.0,
        "limiter": True,
        "fade_in_seconds": fade_in,
        "fade_out_seconds": fade_out,
    }
    return plan


def main():
    import argparse
    parser = argparse.ArgumentParser(
        description="Show the sound-design capability catalogue.")
    parser.add_argument("--catalogue", action="store_true")
    args = parser.parse_args()
    if args.catalogue:
        print(json.dumps(capability_catalogue(), indent=2))
        return 0
    parser.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
