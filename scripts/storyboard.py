#!/usr/bin/env python3
"""Storyboard: the scene layer between a script and a rendered video.

A storyboard is the explicit, auditable plan for a video - one entry per
scene, each carrying its duration, its slice of the narration, what it should
look like, the prompt that will produce it, the image that did, how the camera
moves, and how it hands over to the next scene.

Three properties are load-bearing:

**Deterministic.** The same script, concept and format profile always produce
the same storyboard, down to scene ids and motion assignment. Nothing consults
a clock or a random number generator. A storyboard can therefore be rebuilt
and diffed, and a render is reproducible from it.

**Honest about generation.** A scene's image is requested at the resolution it
will really be generated at - 512x512 on the current GPU worker - and the
``GenerationRequest`` digest records that. Scaling to 1920x1080 happens later,
in ``motion``/``render``, as a presentation step. Nothing here may describe a
512x512 source as a 1080p generation.

**Non-destructive to the existing seam.** A scene's image is produced through
the same ``generation.Router`` as everything else, so the digest/reuse
semantics, the job store, and the provenance rules are unchanged. Storyboards
add a plan; they do not add a second way to make an image.

    python3 scripts/storyboard.py build <video-id> [--niche N] [--scenes N]
    python3 scripts/storyboard.py show <video-id>
    python3 scripts/storyboard.py validate <video-id>

Standard library only.
"""
import argparse
import hashlib
import json
import logging
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import generation  # noqa: E402
import motion as motion_mod  # noqa: E402
import research  # noqa: E402

logging.basicConfig(level=logging.INFO, format="[%(levelname)s] %(message)s")
log = logging.getLogger("storyboard")

ROOT = Path(__file__).resolve().parent.parent
PROJECTS_DIR = ROOT / "projects"

STORYBOARD_VERSION = 1
STORYBOARD_FILENAME = "storyboard.json"

# What the GPU worker can actually render. 512x512 is the verified size on
# the current card; 1920x1080 diffusion has never been run and is not assumed
# to fit. This is the generation size, never the output size.
DEFAULT_SOURCE_WIDTH = 512
DEFAULT_SOURCE_HEIGHT = 512

# Fallbacks when no format profile exists for the niche.
DEFAULT_SECONDS_PER_SCENE = 6.0
# How a shot length is guessed from the video's length when nothing else
# says. Six seconds is right for a ten-minute explainer and plainly wrong
# for a two-hour sleep video: a long piece asks the viewer to settle, and a
# cut every six seconds is the opposite of that. Roughly one shot per two
# minutes of runtime, bounded at both ends, which lands on 6s for a short
# excerpt, 15s for half an hour and 30s for two hours. A research directive
# overrides this the moment sourced findings say what the niche actually does.
SECONDS_PER_SCENE_BOUNDS = (6.0, 30.0)
SCENE_LENGTH_PER_RUNTIME = 1 / 120.0
MIN_SCENE_SECONDS = 2.0
DEFAULT_TRANSITION_SECONDS = 0.75

# Scene count is no longer bounded by memory: above render.MAX_IMAGE_SLOTS
# the renderer switches to its piecewise path, which holds two inputs open at
# a time however long the video is. The ceiling imported here is render.py's
# sanity limit, not a memory one - a thirty-minute video is meant to be two
# hundred distinct shots, not twenty-four five-minute stills.
try:
    import render as render_mod
    MAX_SCENES = render_mod.MAX_SCENES_PIECEWISE
except Exception:  # pragma: no cover - render.py is always importable in-tree
    MAX_SCENES = 1200

# Motion is assigned from a fixed cycle, offset by a stable hash of the video
# id. Deterministic per project, but two projects do not open with the same
# move. Ordered so neighbours contrast.
MOTION_CYCLE = (
    "zoom_in", "pan_right", "static", "zoom_out",
    "pan_left", "pan_zoom", "pan_down", "zoom_in", "pan_up",
)

# Sections a storyboard lays out, in order, with the share of runtime each
# takes. Derived from the format profile when one is available.
DEFAULT_SECTIONS = (("hook", 0.12), ("body", 0.76), ("outro", 0.12))

