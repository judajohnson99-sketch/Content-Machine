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


def build_audio_composition(concept, target_seconds, narration_text=None):
    """The executable ``audio_plan.composition`` for a concept, or None.

    None means the requirement is not synthesisable today - the caller must
    not invent a layer for it. This mirrors AUDIO_CAPABILITY exactly so the
    two never drift apart.
    """
    requirement = concept.get("audio_source_requirement")
    if requirement in _BLOCKED_REQUIREMENTS:
        return None

    if requirement == "tts_required":
        text = (narration_text or "").strip()
        if not text:
            return None
        return {
            "target_seconds": target_seconds,
            "layers": [{"id": "narration", "provider": "tts",
                       "params": {"text": text}, "gain_db": 0.0}],
        }

    if requirement in ("licensed_or_recorded", "licensed_or_recorded_plus_mixing"):
        return {
            "target_seconds": target_seconds,
            "layers": [dict(_PARTIAL_LAYER, id="bed", gain_db=0.0)],
        }

    # synthesisable_now, and any future requirement we don't recognise yet -
    # fail toward the always-available noise bed rather than raising, since
    # every niche in this bucket already accepts a synthesised texture.
    text = (concept.get("audio_concept") or "").lower()
    chosen = _DEFAULT_SYNTHESISABLE
    for keywords, layer in _SYNTHESISABLE_KEYWORDS:
        if any(k in text for k in keywords):
            chosen = layer
            break
    return {
        "target_seconds": target_seconds,
        "layers": [dict(chosen, id="bed", gain_db=0.0)],
    }


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
Visual direction: {visual_concept}
Audio direction: {audio_concept}
Monetization hypothesis: {monetization_hypothesis}
Target length: {minutes:.0f} minutes
{facts_section}
Return a JSON object with exactly these keys:
- "title": a concrete YouTube title under 100 characters, following the working title pattern's spirit
- "description": a 2-4 sentence YouTube description, plain language, no hashtag spam
- "narration_script": spoken narration text if this concept calls for narration, otherwise an empty string. Flat and calm where the concept asks for monotony; never mention it is AI-generated.
- "image_prompt": a text-to-image prompt capturing the visual direction, suitable for a diffusion model
- "negative_prompt": what to exclude from the image (e.g. text, watermarks, people, if not wanted)

Do not claim the video is professionally produced or hand-made. Do not invent facts about the audience or channel."""

# Appended into PROMPT_TEMPLATE only for concepts flagged
# requires_subject_research=true. The facts are the only claims the
# narration may make about the subject - the model is told so explicitly,
# because "here are some facts" without that instruction is not a
# meaningful constraint on an LLM that already thinks it knows the topic.
SOURCED_FACTS_BLOCK_TEMPLATE = """
Sourced facts about the subject (from {source_count} source(s) via {provider}). The narration script may state ONLY facts drawn from this list - do not add historical claims, dates, or figures that are not present here:
{facts_lines}
"""


def _mock_brief(concept):
    """The TEST_MODE reply: no network, deterministic, same shape as a real
    one. Mirrors generate.py's own TEST_MODE convention."""
    return {
        "title": f"[MOCK] {concept.get('working_title_pattern', concept.get('id', 'Untitled'))}",
        "description": f"[MOCK] {concept.get('content_format', '')}".strip() or "[MOCK] description",
        "narration_script": (
            f"[MOCK narration] {concept.get('audio_concept', '')}"
            if concept.get("audio_source_requirement") == "tts_required" else ""
        ),
        "image_prompt": concept.get("visual_concept", "abstract calm backdrop"),
        "negative_prompt": "text, watermark, logo, people, faces",
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
        raise CreativeError(
            f"LLM call failed ({provider}): {result.stderr.strip()[-500:]}")
    return result.stdout


def generate_brief(concept, target_seconds, subject_research=None):
    """Title, script, description and image direction for one concept.

    Follows generate.py's own provider pattern (LLM_PROVIDER, TEST_MODE)
    rather than a new one. Raises CreativeError on any failure - a missing
    key or an unparseable reply must not silently produce an empty brief.

    A concept flagged ``requires_subject_research`` must be called with the
    cached artifact from ``subject_research.research_subject`` - its facts
    are the only claims the narration may make about the subject. Calling
    this without it for such a concept is refused rather than left to the
    model's own (unsourced) knowledge of the topic.
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
    )

    provider = os.environ.get("LLM_PROVIDER", "anthropic").lower()
    text = _call_llm(provider, prompt)
    brief = _extract_json(text)
    required = ("title", "description", "narration_script", "image_prompt", "negative_prompt")
    missing = [k for k in required if k not in brief]
    if missing:
        raise CreativeError(f"LLM reply missing key(s) {missing}: {text[:200]!r}")
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
