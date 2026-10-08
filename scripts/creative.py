#!/usr/bin/env python3
"""Creative brief: title, script, image direction and audio requirements.

    ./content-machine creative <video-id>

Two independent pieces, kept separate because only one of them is genuinely
creative:

- ``build_audio_composition`` maps a concept's ``audio_source_requirement``
  onto the providers ``scripts/audio.py`` already has (noise/tone/rain/tts).
  Deterministic, no network, no LLM - the mapping is exactly what
  ``AUDIO_CAPABILITY`` in ``scripts/experiment.py`` already documents as
  available today. A requirement with no synthesisable substitute
  (``music_generation_*``) returns ``None`` rather than fabricating one.
- ``generate_brief`` asks an LLM for the parts that are actually creative:
  title, narration script, description, and image direction. It follows the
  provider pattern ``generate.py`` already demonstrates (``LLM_PROVIDER``,
  ``TEST_MODE``) rather than inventing a second one.

Neither function touches ``provenance.images.production_grade`` or any other
gate input - the publication gate is unaffected by anything in this module.
"""
import importlib.util
import json
import logging
import os
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import audio as audio_mod  # noqa: E402
import research as research_mod  # noqa: E402
import visual_direction as vd  # noqa: E402

logging.basicConfig(level=logging.INFO, format="[%(levelname)s] %(message)s")
log = logging.getLogger("creative")

ROOT = Path(__file__).resolve().parent.parent


class CreativeError(Exception):
    """Raised when a creative brief cannot be produced or trusted."""


# --------------------------------------------------------------------------
# audio requirements -> an executable composition (deterministic, no LLM)
# --------------------------------------------------------------------------

# Ordered so the first matching keyword wins; checked against the concept's
# own `audio_concept` free-text description, not invented separately.
_SYNTHESISABLE_KEYWORDS = (
    # Brown is checked before pink: the one concept that mentions both
    # ("brown/pink noise") is titled and IDed around brown, so brown must
    # win a tie rather than whichever keyword happens to sort first.
    (("brown noise", "brown"), {"provider": "noise", "params": {"color": "brown"}}),
    (("pink noise", "pink"), {"provider": "noise", "params": {"color": "pink"}}),
    (("white noise", "white", "womb", "shush"), {"provider": "noise", "params": {"color": "white"}}),
    (("drone", "oscillator", "hum", "tone"), {"provider": "tone", "params": {"frequency": 110.0, "lowpass_hz": 400.0}}),
    (("rain",), {"provider": "rain", "params": {"intensity": "steady"}}),
)
_DEFAULT_SYNTHESISABLE = {"provider": "noise", "params": {"color": "brown"}}

# What AUDIO_CAPABILITY (experiment.py) calls PARTIAL: no licensed recording
# exists yet, but the rain bed is documented there as good enough to test the
# format with. Rights-clean because it is synthesised, not because it is a
# substitute for the real thing - the manifest still records it honestly.
_PARTIAL_LAYER = {"provider": "rain", "params": {"intensity": "steady"}}

# BLOCKED in AUDIO_CAPABILITY: no music-generation provider exists in this
# codebase. Returning a fake substitute here would be exactly the kind of
# invented capability the knowledge/doctrine layer forbids.
_BLOCKED_REQUIREMENTS = {"music_generation_or_licensed", "music_generation_plus_mixing"}

# A mood word (from the creative brief's audio_mood, or failing that the
# research findings' own audio-topic language) picks a chord colour for
# audio.py's "pad" provider. Small and deliberately not exhaustive - a
# richer synthesiser is a future provider, not this mapping's job.
_MOOD_CHORDS = (
    (("cozy", "warm", "comfort", "snug"), "warm"),
    (("calm", "gentle", "soft", "soothing", "peaceful", "relax"), "calm"),
    (("dream", "float", "airy", "weightless"), "dreamy"),
    (("mysterious", "curious", "wonder", "enigmatic"), "mysterious"),
    (("eerie", "unsettling", "tense", "suspense", "ominous"), "eerie"),
    (("melancholy", "wistful", "somber", "sombre", "sad", "bittersweet"), "melancholy"),
    (("cinematic", "epic", "grand", "sweeping"), "cinematic"),
)


# Below this many target seconds a pad-led bed stays on one root the whole
# way through, same as any short synthesisable_now clip; at or above it
# (long-form ambience is the whole point of a multi-hour "leave it on"
# video) the bed gets audio.py's slow root drift so it is not frozen on one
# exact frequency for hours.
PAD_LONGFORM_SECONDS = 1800.0


def _resolve_mood_chord(mood, findings):
    """The chord name implied by an explicit mood, or (failing that) the
    research findings' own recurring audio-topic language. ``None`` when
    there is nothing to go on - a project with neither gets no pad at all.
    """
    text = (mood or "").strip().lower()
    if not text and findings:
        digest = research_mod.findings_digest(findings, max_per_topic=5)
        text = " ".join(
            line for line in digest.splitlines() if line.startswith("- [audio")).lower()
    if not text:
        return None
    for keywords, name in _MOOD_CHORDS:
        if any(k in text for k in keywords):
            return name
    return "calm"


# Where a long-form pad's root drifts to and back: tonic, a perfect fourth
# up, tonic, a perfect fourth down. The chord shape is unchanged throughout,
# so every step is consonant - movement, never a harmonic surprise.
PAD_DRIFT_RATIOS = (1.0, 4 / 3, 1.0, 3 / 4)