# Movement styles a research directive can ask for. "still" is not "no
# motion at all": a frame that never moves for half an hour reads as a frozen
# player, so even the stillest style keeps an almost imperceptible move -
# mostly ``drift``, which is exactly that. Every cycle is ordered so that no
# move follows itself, including the wrap from the last entry to the first.
MOTION_STYLE_CYCLES = {
    "still": ("drift", "zoom_in", "drift", "static", "drift", "zoom_out"),
    "drifting": ("drift", "parallax", "zoom_in", "parallax_in", "drift",
                 "zoom_out", "parallax", "pan_zoom"),
    "travelling": ("pan_right", "parallax", "zoom_in", "pan_left", "pan_up",
                   "pan_zoom", "parallax_in", "zoom_out", "pan_down"),
}

# Sleep/relaxation content with no research style gets the layered and
# drifting moves, mixed with plain push-ins so the 2.5D look is a texture
# rather than the only trick. It is the "drifting" cycle: that is what such
# content is.
CALM_MOTION_CYCLE = MOTION_STYLE_CYCLES["drifting"]

# Words in a project's concept/plan that mark it as content meant to be left
# on and relaxed or slept to. Matched as substrings of lower-cased text.
CALM_KEYWORDS = ("sleep", "relax", "calm", "ambient", "meditat", "soothing",
                 "lofi", "lo-fi", "study", "asmr", "dream", "white noise",
                 "rain", "tranquil", "serene", "peaceful", "unwind")
# A video with no narration at all and at least this long is calm content
# whatever its concept says: nobody sits through ten silent minutes of
# whip pans.
CALM_SILENT_MIN_SECONDS = 600.0

# A calm concept that is itself about haze gets the slow fog overlay on every
# third scene - often enough to be part of the look, rarely enough that the
# overlay never becomes the subject (and it costs render time).
FOG_KEYWORDS = ("fog", "mist", "haze", "hazy", "cloud", "smoke", "steam",
                "vapour", "vapor")
FOG_EVERY = 3

# Long-form looping. A three-hour sleep video does not need three hours of
# distinct shots, and a CPU host cannot render them: the storyboard plans one
# *unique cycle* of shots whose last scene dissolves back into its first, and
# the renderer repeats that cycle by stream copy to the full length. The
# cycle is full_length / k for the smallest whole k that keeps it at or under
# this many seconds, so the video ends exactly on a seam rather than
# mid-shot. Override per project with video_spec.json "unique_cycle_seconds"
# (0 disables looping and renders every second unique).
DEFAULT_UNIQUE_CYCLE_SECONDS = 1200.0
# Below this multiple of the cycle the whole video is rendered unique: a
# 25-minute piece is better rendered once than presented as a loop.
LOOP_MIN_RATIO = 1.5


class StoryboardError(Exception):
    def __init__(self, problems):
        if isinstance(problems, str):
            problems = [problems]
        super().__init__("; ".join(problems))
        self.problems = list(problems)


def utc_now():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def project_dir(video_id):
    return PROJECTS_DIR / video_id


def storyboard_path(video_id):
    return project_dir(video_id) / STORYBOARD_FILENAME


# --------------------------------------------------------------------------
# deterministic building blocks
# --------------------------------------------------------------------------

def _stable_offset(text, modulo):
    """A reproducible small integer from a string.

    Python's ``hash()`` is salted per process, so it cannot be used for
    anything that has to survive a restart. SHA-256 can.
    """
    digest = hashlib.sha256(str(text).encode("utf-8")).hexdigest()
    return int(digest[:8], 16) % max(modulo, 1)


_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+")


def split_script(script, scene_count):
    """Distribute narration across scenes without losing or inventing text.

    Sentences are kept whole where possible; a script shorter than the scene
    count leaves later scenes with no narration rather than padding them with
    something nobody wrote.
    """
    text = (script or "").strip()
    if not text or scene_count <= 0:
        return [""] * max(scene_count, 0)
    sentences = [s.strip() for s in _SENTENCE_SPLIT.split(text) if s.strip()]
    if not sentences:
        return [""] * scene_count
    if len(sentences) <= scene_count:
        return sentences + [""] * (scene_count - len(sentences))

    # More sentences than scenes: spread them as evenly as integer division
    # allows, front-loading the remainder so the hook is not starved.
    per = len(sentences) // scene_count
    extra = len(sentences) % scene_count
    out, cursor = [], 0
    for i in range(scene_count):
        take = per + (1 if i < extra else 0)
        out.append(" ".join(sentences[cursor:cursor + take]))
        cursor += take
    return out


def default_seconds_per_scene(target_seconds):
    """How long to hold a shot in a video of this length, absent any
    research or format profile saying otherwise."""
    low, high = SECONDS_PER_SCENE_BOUNDS
    return max(low, min(high, float(target_seconds) * SCENE_LENGTH_PER_RUNTIME))


