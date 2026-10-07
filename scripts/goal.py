#!/usr/bin/env python3
"""Turn a plain-language production goal into something the pipeline can run.

The dashboard's entry point is a sentence - "a two-hour psychedelic Dreamdrip
sleep experience with rain and ambient music" - and everything downstream
needs a concept, a research brief and a spec. This module is the one place
that translation happens, and it is deliberately small and deliberately
honest about what kind of act it is:

**Derivation is interpretation, and is labelled as such.** The concept this
produces is written to ``experiments/derived/<id>.json`` with the goal text it
came from and ``derived_from_goal: true``. It is never added to
``experiments/concepts.json``, which is the curated, human-authored catalogue.

**The model may not invent capability.** Every enumerated field the pipeline
branches on - audio requirement, spec template, whether procedural plates are
acceptable, whether narration happens - is clamped here to a value this build
actually implements. A model that asks for licensed music this host cannot
licence gets the honest requirement recorded instead, and the gate blocks
exactly as it would for a hand-written concept.

**Research is not optional.** Every derived production gets a research brief,
always, with a niche and a creative intent taken from the goal. Depth varies;
"this one does not need research" is not an outcome this module can produce.

    python3 scripts/goal.py derive "a 2-hour rain and ambient sleep video"
    python3 scripts/goal.py derive "..." --video-id my-id --start

Standard library only.
"""
import argparse
import json
import logging
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import research as research_mod  # noqa: E402

logging.basicConfig(level=logging.INFO, format="[%(levelname)s] %(message)s")
log = logging.getLogger("goal")

ROOT = Path(__file__).resolve().parent.parent
DERIVED_DIR = ROOT / "experiments" / "derived"

DERIVED_CONCEPT_VERSION = 1

# Enumerations the pipeline branches on. A derived concept is clamped to
# these: the model proposes, this module disposes.
AUDIO_REQUIREMENTS = (
    "synthesisable_now", "tts_required", "licensed_or_recorded",
    "licensed_or_recorded_plus_mixing", "music_generation_or_licensed",
    "music_generation_plus_mixing",
)
NARRATION_MODES = ("narrated", "silent")

# Which spec template a derived production starts from, by shape. The
# template decides resolution, fps and the default duration; the goal's own
# length overrides the last of those.
SPEC_TEMPLATES = {
    "ambient_motion": "templates/long_slow_motion_ambient.json",
    "ambient_static": "templates/long_static_ambient.json",
    "narrated_story": "templates/narrated_story.json",
    "explainer": "templates/explainer_slideshow.json",
    "lullaby": "templates/kids_lullaby_hour.json",
}
DEFAULT_SHAPE = "ambient_motion"

MIN_MINUTES = 0.5
MAX_MINUTES = 12 * 60

# Written-out lengths a goal is likely to use. Parsed before the model is
# asked anything, because "2 hours" is a fact about the request and not a
# creative decision - and because a model that misreads it would silently
# produce the wrong video.
_WORD_NUMBERS = {
    "half": 0.5, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
    "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10, "twelve": 12,
}
_LENGTH_RE = re.compile(
    r"\b(\d+(?:\.\d+)?|" + "|".join(_WORD_NUMBERS) + r")[\s-]*"
    r"(hour|hr|minute|min|second|sec)s?\b", re.I)
_UNIT_MINUTES = {"hour": 60.0, "hr": 60.0, "minute": 1.0, "min": 1.0,
                 "second": 1 / 60.0, "sec": 1 / 60.0}


class GoalError(Exception):
    """Raised when a goal cannot be turned into a runnable production."""


def utc_now():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def requested_minutes(goal):
    """The length the goal asks for, in minutes, or ``None``.

    Read from the words rather than from the model, and the *longest* stated
    length wins: "a 2 hour video with 30 second scenes" is a two-hour video.
    """
    best = None
    for value, unit in _LENGTH_RE.findall(goal or ""):
        number = _WORD_NUMBERS.get(value.lower())
        if number is None:
            number = float(value)
        minutes = float(number) * _UNIT_MINUTES[unit.lower()]
        if MIN_MINUTES <= minutes <= MAX_MINUTES and (best is None or minutes > best):
            best = minutes
    return best


def slugify(text, fallback="production"):
    slug = re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")
    slug = re.sub(r"-{2,}", "-", slug)
    return (slug or fallback)[:48].strip("-")


def suggest_video_id(goal, title=None):
    base = slugify(title or " ".join((goal or "").split()[:6]))
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M")
    return f"{base[:40]}-{stamp}".strip("-")[:64]