def _mood_pad_layer(mood, findings, target_seconds=None):
    """A gentle harmonic pad layer chosen from the brief's audio_mood, or
    the research findings' own recurring audio-topic language when no mood
    was given. ``None`` when there is nothing to go on - a project with
    neither gets no second layer, exactly as before either existed.

    On a long-form texture bed the pad drifts (``root_drift_ratios``)
    rather than holding one exact frequency for hours; audio.py's
    ``provider_pad`` does the crossfading.
    """
    chord = _resolve_mood_chord(mood, findings)
    if chord is None:
        return None
    params = {"root_hz": 98.0, "chord": chord}
    if target_seconds and target_seconds >= PAD_LONGFORM_SECONDS:
        params["root_drift_ratios"] = list(PAD_DRIFT_RATIOS)
    return {"id": "pad", "provider": "pad", "params": params,
           "gain_db": -14.0, "fade_in_seconds": 3.0, "fade_out_seconds": 3.0}


# Words in a concept's own description that mean "this video's audio is
# music", as distinct from a texture bed. Checked against the concept's
# audio_concept, working title and format description - never invented.
_MUSIC_WORDS = ("music", "melody", "melodic", "piano", "lullaby", "lofi",
                "lo-fi", "song", "harp", "guitar", "strings", "score",
                "soundtrack", "instrumental", "musical")


def _audio_kind(concept, mood, findings):
    """What kind of audio this video actually needs, and why.

    The distinction the routing turns on: a *texture* (brown noise, rain,
    a drone) is the product itself for the niches that ship it, so
    synthesising it is not a compromise. *Music* is not - a synthesiser
    standing in for music is a stand-in, and the rest of this module is
    careful never to pretend otherwise.
    """
    requirement = concept.get("audio_source_requirement")
    if requirement == "tts_required":
        return "narration", "the concept is narrated; speech is the primary audio"
    if requirement in _BLOCKED_REQUIREMENTS:
        return "music", f"the concept declares audio_source_requirement={requirement!r}"

    text = " ".join(str(concept.get(field) or "") for field in
                    ("audio_concept", "working_title_pattern", "content_format",
                     "visual_concept")).lower()
    named = [w for w in _MUSIC_WORDS if w in text]
    if named:
        return "music", (f"the concept describes its own audio as music "
                         f"({', '.join(sorted(set(named))[:3])})")
    for keywords, _layer in _SYNTHESISABLE_KEYWORDS:
        if any(k in (concept.get("audio_concept") or "").lower() for k in keywords):
            return "texture", (f"the concept names a specific texture "
                               f"({keywords[0]}), which is the product itself")
    if _resolve_mood_chord(mood, findings):
        return "music", ("the concept names no texture, and the creative brief "
                         "(or the project's research) describes the audio in "
                         "musical terms")
    return "texture", "no musical or textural signal; the default bed applies"


def _music_sources(concept, mood, tags, target_seconds):
    """Every way this build could produce music, best first, with why.

    Recorded in full - including the options that are *not* available and
    the reason - so "we used a synthesiser" is visibly a routing outcome
    with alternatives, not the only thing the code knows how to do.
    """
    requirement = concept.get("audio_source_requirement")
    track = audio_mod.select_library_track(mood=mood, tags=tags)
    sources = [{
        "source": "licensed-library",
        "provider": "file",
        "available": track is not None,
        "production_grade_capable": True,
        "rights": ("declared per track in assets/music/library.json"
                   if track else "n/a"),
        "cost": "already licensed",
        "detail": (f"library track {track['id']!r} ({track.get('license')})" if track
                   else "no rights-declared track in assets/music/library.json; "
                        "drop licensed music there and it is preferred over "
                        "anything synthesised"),
    }, {
        "source": "music-generation-api",
        "provider": None,
        "available": False,
        "production_grade_capable": True,
        "rights": "depends on the vendor's terms; must be established before use",
        "cost": "paid, per generated minute",
        "detail": "no music-generation provider is configured in this build; "
                  "adding one is an account decision, and nothing here "
                  "substitutes for it silently",
    }, {
        "source": "generative-music",
        "provider": "music",
        "available": requirement not in _BLOCKED_REQUIREMENTS,
        # The honest ceiling of this path. Synthesised ambient music is a
        # real composition technique, but nothing in this codebase can
        # establish that its output is music someone would choose to
        # listen to - so it never claims to be production-grade, and the
        # review gate holds it until a human has actually listened.
        "production_grade_capable": False,
        "rights": "generated-original; no third-party interest",
        "cost": "none (local ffmpeg synthesis)",
        "detail": ("synthesised ambient music: a resolving chord progression "
                   "with phasing bell voices and reverb"
                   if requirement not in _BLOCKED_REQUIREMENTS else
                   f"refused for audio_source_requirement={requirement!r}: that "
                   "requirement says this concept needs generated or licensed "
                   "music, and a local synthesiser is not a substitute for it"),
    }]
    return sources, track