def _sections_for(scene_count, profile):
    """Assign each scene index a structural section name."""
    sections = DEFAULT_SECTIONS
    if profile:
        observed = [entry["value"] for entry in profile.get("structural_sections", [])]
        if len(observed) >= 2:
            share = 1.0 / len(observed)
            sections = tuple((name, share) for name in observed)
    names = []
    for name, share in sections:
        names.extend([name] * max(int(round(share * scene_count)), 1))
    if len(names) < scene_count:
        names.extend([sections[-1][0]] * (scene_count - len(names)))
    return names[:scene_count]


def _scene_prompt(base_prompt, visual_categories, index, section):
    """A per-scene prompt derived from the project's image direction.

    The base prompt is the project's own (from its concept and creative
    brief). Categories only add a structural hint - "interior", "landscape" -
    which is a format fact, not anyone's creative expression.

    Nothing the generator cannot draw goes in: the scene's index and
    section are bookkeeping and live in the scene's own fields. Appending
    ", scene 7, body" described no picture, and since it made every prompt
    unique it also forced a separate render for two scenes meant to show
    the same thing (see ``_seed_for_prompt``).
    """
    parts = [base_prompt.strip()] if base_prompt else []
    if visual_categories:
        parts.append(visual_categories[index % len(visual_categories)])
    return ", ".join(p for p in parts if p)


def _seed_for_prompt(prompt, base_seed, assigned):
    """One seed per distinct picture, not per scene.

    A seed is what makes two renders of the same words different images.
    Varying it by scene index meant a storyboard that deliberately showed
    one environment across ten scenes still paid for ten near-identical
    generations. Keyed on the prompt instead, scenes that ask for the same
    picture share a request digest (and therefore one asset), while each
    genuinely distinct prompt still gets its own seed and its own image.
    """
    if prompt not in assigned:
        assigned[prompt] = base_seed + len(assigned)
    return assigned[prompt]


# --------------------------------------------------------------------------
# build
# --------------------------------------------------------------------------

def motion_cycle_for(style, calm=False):
    """The order moves are assigned in, for a named movement style.

    A style is a research directive ("sources describe held, motionless
    frames"), not a preference: it decides whether this video breathes, drifts
    across its images, or holds them still. Absent a style, calm content
    (sleep, relaxation, ambient) gets the drift/parallax cycle and anything
    else the default mixed cycle, which is what every project got before
    directives existed.
    """
    if style in MOTION_STYLE_CYCLES:
        return MOTION_STYLE_CYCLES[style]
    return CALM_MOTION_CYCLE if calm else MOTION_CYCLE


def _project_text(metadata):
    plan = metadata.get("visual_plan") or {}
    return " ".join(str(v) for v in (
        metadata.get("concept"), metadata.get("niche"),
        metadata.get("selected_title"), plan.get("prompt"), plan.get("style"),
    ) if v).lower()


def _is_narrated(metadata):
    return bool((metadata.get("script") or "").strip())


def is_calm_content(metadata, target_seconds):
    """Is this a video meant to be relaxed or slept to?

    True when the concept/plan says so in words, or when it has no narration
    at all and runs at least CALM_SILENT_MIN_SECONDS.
    """
    if any(word in _project_text(metadata) for word in CALM_KEYWORDS):
        return True
    return (not _is_narrated(metadata)
            and float(target_seconds) >= CALM_SILENT_MIN_SECONDS)