DERIVE_PROMPT = """You are planning one YouTube video for a small automated studio. Reply with ONLY a JSON object, no markdown fences, no commentary.

The operator's goal, in their own words:
{goal}

{length_line}
This studio can actually do the following, and nothing else:
- generate abstract procedural plates locally (gradients, fields, grain), always available
- generate depicted imagery only when a GPU worker or paid image provider is configured
- synthesise audio locally: noise, tones, pads, chord-based ambient music, rain, wind, water, fire, night ambience, and occasional one-shot events
- speak narration with a local text-to-speech voice
- it cannot licence or buy music, and cannot film or record anything

Return a JSON object with exactly these keys:
- "title_pattern": a working title in the house style "Main Title | Qualifier | Length"
- "tagline": one short line describing the experience
- "niche": a short lowercase_snake_case niche key, e.g. adult_sleep or focus_study
- "content_format": one sentence describing what the video actually is
- "target_audience": one sentence describing who watches it and when
- "creative_intent": one sentence describing what it should feel like
- "visual_concept": two or three sentences of concrete visual direction - palette, light, materials, movement. Describe pictures, not adjectives.
- "audio_concept": two or three sentences of concrete sound direction, naming which of the synthesisable layers above lead and which support
- "shape": one of {shapes} - the overall form of the video
- "narration": "narrated" if a spoken script is essential, otherwise "silent"
- "needs_depicted_imagery": true only if abstract plates genuinely cannot carry this idea
- "needs_factual_research": true only if the video would state checkable facts about the real world
- "likes": a list of up to 5 short things the operator's words ask for
- "dislikes": a list of up to 5 short things the operator's words ask to avoid
- "research_topics": a list of 4 to 7 topics worth researching, chosen from: {topics}
- "assumptions": a list of up to 4 short statements this plan assumes and that research should test

Take the operator's words literally where they are specific. Where they are not, choose something a person would actually want to watch - not a generic placeholder."""


def _clamp_list(value, limit, max_items):
    out = []
    for item in value if isinstance(value, list) else []:
        text = str(item).strip()
        if text and text not in out:
            out.append(text[:limit])
        if len(out) >= max_items:
            break
    return out


def _one_line(value, default=""):
    text = " ".join(str(value or "").split())
    return text or default


def sanitize_plan(raw, goal, minutes=None):
    """A model reply turned into a concept this build can honestly run.

    Every enumerated field is forced into a value the pipeline implements,
    and the two that decide what the gate will demand - the audio
    requirement and whether procedural plates are acceptable - are derived
    here from the plan's own answers rather than taken from the model, so a
    confident reply cannot talk the studio into claiming a capability.
    """
    if not isinstance(raw, dict):
        raise GoalError("goal derivation reply was not a JSON object")

    shape = raw.get("shape") if raw.get("shape") in SPEC_TEMPLATES else DEFAULT_SHAPE
    narration = raw.get("narration") if raw.get("narration") in NARRATION_MODES else "silent"
    if narration == "narrated" and shape in ("ambient_motion", "ambient_static"):
        # A narrated ambient video is a narrated video; the template decides
        # how the script is distributed, so it has to agree.
        shape = "narrated_story"

    title = _one_line(raw.get("title_pattern"), goal[:70])
    minutes = minutes or requested_minutes(goal)

    plan = {
        "title_pattern": title,
        "tagline": _one_line(raw.get("tagline")),
        "niche": slugify(_one_line(raw.get("niche"), "general")).replace("-", "_"),
        "content_format": _one_line(raw.get("content_format"), title),
        "target_audience": _one_line(raw.get("target_audience"),
                                     "unstated; research should establish it"),
        "creative_intent": _one_line(raw.get("creative_intent"), goal),
        "visual_concept": _one_line(raw.get("visual_concept")),
        "audio_concept": _one_line(raw.get("audio_concept")),
        "shape": shape,
        "narration": narration,
        "needs_depicted_imagery": bool(raw.get("needs_depicted_imagery")),
        "needs_factual_research": bool(raw.get("needs_factual_research")),
        "likes": _clamp_list(raw.get("likes"), 80, 5),
        "dislikes": _clamp_list(raw.get("dislikes"), 80, 5),
        "research_topics": [t for t in _clamp_list(raw.get("research_topics"), 24, 7)
                            if t in research_mod.FINDING_TOPICS],
        "assumptions": _clamp_list(raw.get("assumptions"), 160, 4),
        "minutes": minutes,
    }
    if not plan["research_topics"]:
        plan["research_topics"] = list(research_mod.DEFAULT_RESEARCH_TOPICS)
    return plan