def route_audio(concept, target_seconds, narration_text=None, mood=None,
                findings=None):
    """Decide what audio this video needs and where it should come from.

    Returns ``{"composition": ..., "direction": ...}``. The composition is
    the executable plan (``None`` when nothing available can honestly
    satisfy the requirement); the direction is the decision record - what
    kind of audio the concept calls for, which sources were considered,
    which was chosen, its rights and cost, and whether that source is even
    capable of being production-grade. ``scripts/project.py`` gates review
    on that last field, which is what keeps "we synthesised something"
    from passing as "the music is good".
    """
    kind, why = _audio_kind(concept, mood, findings)
    requirement = concept.get("audio_source_requirement")
    fade = min(5.0, target_seconds / 6) if target_seconds else 0.0
    direction = {
        "kind": kind,
        "kind_reasoning": why,
        "audio_source_requirement": requirement,
        "target_seconds": target_seconds,
        "considered": [],
        "chosen": None,
        "decided_utc": audio_mod.utc_now(),
    }

    if kind == "narration":
        text = (narration_text or "").strip()
        if not text:
            direction["chosen"] = {
                "source": "none", "available": False,
                "detail": "narrated concept with no script yet"}
            return {"composition": None, "direction": direction}
        direction["chosen"] = {
            "source": "tts", "provider": "tts", "production_grade_capable": True,
            "rights": "voice licence recorded per layer at render time",
            "cost": "none (local Piper)",
            "detail": "the concept is narrated; the script is the audio"}
        return {"composition": {
            "target_seconds": target_seconds,
            "layers": [{"id": "narration", "provider": "tts",
                       "params": {"text": text}, "gain_db": 0.0}],
        }, "direction": direction}

    if kind == "music":
        chord = _resolve_mood_chord(mood, findings) or "calm"
        tags = [t for t in (concept.get("niche"), chord) if t]
        sources, track = _music_sources(concept, chord, tags, target_seconds)
        direction["considered"] = sources
        chosen = next((s for s in sources if s["available"]), None)
        if chosen is None:
            direction["chosen"] = None
            direction["blocked_reason"] = (
                f"no available music source for audio_source_requirement="
                f"{requirement!r}. Supply a rights-declared track in "
                "assets/music/library.json, or configure a music-generation "
                "provider.")
            return {"composition": None, "direction": direction}
        direction["chosen"] = chosen
        if chosen["source"] == "licensed-library":
            params = {
                "path": track["path"],
                "license": {
                    "source": track["source"],
                    "creator": track.get("creator"),
                    "license": track["license"],
                    "commercial_use": bool(track["commercial_use"]),
                    "attribution_required": bool(track.get("attribution_required", False)),
                    "attribution_text": track.get("attribution_text"),
                    "evidence": track.get("evidence",
                                          "Declared in assets/music/library.json."),
                },
                "crossfade_loop_seconds": float(track.get("crossfade_loop_seconds", 6.0)),
            }
            layer = {"id": "bed", "provider": "file", "params": params,
                     "gain_db": 0.0, "fade_in_seconds": fade, "fade_out_seconds": fade}
        else:
            params = {"root_hz": 98.0, "mood": chord}
            if target_seconds and target_seconds >= PAD_LONGFORM_SECONDS:
                # A multi-hour listen gets a longer cycle before it repeats.
                params["chord_seconds"] = 60.0
            layer = {"id": "bed", "provider": "music", "params": params,
                     "gain_db": 0.0, "fade_in_seconds": fade, "fade_out_seconds": fade}
        return {"composition": {"target_seconds": target_seconds, "layers": [layer]},
                "direction": direction}

    # kind == "texture": a synthesised bed is the product, not a stand-in.
    if requirement in ("licensed_or_recorded", "licensed_or_recorded_plus_mixing"):
        direction["chosen"] = {
            "source": "procedural-texture", "provider": _PARTIAL_LAYER["provider"],
            "available": True, "production_grade_capable": True,
            "rights": "generated-original; no third-party interest",
            "cost": "none (local ffmpeg synthesis)",
            "detail": "AUDIO_CAPABILITY calls this requirement PARTIAL: no "
                      "licensed recording exists yet, and the synthesised bed "
                      "is documented there as good enough to test the format",
        }
        return {"composition": {
            "target_seconds": target_seconds,
            "layers": [dict(_PARTIAL_LAYER, id="bed", gain_db=0.0)],
        }, "direction": direction}

    text = (concept.get("audio_concept") or "").lower()
    chosen_layer = None
    for keywords, layer in _SYNTHESISABLE_KEYWORDS:
        if any(k in text for k in keywords):
            chosen_layer = layer
            break
    layers = [dict(chosen_layer or _DEFAULT_SYNTHESISABLE, id="bed", gain_db=0.0,
                   fade_in_seconds=fade, fade_out_seconds=fade)]
    pad = _mood_pad_layer(mood, findings, target_seconds)
    if pad:
        layers.append(pad)
    direction["chosen"] = {
        "source": "procedural-texture", "provider": layers[0]["provider"],
        "available": True, "production_grade_capable": True,
        "rights": "generated-original; no third-party interest",
        "cost": "none (local ffmpeg synthesis)",
        "detail": ("the texture this concept names is synthesised exactly, "
                   "not approximated - it is the product"
                   if chosen_layer else
                   "no texture named and nothing musical to go on; the "
                   "default brown-noise bed applies"),
    }
    return {"composition": {"target_seconds": target_seconds, "layers": layers},
            "direction": direction}


def build_audio_composition(concept, target_seconds, narration_text=None,
                            mood=None, findings=None):
    """The executable ``audio_plan.composition`` for a concept, or None.

    Thin wrapper over ``route_audio`` for callers that only want the plan.
    None still means "nothing available can honestly satisfy this
    requirement" - the caller must not invent a layer for it.
    """
    return route_audio(concept, target_seconds, narration_text=narration_text,
                       mood=mood, findings=findings)["composition"]


# --------------------------------------------------------------------------
# image direction -> a procedural style (deterministic, no LLM)
# --------------------------------------------------------------------------

# scripts/make_visuals.STYLES, duplicated as plain strings rather than
# imported, so this module stays import-order independent of make_visuals.
_STYLE_KEYWORDS = (
    (("storm", "slate", "grey", "gray"), "storm-slate"),
    (("warm", "ember", "fire", "amber"), "warm-ember"),
    (("forest", "green", "nature", "leaf"), "muted-forest"),
    (("archive", "dust", "sepia", "old"), "dust-archive"),
)
_DEFAULT_STYLE = "deep-night"


def pick_procedural_style(concept):
    """The procedural fallback style implied by a concept's visual concept."""
    text = (concept.get("visual_concept") or "").lower()
    for keywords, style in _STYLE_KEYWORDS:
        if any(k in text for k in keywords):
            return style
    return _DEFAULT_STYLE


# --------------------------------------------------------------------------
# the creative brief (LLM) - title, script, description, image direction
# --------------------------------------------------------------------------