def loop_plan(target_seconds, spec, narrated):
    """``(cycle_seconds, repeats)`` when this video should loop a unique
    cycle of shots, else ``None``.

    Never for narrated video: narration is laid out scene by scene, and
    repeating the pictures under a script that keeps going would put the
    wrong shot under every line after the first cycle.
    """
    if narrated:
        return None
    raw = spec.get("unique_cycle_seconds", DEFAULT_UNIQUE_CYCLE_SECONDS)
    if (raw is None or isinstance(raw, bool)
            or not isinstance(raw, (int, float)) or raw <= 0):
        return None
    target = float(target_seconds)
    if target < float(raw) * LOOP_MIN_RATIO:
        return None
    # Smallest whole number of repeats that keeps the cycle within the limit;
    # integer milliseconds so float noise cannot add a repeat.
    repeats = -(-int(round(target * 1000)) // int(round(float(raw) * 1000)))
    return round(target / repeats, 3), repeats


def _break_repeats(kinds, cycle, circular):
    """No move directly follows itself - including across the loop seam.

    The cycles never repeat a move on their own; this only matters where the
    scene count and the cycle length line up so that, in a looped video, the
    last scene would play the same move as the first one it dissolves into.
    """
    kinds = list(kinds)
    if len(kinds) < 2:
        return kinds
    indices = range(len(kinds)) if circular else range(1, len(kinds))
    for i in indices:
        prev = kinds[i - 1]
        if kinds[i] != prev:
            continue
        after = kinds[(i + 1) % len(kinds)]
        for candidate in cycle:
            if candidate not in (prev, after):
                kinds[i] = candidate
                break
    return kinds


def build_storyboard(video_id, metadata, spec, profile=None, scene_count=None,
                     source_width=None, source_height=None, directives=None):
    """Derive a complete storyboard. Pure function of its arguments.

    ``directives`` is ``research.production_directives()["values"]``: the
    shot length, movement style and dissolve length sourced research asked
    for. Each one is applied only if it is present, so a project with no
    research renders exactly as it did before, and each applied value is
    recorded in ``storyboard["research_applied"]`` so the reason a video is
    paced the way it is stays inspectable.
    """
    target_seconds = float(spec.get("duration_seconds") or 0)
    if target_seconds <= 0:
        raise StoryboardError("video_spec.duration_seconds must be > 0")

    width = int(spec.get("width") or 1920)
    height = int(spec.get("height") or 1080)
    fps = spec.get("fps") or 30
    directives = directives or {}
    applied = {}

    seconds_per_scene = default_seconds_per_scene(target_seconds)
    if profile and profile.get("seconds_per_shot_median"):
        seconds_per_scene = float(profile["seconds_per_shot_median"])
    if directives.get("seconds_per_scene"):
        seconds_per_scene = float(directives["seconds_per_scene"])
        applied["seconds_per_scene"] = seconds_per_scene
    seconds_per_scene = max(seconds_per_scene, MIN_SCENE_SECONDS)

    # A long silent video plans one unique cycle of shots and loops it; the
    # scene count, dissolves and shot lengths are then all about that cycle.
    narrated = _is_narrated(metadata)
    loop = loop_plan(target_seconds, spec, narrated)
    span = loop[0] if loop else target_seconds

    if scene_count is None:
        scene_count = max(int(round(span / seconds_per_scene)), 1)
    scene_count = max(1, min(int(scene_count), MAX_SCENES))

    transition_seconds = min(
        DEFAULT_TRANSITION_SECONDS,
        max(span / scene_count * 0.2, 0.0))
    if directives.get("transition_seconds"):
        # Still bounded by the scene it leaves: a dissolve cannot be longer
        # than the shot it dissolves out of, whatever research says.
        transition_seconds = min(float(directives["transition_seconds"]),
                                 max(span / scene_count * 0.4, 0.0))
        applied["transition_seconds"] = round(transition_seconds, 3)

    calm = is_calm_content(metadata, target_seconds)
    motion_cycle = motion_cycle_for(directives.get("motion_style"), calm=calm)
    if directives.get("motion_style") in MOTION_STYLE_CYCLES:
        applied["motion_style"] = directives["motion_style"]
        motion_profile = directives["motion_style"]
    else:
        motion_profile = "calm" if calm else "default"
    foggy = calm and any(word in _project_text(metadata) for word in FOG_KEYWORDS)

    # Solve scene duration so the finished timeline - which is shorter than
    # the sum of the scenes by one overlap per cut - lands on the target. A
    # looped cycle's last scene also dissolves (back into the first), so it
    # carries one overlap per scene rather than one per cut.
    overlaps = (scene_count if loop else scene_count - 1) * transition_seconds
    per_scene = (span + overlaps) / scene_count
    if per_scene < MIN_SCENE_SECONDS:
        raise StoryboardError(
            f"{scene_count} scenes over {span}s gives "
            f"{per_scene:.2f}s each, under the {MIN_SCENE_SECONDS}s minimum. "
            f"Ask for fewer scenes or a longer video.")
    offset = _stable_offset(video_id, len(motion_cycle))
    kinds = _break_repeats(
        [motion_cycle[(i + offset) % len(motion_cycle)] for i in range(scene_count)],
        motion_cycle, circular=bool(loop))

    plan = metadata.get("visual_plan") or {}
    base_prompt = plan.get("prompt") or metadata.get("concept") or ""
    negative = plan.get("negative_prompt")
    style = plan.get("style", "deep-night")
    seed = plan.get("seed", 20260827)
    model = plan.get("model")

    categories = [entry["value"] for entry in (profile or {}).get("visual_categories", [])]
    narration = split_script(metadata.get("script"), scene_count)
    sections = _sections_for(scene_count, profile)

    src_w = int(source_width or DEFAULT_SOURCE_WIDTH)
    src_h = int(source_height or DEFAULT_SOURCE_HEIGHT)

    scenes = []
    seeds_by_prompt = {}
    for i in range(scene_count):
        section = sections[i]
        prompt = _scene_prompt(base_prompt, categories, i, section)
        # In a loop the last scene dissolves back into the first; otherwise
        # the video simply ends on it.
        is_last = i == scene_count - 1 and not loop
        scene = {
            "scene_id": f"s{i + 1:02d}",
            "index": i,
            "section": section,
            "duration_seconds": round(per_scene, 3),
            "narration": narration[i],
            "visual_intent": (categories[i % len(categories)]
                              if categories else section),
            "image_prompt": prompt,
            "negative_prompt": negative,
            "image": None,
            "generation": {
                "job_id": None,
                "provider": None,
                "width": src_w,
                "height": src_h,
                "seed": _seed_for_prompt(prompt, seed, seeds_by_prompt),
                "model": model,
                "style": style,
                "request_digest": None,
            },
            "motion": {
                "kind": kinds[i],
                "fit": "cover",
                "amount": None,
            },
            "transition": {
                "kind": "cut" if is_last else "crossfade",
                "duration_seconds": 0.0 if is_last else round(transition_seconds, 3),
            },
            "overlay": {"text": None, "position": None},
            "audio_cues": {
                "narration_segment_index": i,
                "ambience": (profile or {}).get("ambience", [{}])[0].get("value")
                if (profile or {}).get("ambience") else None,
            },
            "provenance": {
                "derived_from": "script + visual_plan"
                                + (" + format_profile" if profile else ""),
                "storyboard_version": STORYBOARD_VERSION,
            },
        }
        # The digest is what makes a scene's image reusable, so it is computed
        # from the same request object the router will be handed.
        scene["generation"]["request_digest"] = scene_request(scene).digest()
        scenes.append(scene)
        if foggy and i % FOG_EVERY == 1:
            scene["motion"]["ambient"] = "fog"

    storyboard = {
        "storyboard_version": STORYBOARD_VERSION,
        "video_id": video_id,
        "built_utc": utc_now(),
        # Kept so a later motif pass (apply_scene_motifs) can rebuild each
        # scene's prompt around the same base without re-reading metadata.
        "visual_plan_prompt": base_prompt,
        "target": {
            "width": width, "height": height, "fps": fps,
            "duration_seconds": target_seconds,
        },
        # Stated explicitly and separately from `target`, because these are
        # not the same number and conflating them is exactly the dishonesty
        # this field exists to prevent.
        "source_generation": {
            "width": src_w,
            "height": src_h,
            "note": "images are generated at this size and scaled up for "
                    "output; they are not 1920x1080 generations",
        },
        "format_profile": ({"niche": profile["niche"],
                            "confidence": profile["confidence"],
                            "observation_count": profile["observation_count"]}
                           if profile else None),
        # What sourced research changed about this plan, and nothing else:
        # an empty mapping means the defaults stood, not that research ran.
        "research_applied": applied,
        # Which move cycle was used and why: a research style, "calm"
        # (sleep/relaxation content: drift and parallax), or "default".
        "motion_profile": motion_profile,
        "scenes": scenes,
    }
    if loop:
        storyboard["loop"] = loop_record(scenes, target_seconds)
    storyboard["timeline_seconds"] = finished_seconds(storyboard)
    return storyboard


def loop_record(scenes, full_seconds):
    """What a looped video really is, stated where a reviewer will see it.

    A three-hour video built from a twenty-minute cycle is not three hours of
    distinct shots, and nothing downstream may present it as one: the
    package and the dashboard carry these numbers, not just the runtime.
    """
    cycle = motion_mod.cycle_seconds(scenes)
    return {
        "mode": "looped_cycle",
        "unique_scenes": len(scenes),
        "cycle_seconds": cycle,
        "full_seconds": float(full_seconds),
        "repeats": round(float(full_seconds) / cycle, 3) if cycle else None,
        "note": ("the video repeats one unique cycle of shots; the last shot "
                 "dissolves back into the first so the repeat has no seam"),
    }


def finished_seconds(storyboard):
    """The finished runtime this plan renders to.

    For a looped plan that is the full length the cycle is repeated to; for
    anything else it is the scenes' own timeline.
    """
    scenes = storyboard.get("scenes", [])
    loop = storyboard.get("loop")
    if loop and loop.get("full_seconds"):
        return round(float(loop["full_seconds"]), 3)
    return motion_mod.timeline_seconds(scenes)


def scene_request(scene, require_depicted=False):
    """The GenerationRequest for one scene.

    Built at the scene's *source* resolution. Passing the output resolution
    here would both ask the GPU for a latent it cannot fit and make the
    digest describe a generation that never happened.
    """
    gen = scene.get("generation") or {}
    return generation.GenerationRequest(
        prompt=scene.get("image_prompt") or "",
        negative_prompt=scene.get("negative_prompt"),
        width=int(gen.get("width", DEFAULT_SOURCE_WIDTH)),
        height=int(gen.get("height", DEFAULT_SOURCE_HEIGHT)),
        count=1,
        seed=gen.get("seed", 20260827),
        model=gen.get("model"),
        style=gen.get("style", "deep-night"),
        require_depicted=require_depicted,
    )


def apply_scene_prompts(storyboard, prompts, negative_prompt=None,
                        intents=None):
    """Replace each scene's prompt with a fully compiled one.

    The difference from ``apply_scene_motifs`` is what the string means.
    A motif is a fragment appended to the project's base prompt; a compiled
    prompt (``visual_direction.compile_scene_prompts``) already contains the
    environment *and* the identity facets in the order a generator weights
    them, so prefixing the old free-text base prompt would only put the
    slop back in front of it.

    Seeds and request digests are recomputed here for the same reason they
    are there: identity is the prompt, so two scenes asking for the same
    picture must share a seed and a digest and therefore one render.
    """
    scenes = storyboard.get("scenes", [])
    base_seed = min((s["generation"].get("seed", 0) for s in scenes
                     if s.get("generation")), default=0)
    seeds_by_prompt = {}
    for scene in scenes:
        prompt = prompts.get(scene["scene_id"])
        if not prompt:
            continue
        scene["image_prompt"] = prompt
        if intents and intents.get(scene["scene_id"]):
            # What this scene depicts, in words a person reads - the prompt
            # itself is the compiled form and is not a summary of anything.
            scene["visual_intent"] = intents[scene["scene_id"]]
        if negative_prompt is not None:
            scene["negative_prompt"] = negative_prompt
        scene["generation"]["seed"] = _seed_for_prompt(
            prompt, base_seed, seeds_by_prompt)
        scene["generation"]["request_digest"] = scene_request(scene).digest()
    return storyboard


def apply_scene_motifs(storyboard, motifs):
    """Rebuild each scene's prompt around its subject-grounded motif.

    ``motifs`` maps scene_id -> a short visual-motif string, normally from
    ``creative.generate_scene_motifs`` (one batched call for the whole
    storyboard). Replaces the generic category tag ``_scene_prompt`` used
    with something specific to that scene's own narration, and recomputes
    the scene's request digest - reuse-by-digest must key on the prompt that
    will actually be generated, not the one it replaced.

    The prompt carries only what a generator should draw. Scene bookkeeping
    (index, section) stays in the scene's own fields: appending ", scene 7,
    body" told the model nothing about the picture, and - because it made
    every prompt unique - it also forced a separate generation for two
    scenes that were deliberately assigned the *same* environment. With the
    tag gone, those two scenes share a request digest and therefore one
    generated asset, which is the intended behaviour: a repeated
    environment is a reuse, not a second render.
    """
    base_prompt = (storyboard.get("visual_plan_prompt") or "").strip()
    scenes = storyboard.get("scenes", [])
    base_seed = min((s["generation"].get("seed", 0) for s in scenes
                     if s.get("generation")), default=0)
    seeds_by_prompt = {}
    for scene in scenes:
        motif = motifs.get(scene["scene_id"])
        if not motif:
            continue
        scene["visual_intent"] = motif
        parts = [base_prompt] if base_prompt else []
        parts.append(motif)
        scene["image_prompt"] = ", ".join(p for p in parts if p)
        scene["generation"]["seed"] = _seed_for_prompt(
            scene["image_prompt"], base_seed, seeds_by_prompt)
        scene["generation"]["request_digest"] = scene_request(scene).digest()
    return storyboard


# --------------------------------------------------------------------------
# validation
# --------------------------------------------------------------------------

def validate(storyboard, pdir=None):
    """Every reason this storyboard could not be rendered. Empty means go."""
    problems = []
    if not isinstance(storyboard, dict):
        return ["storyboard must be a JSON object"]
    if storyboard.get("storyboard_version") != STORYBOARD_VERSION:
        problems.append(
            f"storyboard_version {storyboard.get('storyboard_version')!r} is "
            f"not the supported version {STORYBOARD_VERSION}; rebuild it")

    target = storyboard.get("target") or {}
    for key in ("width", "height", "fps", "duration_seconds"):
        value = target.get(key)
        if not isinstance(value, (int, float)) or isinstance(value, bool) or value <= 0:
            problems.append(f"target.{key} must be a positive number, got {value!r}")

    scenes = storyboard.get("scenes")
    if not isinstance(scenes, list) or not scenes:
        problems.append("storyboard has no scenes")
        return problems
    if len(scenes) > MAX_SCENES:
        problems.append(
            f"{len(scenes)} scenes exceeds the {MAX_SCENES} concurrent-input "
            f"limit; each scene is an open ffmpeg input and too many exhaust "
            f"memory mid-render")

    seen = set()
    for i, scene in enumerate(scenes):
        where = scene.get("scene_id") or f"scene {i}"
        if not isinstance(scene, dict):
            problems.append(f"{where}: must be an object")
            continue
        scene_id = scene.get("scene_id")
        if not scene_id:
            problems.append(f"scene {i}: missing scene_id")
        elif scene_id in seen:
            problems.append(f"duplicate scene_id {scene_id!r}")
        else:
            seen.add(scene_id)

        duration = scene.get("duration_seconds")
        if not isinstance(duration, (int, float)) or isinstance(duration, bool) or duration <= 0:
            problems.append(f"{where}: duration_seconds must be > 0, got {duration!r}")

        motion_cfg = scene.get("motion") or {}
        transition_cfg = scene.get("transition") or {}
        problems.extend(
            f"{where}: {p}" for p in motion_mod.validate_motion(
                motion_cfg.get("kind", "static"),
                fit=motion_cfg.get("fit", "cover"),
                transition=transition_cfg.get("kind", "crossfade"),
                ambient=motion_cfg.get("ambient")))

        overlap = float(transition_cfg.get("duration_seconds", 0.0) or 0.0)
        if transition_cfg.get("kind") != "cut" and isinstance(duration, (int, float)) \
                and not isinstance(duration, bool) and overlap >= duration:
            problems.append(
                f"{where}: transition of {overlap}s is not shorter than the "
                f"{duration}s scene it leaves")

        image = scene.get("image")
        if not image:
            problems.append(f"{where}: no image assigned (run `scenes`)")
        elif pdir is not None:
            path = Path(image)
            if not path.is_absolute():
                path = pdir / image
            if not path.is_file():
                problems.append(f"{where}: image is missing from disk: {image}")

        gen = scene.get("generation") or {}
        if image and not gen.get("job_id"):
            problems.append(
                f"{where}: has an image but no generation.job_id, so its "
                f"provenance cannot be traced")
    return problems


def unresolved_scenes(storyboard):
    """Scenes still waiting on an image - the queue-is-pending case."""
    return [s for s in storyboard.get("scenes", []) if not s.get("image")]


# --------------------------------------------------------------------------
# projection into the renderer
# --------------------------------------------------------------------------

def render_scenes(storyboard, pdir):
    """Scene list with absolute image paths, for render.build_ffmpeg_command."""
    out = []
    for scene in storyboard.get("scenes", []):
        image = scene.get("image")
        path = Path(image) if image else None
        if path is not None and not path.is_absolute():
            path = (pdir / image).resolve()
        entry = dict(scene)
        entry["image_path"] = path
        out.append(entry)
    return out


def scene_summary(storyboard):
    """The compact view written into metadata.scenes.

    A pointer, not a copy: metadata records what a reviewer needs to see at a
    glance, and storyboard.json stays the detailed artefact.
    """
    return [
        {
            "scene_id": s["scene_id"],
            "section": s.get("section"),
            "duration_seconds": s.get("duration_seconds"),
            "motion": (s.get("motion") or {}).get("kind"),
            "transition": (s.get("transition") or {}).get("kind"),
            "image": s.get("image"),
            "job_id": (s.get("generation") or {}).get("job_id"),
        }
        for s in storyboard.get("scenes", [])
    ]


def load(video_id):
    path = storyboard_path(video_id)
    if not path.is_file():
        return None
    return json.loads(path.read_text())


def save(video_id, storyboard):
    path = storyboard_path(video_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    if storyboard.get("loop"):
        # Scenes can be edited after the build; the cycle is what they say.
        storyboard["loop"] = loop_record(
            storyboard.get("scenes", []), storyboard["loop"]["full_seconds"])
    storyboard["timeline_seconds"] = finished_seconds(storyboard)
    path.write_text(json.dumps(storyboard, indent=2) + "\n")
    return path


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

def _load_project_files(video_id):
    pdir = project_dir(video_id)
    spec_path = pdir / "video_spec.json"
    meta_path = pdir / "metadata.json"
    if not spec_path.is_file() or not meta_path.is_file():
        raise StoryboardError(
            f"not a project (missing video_spec.json/metadata.json): {pdir}")
    return pdir, json.loads(spec_path.read_text()), json.loads(meta_path.read_text())


def cmd_build(args):
    pdir, spec, metadata = _load_project_files(args.video_id)
    profile = research.load_profile(args.niche) if args.niche else None
    if args.niche and profile is None:
        log.warning("No format profile for niche %r; using defaults.", args.niche)

    storyboard = build_storyboard(
        args.video_id, metadata, spec, profile=profile,
        scene_count=args.scenes,
        source_width=args.source_width, source_height=args.source_height)

    existing = load(args.video_id)
    if existing and not args.force:
        # Carry forward images already generated for an identical request, so
        # rebuilding a storyboard does not throw away GPU work.
        by_digest = {
            (s.get("generation") or {}).get("request_digest"): s
            for s in existing.get("scenes", []) if s.get("image")
        }
        reused = 0
        for scene in storyboard["scenes"]:
            prior = by_digest.get(scene["generation"]["request_digest"])
            if prior:
                scene["image"] = prior["image"]
                scene["generation"].update({
                    "job_id": (prior.get("generation") or {}).get("job_id"),
                    "provider": (prior.get("generation") or {}).get("provider"),
                })
                reused += 1
        if reused:
            log.info("Reused %d already-generated scene image(s)", reused)

    path = save(args.video_id, storyboard)
    log.info("Storyboard: %d scene(s), %.2fs timeline (target %.2fs)",
             len(storyboard["scenes"]), storyboard["timeline_seconds"],
             storyboard["target"]["duration_seconds"])
    log.info("Source generation size: %dx%d (output %dx%d)",
             storyboard["source_generation"]["width"],
             storyboard["source_generation"]["height"],
             storyboard["target"]["width"], storyboard["target"]["height"])
    log.info("Written: %s", path)
    return 0


def cmd_show(args):
    storyboard = load(args.video_id)
    if storyboard is None:
        log.error("No storyboard for %s", args.video_id)
        return 1
    print(json.dumps(storyboard, indent=2))
    return 0


def cmd_validate(args):
    storyboard = load(args.video_id)
    if storyboard is None:
        log.error("No storyboard for %s", args.video_id)
        return 1
    problems = validate(storyboard, project_dir(args.video_id))
    if problems:
        log.error("Storyboard has %d problem(s):", len(problems))
        for p in problems:
            log.error("  - %s", p)
        return 1
    log.info("Storyboard OK: %d scene(s), %.2fs",
             len(storyboard["scenes"]), storyboard["timeline_seconds"])
    return 0


def main():
    parser = argparse.ArgumentParser(description="Scene plan for a project.")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("build", help="derive a storyboard from the project")
    p.add_argument("video_id")
    p.add_argument("--niche", default=None, help="format profile to shape it")
    p.add_argument("--scenes", type=int, default=None)
    p.add_argument("--source-width", type=int, default=None)
    p.add_argument("--source-height", type=int, default=None)
    p.add_argument("--force", action="store_true",
                   help="discard already-assigned scene images")
    p.set_defaults(func=cmd_build)

    p = sub.add_parser("show", help="print the storyboard")
    p.add_argument("video_id")
    p.set_defaults(func=cmd_show)

    p = sub.add_parser("validate", help="check it could be rendered")
    p.add_argument("video_id")
    p.set_defaults(func=cmd_validate)

    args = parser.parse_args()
    try:
        raise SystemExit(args.func(args))
    except StoryboardError as e:
        for problem in e.problems:
            log.error("  - %s", problem)
        raise SystemExit(1)


if __name__ == "__main__":
    main()