def concept_from_plan(plan, concept_id, goal):
    """The concept document a derived production is scaffolded from.

    Shaped exactly like an entry in ``experiments/concepts.json`` so every
    consumer - scaffold, readiness, creative, storyboard - reads it without
    knowing it was derived, plus the provenance fields that say it was.
    """
    audio_requirement = ("tts_required" if plan["narration"] == "narrated"
                         else "synthesisable_now")
    return {
        "id": concept_id,
        "niche": plan["niche"],
        "kind": "derived",
        "working_title_pattern": plan["title_pattern"],
        "tagline": plan["tagline"],
        "creative_intent": plan["creative_intent"],
        "target_audience": plan["target_audience"],
        "video_length_minutes": plan["minutes"] or 30,
        "visual_concept": plan["visual_concept"],
        "audio_concept": plan["audio_concept"],
        "content_format": plan["content_format"],
        "narration": plan["narration"],
        "monetization_hypothesis": "UNVERIFIED - derived from an operator goal, "
                                   "not from measured performance.",
        "production_complexity": 2,
        "generation_dependence": 3 if plan["needs_depicted_imagery"] else 1,
        "risks": ["Derived from a one-line goal; the audience and format "
                  "assumptions behind it are untested."],
        "scores": {"uncertainty_reduction": 3, "generalizability": 3,
                   "repeatability": 4, "monetization_clarity": 2,
                   "policy_risk": 3},
        "audio_source_requirement": audio_requirement,
        "spec_template": SPEC_TEMPLATES[plan["shape"]],
        # The honest capability answer, not the model's wish: this build makes
        # abstract plates everywhere, and depicted imagery only where a
        # provider exists. Saying otherwise would move the gate, not the host.
        "procedural_visuals_acceptable": not plan["needs_depicted_imagery"],
        "requires_subject_research": plan["needs_factual_research"],
        "continue_evidence": "The finished video holds attention for its full "
                             "length and the research behind it proved out.",
        "kill_evidence": "Research contradicts the premise, or the finished "
                         "video cannot be watched for its stated length.",
        "assumptions_requiring_research": plan["assumptions"] or [
            "That an audience exists for this format at this length."],
        "derived_from_goal": True,
        "goal_text": goal,
        "derived_utc": utc_now(),
        "derived_concept_version": DERIVED_CONCEPT_VERSION,
    }


def brief_from_plan(plan, goal):
    """The research brief every derived production carries.

    Always written, never conditional: a production whose brief says only
    "this niche, this intent" still researches the niche, which is the whole
    point of research being a requirement rather than a feature.
    """
    # research_topics is deliberately left empty. In a hand-written brief it
    # means "the operator named these specifically", which research treats as
    # an instruction that must succeed or fail the stage. A derived plan's
    # topics are a plan, not an instruction, and the default sweep covers the
    # same ground - so a host with no search provider gets a visible skipped
    # research stage and a blocked review gate, rather than a production that
    # cannot be started at all.
    return {
        "niche": plan["niche"],
        "creative_intent": plan["creative_intent"] or goal,
        "likes": plan["likes"],
        "dislikes": plan["dislikes"],
        "research_topics": [],
        "notes": f"Derived from the operator's goal: {goal}. Planned research "
                 f"topics: {', '.join(plan['research_topics'])}.",
        "seed_references": [],
    }


def derive_plan(goal, llm=None, minutes=None):
    """One LLM call turning a goal into a sanitised plan."""
    goal = " ".join((goal or "").split())
    if len(goal) < 8:
        raise GoalError("a production goal needs to be a sentence, not a word")

    stated = minutes or requested_minutes(goal)
    length_line = (
        f"The operator asked for about {stated:.0f} minutes; plan for that length.\n"
        if stated else
        "The operator did not state a length; choose one that suits the format.\n")
    prompt = DERIVE_PROMPT.format(
        goal=goal, length_line=length_line,
        shapes=", ".join(sorted(SPEC_TEMPLATES)),
        topics=", ".join(research_mod.FINDING_TOPICS))

    if llm is None:
        if os.environ.get("TEST_MODE") == "1":
            return sanitize_plan(_mock_plan(goal), goal, minutes=stated)

        def llm(text):
            import creative as creative_mod
            return creative_mod._call_llm(
                os.environ.get("LLM_PROVIDER", "anthropic").lower(), text)

    text = llm(prompt)
    try:
        import creative as creative_mod
        reply = creative_mod._extract_json(text)
    except Exception as exc:  # noqa: BLE001 - reported, never guessed around
        raise GoalError(f"goal derivation reply was not usable JSON: {exc}") from exc
    return sanitize_plan(reply, goal, minutes=stated)