PROMPT_TEMPLATE = """You are producing a YouTube video for this concept. Reply with ONLY a JSON object, no markdown fences, no commentary.

Concept: {content_format}
Niche: {niche}
Target audience: {target_audience}
Working title pattern: {working_title_pattern}
Visual direction: {visual_concept}{direction_section}
Audio direction: {audio_concept}
Monetization hypothesis: {monetization_hypothesis}
Target length: {minutes:.0f} minutes
{facts_section}{research_section}
Return a JSON object with exactly these keys:
- "title": a concrete YouTube title under 100 characters, following the working title pattern's spirit
- "description": a 2-4 sentence YouTube description, plain language, no hashtag spam
- "narration_script": spoken narration text if this concept calls for narration, otherwise an empty string. Flat and calm where the concept asks for monotony; never mention it is AI-generated.
- "image_prompt": a text-to-image prompt capturing the visual direction, suitable for a diffusion model. Where a palette, motifs and things to avoid are given, the prompt must use that palette and those motifs and must not include anything listed to avoid.
- "negative_prompt": what to exclude from the image (e.g. text, watermarks, people, if not wanted)
- "audio_mood": 3-8 words describing the intended sonic atmosphere (e.g. "cozy, distant rain, slow warm pad") - grounded in the audio direction and, where present, the research below. Never mention specific providers or file formats.

Do not claim the video is professionally produced or hand-made. Do not invent facts about the audience or channel. Where research findings are given below, an "observation" is sourced and may inform the brief; an "interpretation" is this project's own pattern-count across sourced observations, not a fact about any single source - do not present either as something you personally verified."""

RESEARCH_SECTION_TEMPLATE = """
Research brief for this project - creative intent, preferences and (if research has run) sourced findings about what works in this niche. Use it to shape title, script tone, image direction and audio_mood; never treat "dislikes" as something to depict or narrate favourably:
Creative intent: {creative_intent}
Likes: {likes}
Dislikes: {dislikes}
{findings_block}
"""

# Appended into PROMPT_TEMPLATE only for concepts flagged
# requires_subject_research=true. The facts are the only claims the
# narration may make about the subject - the model is told so explicitly,
# because "here are some facts" without that instruction is not a
# meaningful constraint on an LLM that already thinks it knows the topic.
SOURCED_FACTS_BLOCK_TEMPLATE = """
Sourced facts about the subject (from {source_count} source(s) via {provider}). The narration script may state ONLY facts drawn from this list - do not add historical claims, dates, or figures that are not present here:
{facts_lines}
"""


def direction_section(concept):
    """The concept's curated visual direction, as prompt guidance.

    Reference concepts carry a palette, motifs and an avoid-list (a channel
    preference recorded on the concept, never a pipeline rule); when
    present they are handed to the model verbatim so the image prompt it
    writes stays inside that vocabulary.
    """
    direction = concept.get("visual_direction") or {}
    if not direction:
        return ""
    lines = []
    for key, label in (("atmosphere", "Atmosphere"), ("palette", "Palette"),
                       ("motifs", "Motifs"), ("avoid", "Avoid")):
        value = direction.get(key)
        if isinstance(value, (list, tuple)):
            value = ", ".join(str(v) for v in value)
        if value:
            lines.append(f"{label}: {value}")
    return ("\n" + "\n".join(lines)) if lines else ""


def research_section(brief=None, findings=None):
    """The operator's research brief plus a compact findings digest, as
    prompt guidance - or "" when there is neither, so a project with no
    brief prompts exactly as it did before this existed.
    """
    if not brief and not findings:
        return ""
    brief = brief or {}
    digest = research_mod.findings_digest(findings) if findings else ""
    findings_block = (
        f"Sourced/interpreted findings (see the [topic/kind] tag on each line):\n{digest}"
        if digest else "No competitor research findings yet."
    )
    return RESEARCH_SECTION_TEMPLATE.format(
        creative_intent=brief.get("creative_intent") or "(none given)",
        likes=", ".join(brief.get("likes") or []) or "(none given)",
        dislikes=", ".join(brief.get("dislikes") or []) or "(none given)",
        findings_block=findings_block,
    )


def _mock_brief(concept):
    """The TEST_MODE reply: no network, deterministic, same shape as a real
    one. Mirrors generate.py's own TEST_MODE convention."""
    direction = concept.get("visual_direction") or {}
    return {
        "title": f"[MOCK] {concept.get('working_title_pattern', concept.get('id', 'Untitled'))}",
        "description": f"[MOCK] {concept.get('content_format', '')}".strip() or "[MOCK] description",
        "narration_script": (
            f"[MOCK narration] {concept.get('audio_concept', '')}"
            if concept.get("audio_source_requirement") == "tts_required" else ""
        ),
        "image_prompt": direction.get("prompt_core") or concept.get("visual_concept", "abstract calm backdrop"),
        "negative_prompt": direction.get("negative") or "text, watermark, logo, people, faces",
        "audio_mood": "[MOCK] calm, steady, unobtrusive",
    }


def _extract_json(text):
    """Models sometimes wrap JSON in a fence despite instructions not to."""
    match = re.search(r"\{.*\}", text, re.S)
    if not match:
        raise CreativeError(f"no JSON object found in LLM reply: {text[:200]!r}")
    return json.loads(match.group(0))


# --------------------------------------------------------------------------
# LLM call - anthropic/google-genai live in .venv, same as Piper's dependency
# (audio.py's _piper_python()). The render/generation path stays
# standard-library-only; content authoring is allowed the venv's own SDKs,
# reached the same way Piper already is: shell out rather than require them
# on the system interpreter that runs project.py.
# --------------------------------------------------------------------------

_ANTHROPIC_SNIPPET = """
import os, sys
from dotenv import load_dotenv
load_dotenv()
from anthropic import Anthropic
client = Anthropic()
message = client.messages.create(
    model=os.environ.get("CREATIVE_MODEL", "claude-sonnet-4-5"),
    max_tokens=1024,
    messages=[{"role": "user", "content": sys.stdin.read()}],
)
sys.stdout.write(message.content[0].text)
"""

_GEMINI_SNIPPET = """
import os, sys
from dotenv import load_dotenv
load_dotenv()
from google import genai
client = genai.Client()
response = client.models.generate_content(
    model=os.environ.get("CREATIVE_MODEL", "gemini-3.5-flash-lite"),
    contents=sys.stdin.read(),
)
sys.stdout.write(response.text)
"""


def _sdk_python(provider):
    """A Python interpreter that has the SDK this provider needs.

    Checked in place first (works if it's ever installed system-wide or in
    whatever interpreter is already running this process); falls back to
    the project's own .venv, exactly where Piper's dependency lives.
    """
    module = "google.genai" if provider == "gemini" else "anthropic"
    try:
        # find_spec raises rather than returning None when a dotted name's
        # parent package (here, "google") isn't importable at all - a plain
        # lookup failure, not a real error, so it's treated as "not found".
        found = importlib.util.find_spec(module) is not None
    except ModuleNotFoundError:
        found = False
    if found:
        return sys.executable
    venv_python = ROOT / ".venv" / "bin" / "python3"
    return str(venv_python) if venv_python.is_file() else None


def _call_llm(provider, prompt):
    python = _sdk_python(provider)
    if not python:
        raise CreativeError(
            f"no Python interpreter has the {provider} SDK installed "
            f"(checked {sys.executable} and .venv/bin/python3)"
        )
    snippet = _GEMINI_SNIPPET if provider == "gemini" else _ANTHROPIC_SNIPPET
    try:
        result = subprocess.run(
            [python, "-c", snippet], input=prompt, capture_output=True,
            text=True, cwd=str(ROOT), timeout=120,
        )
    except subprocess.TimeoutExpired as e:
        raise CreativeError(f"LLM call timed out ({provider})") from e
    if result.returncode != 0:
        # The last line of stderr is the exception itself; the traceback
        # above it belongs in the log, not in a message an operator reads.
        lines = [line for line in result.stderr.strip().splitlines() if line.strip()]
        reason = lines[-1][-500:] if lines else f"exit {result.returncode}"
        log.error("LLM call failed (%s):\n%s", provider, result.stderr.strip()[-2000:])
        raise CreativeError(f"LLM call failed ({provider}): {reason}")
    return result.stdout


# --------------------------------------------------------------------------
# rules fallbacks - used only when no language model can be reached
# --------------------------------------------------------------------------
# A production must still reach review from the dashboard when the model
# provider is out of credit. These read the concept's own words and the
# research findings; they invent no facts and say how they were made.

_SCENERY_VANTAGES = (
    ("close", "a close, quiet detail of {thing}, {light}"),
    ("window", "{thing} seen through a rain-streaked window pane, {light}"),
    ("wide", "a wide, still view of {thing} under {light}"),
    ("low", "a low vantage near the ground looking across {thing}, {light}"),
    ("distant", "{thing} far in the distance, soft haze between, {light}"),
    ("interior", "a warm interior corner looking out toward {thing}, {light}"),
)
_NIGHT_WORDS = ("night", "sleep", "moon", "dark", "midnight", "evening", "dusk")


def _concept_words(concept):
    return " ".join(str(concept.get(k) or "") for k in (
        "visual_concept", "content_format", "creative_intent", "goal_text",
        "working_title_pattern")).lower()


def rules_visual_direction(concept, scene_count):
    """A visual direction document built from the concept's own words."""
    text = _concept_words(concept)
    night = any(w in text for w in _NIGHT_WORDS)
    rain = "rain" in text or "storm" in text
    mist = any(w in text for w in ("mist", "fog", "haze"))
    places = [w for w in ("cabin", "forest", "window", "lake", "ocean", "beach",
                          "mountain", "garden", "library", "room", "city", "meadow",
                          "river", "snow", "desert", "nebula", "space", "temple")
              if w in text] or ["a quiet landscape"]
    light = ("deep blue moonlight with a faint warm lamp glow" if night
             else "soft overcast daylight")
    subject = " and ".join(places[:2])
    if mist:
        subject = f"misty {subject}"
    if rain:
        subject = f"{subject} in gentle rain"
    count = max(1, min(len(_SCENERY_VANTAGES), 6, scene_count or 6))
    environments = [{
        "slug": slug,
        "description": template.format(thing=subject, light=light),
        "focal_point": places[0],
        "scale": slug,
    } for slug, template in _SCENERY_VANTAGES[:count]]
    return {
        "reasoning": f"rules: {count} vantages on the concept's own setting "
                     "(no language model was reachable)",
        "identity": {
            "palette": (["midnight blue", "slate grey", "amber", "deep pine green"]
                        if night else ["sage", "stone grey", "pale gold", "soft white"]),
            "lighting": light,
            "atmosphere": ("damp, hushed and still" if rain or mist else "calm and still"),
            "materials": ["weathered wood", "wet glass", "moss", "stone"][:3 if rain else 2],
            "continuity_anchors": places[:2],
            "camera": {"lens": "35mm", "perspective": "eye level",
                       "depth_of_field": "medium"},
            "render_intent": "natural light photography, gentle film grain",
        },
        "environments": environments,
        "avoid": ["text", "people", "faces", "bright flashes"],
    }


def rules_brief(concept, target_seconds, findings=None, reason=""):
    """A creative brief for an un-narrated video, from the concept itself."""
    direction = concept.get("visual_direction") or {}
    minutes = (target_seconds or 0) / 60.0
    length = (f"{minutes / 60:.1f} Hours".replace(".0 ", " ") if minutes >= 60
              else f"{minutes:.0f} Minutes")
    title = concept.get("working_title_pattern") or concept.get("id", "Untitled")
    if minutes and length.split()[0] not in title:
        title = f"{title[:70]} | {length}"
    description = " ".join(x for x in (
        concept.get("tagline") or "",
        concept.get("content_format") or "",
        f"Sound: {concept['audio_concept']}." if concept.get("audio_concept") else "",
    ) if x).strip()
    return {
        "title": title[:100],
        "description": description or title,
        "narration_script": "",
        "image_prompt": direction.get("prompt_core") or concept.get("visual_concept")
                        or "calm natural scene",
        "negative_prompt": direction.get("negative")
                           or "text, watermark, logo, people, faces",
        "audio_mood": concept.get("audio_concept") or "calm, steady, unobtrusive",
        "written_by": f"rules (model unavailable: {reason[:160]})",
    }