def _mock_plan(goal):
    """TEST_MODE's deterministic stand-in. Marked, so nothing mistakes it."""
    return {
        "title_pattern": f"[MOCK] {goal[:50]} | Long Form",
        "tagline": "[MOCK] a derived production",
        "niche": "adult_sleep",
        "content_format": f"[MOCK] a video made from the goal: {goal}",
        "target_audience": "[MOCK] adults who sleep with sound",
        "creative_intent": goal,
        "visual_concept": "[MOCK] slow violet fields under low light.",
        "audio_concept": "[MOCK] a pad bed under rain.",
        "shape": "ambient_motion",
        "narration": "silent",
        "needs_depicted_imagery": False,
        "needs_factual_research": False,
        "likes": ["rain"],
        "dislikes": ["sudden noises"],
        "research_topics": ["concept", "audience", "visuals", "audio", "duration"],
        "assumptions": ["[MOCK] that this format holds attention"],
    }


def spec_seconds(pdir):
    try:
        return float(json.loads((pdir / "video_spec.json").read_text())["duration_seconds"])
    except (OSError, ValueError, KeyError):
        return None


def derived_path(concept_id):
    return DERIVED_DIR / f"{concept_id}.json"


def save_derived_concept(concept):
    DERIVED_DIR.mkdir(parents=True, exist_ok=True)
    derived_path(concept["id"]).write_text(json.dumps(concept, indent=2) + "\n")
    return concept


def load_derived_concept(concept_id):
    path = derived_path(concept_id)
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return None


def derive_production(goal, video_id=None, minutes=None, llm=None,
                      excerpt_seconds=None):
    """Goal in, a scaffolded project out, with its research brief written.

    Returns ``{"video_id", "concept", "plan", "brief", "scaffold"}``. The
    project exists on disk afterwards and nothing has been produced yet:
    starting the run stays a separate, explicit act.

    ``excerpt_seconds`` builds the project at a short length while the
    concept still records the full one it is meant to become. That is what
    lets the dashboard offer "produce this at full length" afterwards
    instead of making the operator describe the video a second time.
    """
    import experiment as experiment_mod

    plan = derive_plan(goal, llm=llm, minutes=minutes)
    video_id = (video_id or suggest_video_id(goal, plan["title_pattern"])).strip().lower()
    concept = save_derived_concept(concept_from_plan(plan, video_id, goal))

    full_seconds = (plan["minutes"] * 60.0) if plan["minutes"] else None
    scaffold = experiment_mod.scaffold_project(
        concept["id"], video_id,
        duration=float(excerpt_seconds) if excerpt_seconds else full_seconds)

    brief = research_mod.save_brief(video_id, brief_from_plan(plan, goal))

    # The goal is the project's own origin story, not a detail of the
    # concept file: a reader of metadata.json should see what was asked for.
    pdir = ROOT / "projects" / video_id
    metadata = json.loads((pdir / "metadata.json").read_text())
    metadata.setdefault("experiment", {})["goal_text"] = goal
    metadata["experiment"]["derived_concept"] = True
    # What this production is meant to be, as opposed to what it currently
    # is. An excerpt is a view of a full-length video, not a different one.
    metadata["experiment"]["full_length_seconds"] = (
        full_seconds or spec_seconds(pdir))
    metadata["experiment"]["is_excerpt"] = bool(
        excerpt_seconds and full_seconds and excerpt_seconds < full_seconds)
    metadata["tagline"] = plan["tagline"]
    (pdir / "metadata.json").write_text(json.dumps(metadata, indent=2) + "\n")

    log.info("Derived %s from the goal (%s, %s minutes, %s)",
             video_id, plan["niche"], plan["minutes"], plan["shape"])
    return {"video_id": video_id, "concept": concept, "plan": plan,
            "brief": brief, "scaffold": scaffold}


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

def cmd_derive(args):
    try:
        result = derive_production(args.goal, video_id=args.video_id,
                                   minutes=args.minutes)
    except (GoalError, Exception) as e:  # noqa: BLE001 - CLI reports, never traces
        if isinstance(e, KeyboardInterrupt):
            raise
        log.error("%s", e)
        return 1
    print(json.dumps({"video_id": result["video_id"],
                      "concept_id": result["concept"]["id"],
                      "plan": result["plan"]}, indent=2))
    return 0


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    derive = sub.add_parser("derive", help="turn a goal into a scaffolded project")
    derive.add_argument("goal")
    derive.add_argument("--video-id", default=None)
    derive.add_argument("--minutes", type=float, default=None)
    derive.set_defaults(func=cmd_derive)
    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