def generate_brief(concept, target_seconds, subject_research=None,
                   research_brief=None, findings=None):
    """Title, script, description, image direction and audio mood for one
    concept.

    Follows generate.py's own provider pattern (LLM_PROVIDER, TEST_MODE)
    rather than a new one. Raises CreativeError on any failure - a missing
    key or an unparseable reply must not silently produce an empty brief.

    A concept flagged ``requires_subject_research`` must be called with the
    cached artifact from ``subject_research.research_subject`` - its facts
    are the only claims the narration may make about the subject. Calling
    this without it for such a concept is refused rather than left to the
    model's own (unsourced) knowledge of the topic.

    ``research_brief`` (the operator's own intent, from ``research.py``)
    and ``findings`` (brief-driven competitor research, if it has run) are
    both optional - a project with neither prompts exactly as it did before
    either existed. Neither is ever required the way subject research is:
    this is additive creative context, not a factual claim the narration
    depends on.
    """
    if concept.get("requires_subject_research") and not (subject_research or {}).get("facts"):
        raise CreativeError(
            f"concept {concept.get('id')!r} requires source-backed subject "
            "research and none was supplied; run "
            "`./content-machine research <video-id>` first")

    if os.environ.get("TEST_MODE") == "1":
        return _mock_brief(concept)

    facts = (subject_research or {}).get("facts") or []
    facts_section = ""
    if facts:
        facts_lines = "\n".join(
            f"- {f['statement']} (source: {f['source_url']})" for f in facts)
        facts_section = SOURCED_FACTS_BLOCK_TEMPLATE.format(
            source_count=len(facts), provider=subject_research.get("provider", "unknown"),
            facts_lines=facts_lines)

    prompt = PROMPT_TEMPLATE.format(
        content_format=concept.get("content_format", ""),
        niche=concept.get("niche", ""),
        target_audience=concept.get("target_audience", ""),
        working_title_pattern=concept.get("working_title_pattern", ""),
        visual_concept=concept.get("visual_concept", ""),
        audio_concept=concept.get("audio_concept", ""),
        monetization_hypothesis=concept.get("monetization_hypothesis", ""),
        minutes=(target_seconds or 0) / 60.0,
        facts_section=facts_section,
        research_section=research_section(research_brief, findings),
        direction_section=direction_section(concept),
    )

    provider = os.environ.get("LLM_PROVIDER", "anthropic").lower()
    try:
        text = _call_llm(provider, prompt)
    except CreativeError as e:
        if concept.get("audio_source_requirement") == "tts_required":
            raise   # a narration script is the one thing rules cannot write
        log.warning("creative model unavailable (%s); writing the brief by rules", e)
        return rules_brief(concept, target_seconds, findings, reason=str(e))
    brief = _extract_json(text)
    required = ("title", "description", "narration_script", "image_prompt", "negative_prompt")
    missing = [k for k in required if k not in brief]
    if missing:
        raise CreativeError(f"LLM reply missing key(s) {missing}: {text[:200]!r}")
    brief.setdefault("audio_mood", None)
    return brief


# --------------------------------------------------------------------------
# scene motifs (LLM, batched) - one call per video, never one per scene
# --------------------------------------------------------------------------

SCENE_MOTIF_PROMPT_TEMPLATE = """You are choosing what each scene of a documentary-style video should visually depict. Reply with ONLY a JSON object, no markdown fences, no commentary.

Sourced facts about the subject (use only these; do not add historical claims not present here):
{facts_block}

Base visual style: {base_prompt}

For each scene below, choose a short (5-15 word) visual motif describing what its image should depict - concrete, specific to that scene's narration, and consistent only with the sourced facts above.

Scenes:
{scenes_block}

Return a JSON object mapping each scene_id to its visual motif string, e.g. {{"s01": "...", "s02": "..."}}. Include every scene_id listed above and no others."""


def _mock_scene_motifs(scenes):
    """The TEST_MODE reply: no network, deterministic, one motif per scene."""
    return {
        scene["scene_id"]: f"[MOCK motif] {scene.get('narration') or scene.get('section') or scene['scene_id']}"
        for scene in scenes
    }


def generate_scene_motifs(concept, subject_research, scenes, base_prompt=""):
    """One batched LLM call assigning a subject-grounded visual motif to
    every storyboard scene.

    Batched deliberately: one call for the whole video's scene list, never
    one call per scene - the per-scene cost this project's session-economy
    rules exist to avoid. Raises CreativeError if ``subject_research`` carries
    no facts, rather than letting the model draw scene visuals from its own
    (unsourced) memory of the topic.
    """
    facts = (subject_research or {}).get("facts") or []
    if not facts:
        raise CreativeError(
            "generate_scene_motifs requires sourced facts; refusing to "
            "invent scene visuals from model memory alone")

    if os.environ.get("TEST_MODE") == "1":
        return _mock_scene_motifs(scenes)

    facts_block = "\n".join(f"- {f['statement']} (source: {f['source_url']})" for f in facts)
    scenes_block = "\n".join(
        f"- {s['scene_id']} ({s.get('section', '')}): {s.get('narration') or '(no narration)'}"
        for s in scenes)
    prompt = SCENE_MOTIF_PROMPT_TEMPLATE.format(
        facts_block=facts_block, base_prompt=base_prompt, scenes_block=scenes_block)

    provider = os.environ.get("LLM_PROVIDER", "anthropic").lower()
    text = _call_llm(provider, prompt)
    motifs = _extract_json(text)
    missing = [s["scene_id"] for s in scenes if s["scene_id"] not in motifs]
    if missing:
        raise CreativeError(f"LLM reply missing motif(s) for scene(s) {missing}: {text[:200]!r}")
    return {s["scene_id"]: str(motifs[s["scene_id"]]) for s in scenes}


# --------------------------------------------------------------------------
# scene environments (LLM, batched) - the non-documentary counterpart of
# generate_scene_motifs above, for concepts with no sourced facts to depict
# --------------------------------------------------------------------------

SCENE_ENVIRONMENT_PROMPT_TEMPLATE = """You are choosing the visual environments for a video. Reply with ONLY a JSON object, no markdown fences, no commentary.

Concept: {content_format}
Niche: {niche}
Target audience: {target_audience}
Duration: {duration_description}
How this video is actually watched: {viewing_behavior}
Base visual style: {base_prompt}{direction_section}{research_section}

Decide how many distinct-but-visually-consistent environments THIS video needs, then describe each one. There is no fixed target - reason from the duration, format and viewing behavior above. A video that plays passively in the background for hours needs only as many strong environments as a half-attentive viewer would ever notice change; a single held setting can be the right, deliberate choice for a long ambient/slow-TV piece if it is excellent and clearly connects to the concept. A shorter or more actively watched video can support more. Do not pick a count just to give every scene something different, and do not default to one per scene.

Each environment must be a concrete, specific visual description (15-30 words) suitable for a text-to-image prompt, clearly reinforcing the concept above (not a generic, unconnected setting), and must not include anything the research below calls unwanted.

Return a JSON object with exactly two keys: "reasoning" (one sentence: why this many, given the duration and viewing behavior) and "environments" (an object mapping short slug names to their descriptions), e.g. {{"reasoning": "...", "environments": {{"env1": "..."}}}}."""

# A ceiling against a malformed or runaway reply, never a design target: the
# count of environments an actual video needs is the model's judgment call
# (duration, format, viewing behavior, concept), made in the prompt above.
_ABSOLUTE_MAX_ENVIRONMENTS = 12


def _duration_description(target_seconds):
    if not target_seconds:
        return "unknown"
    minutes = float(target_seconds) / 60.0
    if minutes >= 60:
        return f"{minutes / 60:.1f} hours ({int(minutes)} minutes)"
    return f"{minutes:.0f} minutes"


def _sanitize_environments(environments, scene_count):
    """Enforce only the bounds a malformed LLM reply could violate: at least
    one environment, and never more than there are scenes to assign or the
    absolute safety ceiling. This never second-guesses how many the model
    judged appropriate - it only guards against a broken reply.
    """
    cap = max(1, min(_ABSOLUTE_MAX_ENVIRONMENTS, scene_count))
    names = list(environments)[:cap]
    return {name: environments[name] for name in names}


def _mock_scene_environments(scenes):
    """The TEST_MODE reply: no network, deterministic. Real duration/format/
    viewing-behavior reasoning only happens against the live model (that is
    what the prompt above is for) - this stub exists purely to exercise the
    assignment/cycling wiring without a network call, at a small,
    representative count.
    """
    count = max(1, min(3, len(scenes)))
    return {f"env{i + 1}": f"[MOCK environment {i + 1}]" for i in range(count)}


def generate_scene_environments(concept, scenes, brief=None, research_brief=None,
                                findings=None, base_prompt="", target_seconds=None):
    """One batched LLM call choosing the distinct, visually consistent
    environments a concept that carries no sourced facts to depict (the
    ``requires_subject_research`` case that ``generate_scene_motifs`` handles
    instead) actually needs - the fix for scenes that would otherwise all
    reuse one fixed base prompt. How many environments is not a formula: the
    model decides from this video's duration, format and viewing behavior.

    Returns a ``{scene_id: motif}`` mapping, exactly the shape
    ``storyboard.apply_scene_motifs`` already expects, so it wires into the
    identical call site. Scenes are assigned environments round-robin in
    scene order, so consecutive scenes still vary while the same handful of
    settings recur across the video - long-form subtle variation, not a new
    setting every few minutes.
    """
    if os.environ.get("TEST_MODE") == "1":
        environments = _mock_scene_environments(scenes)
    else:
        prompt = SCENE_ENVIRONMENT_PROMPT_TEMPLATE.format(
            content_format=concept.get("content_format", ""),
            niche=concept.get("niche", ""),
            target_audience=concept.get("target_audience", ""),
            duration_description=_duration_description(target_seconds),
            viewing_behavior=(concept.get("monetization_hypothesis")
                              or concept.get("publishing_format") or "unknown"),
            base_prompt=base_prompt or concept.get("visual_concept", ""),
            direction_section=direction_section(concept),
            research_section=research_section(research_brief, findings),
        )
        provider = os.environ.get("LLM_PROVIDER", "anthropic").lower()
        try:
            text = _call_llm(provider, prompt)
            reply = _extract_json(text)
        except CreativeError as e:
            log.warning("scene environment model unavailable (%s); using rules", e)
            reply = {"environments": {
                env["slug"]: env["description"]
                for env in rules_visual_direction(concept, len(scenes))["environments"]}}
        environments = reply.get("environments") or {}
        if not environments:
            raise CreativeError(f"LLM reply carried no environments: {text[:200]!r}")

    environments = _sanitize_environments(environments, len(scenes))
    names = list(environments)
    return {s["scene_id"]: str(environments[names[i % len(names)]])
           for i, s in enumerate(scenes)}


# --------------------------------------------------------------------------
# visual direction (LLM, one call per video) - the structured replacement
# for a single free-text image prompt reused across every scene
# --------------------------------------------------------------------------

VISUAL_DIRECTION_PROMPT_TEMPLATE = """You are the art director for a video. Reply with ONLY a JSON object, no markdown fences, no commentary.

Concept: {content_format}
Niche: {niche}
Audience: {target_audience}
Visual concept: {visual_concept}
Duration: {duration_description}
How this video is actually watched: {viewing_behavior}
Scenes to fill: {scene_count}{direction_section}{research_section}

Write the visual direction for this video as structured facets, not as a prompt. Something else assembles the prompts from what you return, so do not write prompt text, do not repeat the same idea in several fields, and never use generic quality words ("8k", "hyper-realistic", "masterpiece", "highly detailed", "award winning") - they are stripped before generation and waste the field they sit in.

Decide how many distinct environments this video genuinely needs. Reason from the duration and how it is watched: a long piece playing in the background needs only as many strong settings as a half-attentive viewer would notice changing, and a single excellent setting can be the right answer. Do not give every scene its own environment.

Every environment must be concrete and specific to this concept - a place, not a mood - and the identity facets must hold across all of them, because they are what makes the scenes read as one video.

Return a JSON object with exactly these keys:
- "reasoning": one sentence on why this many environments, given duration and viewing behaviour
- "identity": an object with
    "palette": 3-5 specific colour terms (name real colours, not "warm tones")
    "lighting": one phrase describing the light source and its quality, specific enough to reproduce
    "atmosphere": one phrase for the feeling of the air in these spaces
    "materials": 3-5 specific surfaces and textures that recur
    "continuity_anchors": 2-4 concrete things that appear across scenes so a viewer knows it is the same world
    "camera": {{"lens": <e.g. "35mm", "85mm", "wide 24mm">, "perspective": <e.g. "eye level", "low and close">, "depth_of_field": one of "deep", "shallow", "medium"}}
    "render_intent": one phrase for the image's medium and treatment (e.g. "natural available light photography, faint film grain")
- "environments": a list of objects {{"slug": <short name>, "description": <15-30 words, concrete and specific>, "focal_point": <the one thing the eye lands on>, "scale": <how much of the space is in frame>}}
- "avoid": a list of short things that must not appear in these images, specific to this concept"""


def _mock_visual_direction(concept, scene_count):
    """The TEST_MODE reply: no network, deterministic, same shape."""
    return {
        "reasoning": "[MOCK] two settings for a background piece",
        "identity": {
            "palette": ["[MOCK] slate", "[MOCK] oat", "[MOCK] moss"],
            "lighting": "[MOCK] soft overcast light from one window",
            "atmosphere": "[MOCK] still and unhurried",
            "materials": ["[MOCK] worn oak", "[MOCK] brushed wool"],
            "continuity_anchors": ["[MOCK] the same brass lamp"],
            "camera": {"lens": "35mm", "perspective": "eye level",
                       "depth_of_field": "medium"},
            "render_intent": "[MOCK] natural light photography",
        },
        "environments": [
            {"slug": "envA", "description": f"[MOCK environment A] {concept.get('visual_concept', '')}".strip(),
             "focal_point": "[MOCK] the window", "scale": "[MOCK] a corner of the room"},
            {"slug": "envB", "description": f"[MOCK environment B] {concept.get('visual_concept', '')}".strip(),
             "focal_point": "[MOCK] the doorway", "scale": "[MOCK] the full room"},
        ][:max(1, min(2, scene_count or 2))],
        "avoid": ["[MOCK] text"],
    }


def generate_visual_direction(concept, scene_count, brief=None,
                              research_brief=None, findings=None,
                              target_seconds=None, llm=None):
    """One batched LLM call producing this video's visual direction document.

    One call per video, like the creative brief and the sound design - the
    per-scene call this project's economy rules exist to avoid. The reply is
    sanitised by ``visual_direction.sanitize_direction`` before anyone sees
    it, so a model that ignores the instruction not to write slop still
    cannot get slop into a prompt.
    """
    if os.environ.get("TEST_MODE") == "1":
        return vd.sanitize_direction(
            _mock_visual_direction(concept, scene_count), scene_count)

    prompt = VISUAL_DIRECTION_PROMPT_TEMPLATE.format(
        content_format=concept.get("content_format", ""),
        niche=concept.get("niche", ""),
        target_audience=concept.get("target_audience", ""),
        visual_concept=(brief or {}).get("image_prompt")
                       or concept.get("visual_concept", ""),
        duration_description=_duration_description(target_seconds),
        viewing_behavior=(concept.get("monetization_hypothesis")
                          or concept.get("publishing_format") or "unknown"),
        scene_count=scene_count,
        direction_section=direction_section(concept),
        research_section=research_section(research_brief, findings),
    )
    if llm is None:
        provider = os.environ.get("LLM_PROVIDER", "anthropic").lower()

        def llm(text):
            return _call_llm(provider, text)

    try:
        raw_reply = llm(prompt)
    except CreativeError as e:
        log.warning("visual direction model unavailable (%s); directing by rules", e)
        return vd.sanitize_direction(rules_visual_direction(concept, scene_count),
                                     scene_count)
    reply = _extract_json(raw_reply)
    direction = vd.sanitize_direction(reply, scene_count)
    if not vd.is_usable(direction):
        raise CreativeError(
            "visual direction reply carried no usable environments or "
            f"identity facets: {str(reply)[:200]!r}")
    return direction


def main():
    import argparse
    import sys

    parser = argparse.ArgumentParser(description="Standalone creative-brief preview (no metadata write).")
    parser.add_argument("concept_id")
    parser.add_argument("--minutes", type=float, default=5.0)
    args = parser.parse_args()

    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from experiment import load_concepts

    _, concepts = load_concepts()
    concept = next((c for c in concepts if c["id"] == args.concept_id), None)
    if concept is None:
        log.error("No such concept: %s", args.concept_id)
        return 1

    brief = generate_brief(concept, args.minutes * 60.0)
    composition = build_audio_composition(concept, args.minutes * 60.0, brief.get("narration_script"))
    print(json.dumps({
        "brief": brief,
        "audio_composition": composition,
        "procedural_style": pick_procedural_style(concept),
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
