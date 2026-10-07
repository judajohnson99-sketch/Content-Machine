#!/usr/bin/env python3
"""Structured visual direction, and the compiler that turns it into prompts.

The problem this replaces: one free-text ``image_prompt`` written by a
model, reused verbatim for every scene, decorated with the tags that make
generated images look generated ("8k", "hyper-realistic", "masterpiece").
That produces ten copies of one picture and gives a reviewer nothing to
argue with.

Instead, a video carries a *visual direction document*: a small set of
named facets - palette, light, materials, camera, atmosphere, continuity
anchors - plus the environments it actually depicts and a shot plan across
them. Prompts are then **compiled** from that document, deterministically,
one per scene:

- the identity facets are the same in every scene, which is what makes a
  video look like one video;
- the environment, framing and light state differ, which is what keeps it
  from looking like one picture ten times;
- nothing goes in that a generator cannot draw, and the slop vocabulary is
  stripped rather than emitted;
- the wording is chosen for the target provider's prompt dialect, so a
  tag-conditioned diffusion model and a natural-language image model each
  get the form they actually respond to.

The document is one LLM call per video (``creative.generate_visual_direction``).
Everything in this module is pure and deterministic.
"""
import logging
import re

logging.basicConfig(level=logging.INFO, format="[%(levelname)s] %(message)s")
log = logging.getLogger("visual_direction")


# --------------------------------------------------------------------------
# closed vocabularies - the compiler varies within these, never outside
# --------------------------------------------------------------------------

# Framings, ordered so consecutive scenes alternate scale rather than
# drifting in one direction. A video that only ever cuts between wide shots
# reads as a slideshow; one that only ever shows details reads as unmoored.
FRAMINGS = {
    "establishing": "wide establishing view, full depth of the space visible",
    "medium": "medium shot, the space read at human distance",
    "detail": "close detail study, a single surface filling the frame",
    "elevated": "slightly elevated three-quarter view looking down into the space",
    "low": "low vantage near floor level, looking slightly up",
    "through": "framed through a foreground element, the subject beyond it",
}
DEFAULT_FRAMING_CYCLE = ("establishing", "medium", "detail", "through",
                         "elevated", "medium", "detail", "low")

# Depth-of-field intents. The point is that *something* is chosen: an
# unstated depth of field is where diffusion models default to a flat,
# evenly-sharp plate.
DEPTH_OF_FIELD = {
    "deep": "deep focus, near and far both sharp",
    "shallow": "shallow depth of field, background falling softly out of focus",
    "medium": "moderate depth of field, foreground sharp and distance gently soft",
}

# How the light differs between scenes without breaking continuity. These
# are shifts within one lighting setup, not different times of day - the
# identity's own lighting phrase still governs.
LIGHT_STATES = {
    "steady": "",
    "softer": "light a touch softer and more diffuse here",
    "raking": "light raking across the surfaces at a low angle",
    "pooled": "light pooling in one part of the frame, the rest in gentle shadow",
    "backlit": "the light source behind the subject, edges rimmed",
}
DEFAULT_LIGHT_CYCLE = ("steady", "softer", "raking", "steady", "pooled",
                       "softer", "backlit", "steady")

# Vocabulary that marks a prompt as machine-written and buys nothing. These
# are stripped from anything that reaches a generator - including from a
# model's own suggestion, which is where most of them come from.
SLOP_TERMS = (
    "8k", "4k", "16k", "uhd", "hyper-realistic", "hyperrealistic",
    "ultra realistic", "ultra-realistic", "ultra detailed", "ultra-detailed",
    "extremely detailed", "highly detailed", "intricate details",
    "masterpiece", "best quality", "high quality", "award winning",
    "award-winning", "trending on artstation", "artstation", "unreal engine",
    "octane render", "cgsociety", "stunning", "breathtaking", "epic",
    "professional photography", "dslr", "sharp focus", "vivid colors",
    "beautiful lighting", "perfect composition", "cinematic masterpiece",
)

# Failure modes worth naming to a model that accepts a negative prompt.
# Generic ones only: anything concept-specific belongs in the direction's
# own avoid list, which is appended to this.
GENERATION_FAILURES = (
    "text", "letters", "watermark", "signature", "logo", "caption",
    "deformed geometry", "warped perspective", "duplicated objects",
    "melted edges", "smeared detail", "oversaturated colour",
    "harsh flash lighting", "plastic sheen", "jpeg artifacts",
    "lowres", "blurry", "washed out", "cluttered frame", "visual noise",
)

# Words that name something a still picture cannot contain. A video's
# avoid-list and its project negative prompt are written for the whole
# deliverable - sound design, pacing, edit rhythm - and that language is
# harmless prose until it is handed to an image model, which has to spend
# conditioning on "storm sounds" and "rapid cuts" instead of on the frame.
# Stripped from the concept-supplied sources only; GENERATION_FAILURES
# below is about pictures by construction and always survives.
NON_VISUAL_TERMS = (
    # sound
    "sound", "sounds", "audio", "music", "musical", "melody", "melodies",
    "percussion", "frequency", "frequencies", "thunder", "bark", "barking",
    "whine", "whining", "squeaker", "squeakers", "loud", "silence", "sonic",
    "rumble", "hiss", "echo", "reverb",
    # time, motion and edit rhythm
    "motion", "movement", "moving", "flicker", "flickering", "flashing",
    "strobe", "animation", "animated", "loop", "loops", "looping", "cut",
    "cuts", "transition", "transitions", "pacing", "tempo", "rhythm",
    "sudden", "abrupt", "rapid", "fast", "changes", "shifts",
)

# The two prompt dialects this codebase targets. `tag` is the comma-
# separated, negative-prompt-carrying form SD-family checkpoints were
# trained on; `natural` is the ordinary-sentence form the hosted image
# models want, where a comma salad actively hurts.
PROMPT_STYLES = ("tag", "natural")

MAX_PALETTE_TERMS = 5
MAX_MATERIAL_TERMS = 5
MAX_ANCHORS = 4
MAX_ENVIRONMENTS = 8
MAX_AVOID = 10


# --------------------------------------------------------------------------
# sanitisation
# --------------------------------------------------------------------------

def _strip_slop(text):
    """Remove the generic-quality vocabulary, return (clean, removed)."""
    if not text:
        return "", []
    removed = []
    clean = str(text)
    for term in SLOP_TERMS:
        pattern = re.compile(r"(?<![\w-])" + re.escape(term) + r"(?![\w-])",
                             re.IGNORECASE)
        if pattern.search(clean):
            removed.append(term)
            clean = pattern.sub("", clean)
    # Collapse the punctuation the removals left behind.
    clean = re.sub(r"\s*,\s*(,\s*)+", ", ", clean)
    clean = re.sub(r"\s{2,}", " ", clean).strip().strip(",").strip()
    return clean, removed


def _terms(value, limit):
    """A bounded list of short terms from a list or a comma-separated string."""
    if value is None:
        return []
    if isinstance(value, str):
        parts = [p.strip() for p in value.split(",")]
    elif isinstance(value, (list, tuple)):
        parts = [str(p).strip() for p in value]
    else:
        return []
    out = []
    for part in parts:
        clean, _ = _strip_slop(part)
        if clean and clean.lower() not in {o.lower() for o in out}:
            out.append(clean)
        if len(out) >= limit:
            break
    return out


def _phrase(value, default=""):
    clean, _ = _strip_slop(value if isinstance(value, str) else "")
    return clean or default


def sanitize_direction(raw, scene_count=None):
    """Coerce a direction document into something the compiler can use.

    Bounded, vocabulary-checked and slop-free. A reply that gets everything
    wrong still yields a usable document rather than an exception: the
    compiler's job is to produce a prompt, and a direction missing a facet
    simply contributes nothing for that facet.
    """
    raw = raw if isinstance(raw, dict) else {}
    identity = raw.get("identity") if isinstance(raw.get("identity"), dict) else {}
    camera = identity.get("camera") if isinstance(identity.get("camera"), dict) else {}
    stripped = []

    for source in (identity.get("lighting"), identity.get("atmosphere"),
                   identity.get("render_intent")):
        _, removed = _strip_slop(source if isinstance(source, str) else "")
        stripped.extend(removed)

    dof = str(camera.get("depth_of_field", "")).lower()
    direction = {
        "identity": {
            "palette": _terms(identity.get("palette"), MAX_PALETTE_TERMS),
            "lighting": _phrase(identity.get("lighting")),
            "atmosphere": _phrase(identity.get("atmosphere")),
            "materials": _terms(identity.get("materials"), MAX_MATERIAL_TERMS),
            "continuity_anchors": _terms(identity.get("continuity_anchors"), MAX_ANCHORS),
            "camera": {
                "lens": _phrase(camera.get("lens")),
                "perspective": _phrase(camera.get("perspective")),
                "depth_of_field": dof if dof in DEPTH_OF_FIELD else "medium",
            },
            "render_intent": _phrase(identity.get("render_intent")),
        },
        "avoid": _terms(raw.get("avoid"), MAX_AVOID),
        "reasoning": _phrase(raw.get("reasoning")),
        "environments": [],
        "slop_removed": sorted(set(stripped)),
    }

    for entry in (raw.get("environments") or [])[:MAX_ENVIRONMENTS]:
        if isinstance(entry, str):
            entry = {"description": entry}
        if not isinstance(entry, dict):
            continue
        description = _phrase(entry.get("description"))
        if not description:
            continue
        direction["environments"].append({
            "slug": _phrase(entry.get("slug")) or f"env{len(direction['environments']) + 1}",
            "description": description,
            "focal_point": _phrase(entry.get("focal_point")),
            "scale": _phrase(entry.get("scale")),
        })

    if scene_count:
        direction["environments"] = direction["environments"][:max(1, min(
            MAX_ENVIRONMENTS, scene_count))] or direction["environments"]
    return direction


def is_usable(direction):
    """Whether a direction has enough in it to compile a better prompt.

    A document with no environments and no identity facets would compile to
    the base prompt with decoration, which is worse than leaving the base
    prompt alone - so callers check this and fall back honestly.
    """
    if not direction:
        return False
    identity = direction.get("identity") or {}
    facets = any(identity.get(key) for key in
                 ("palette", "lighting", "atmosphere", "materials", "render_intent"))
    return bool(direction.get("environments")) and facets


# --------------------------------------------------------------------------
# the shot plan - which environment, framing and light each scene gets
# --------------------------------------------------------------------------

def shot_plan(direction, scenes, framing_cycle=DEFAULT_FRAMING_CYCLE,
              light_cycle=DEFAULT_LIGHT_CYCLE, max_distinct=None):
    """Assign every scene an environment, a framing and a light state.

    Environments cycle slowest and framings fastest, with cycle lengths
    that are deliberately not equal: if both advanced together, scene 9
    would repeat scene 1 exactly. Offsetting them means a returning
    environment returns *differently* - the same place, seen another way -
    which is how a long video stays coherent without becoming a loop.
    """
    environments = direction.get("environments") or []
    if not environments:
        return []
    plan = []
    # A half-hour video is two hundred shots, and two hundred separately
    # generated pictures is not a quality decision, it is a bill. Beyond the
    # budget the same shots come back - in a different order, under different
    # motion - which is how long-form ambient has always been cut.
    period = len(scenes)
    if max_distinct and max_distinct < len(scenes):
        period = max(int(max_distinct), 1)
    for index, scene in enumerate(scenes):
        slot = index % period
        environment = environments[slot % len(environments)]
        framing = framing_cycle[slot % len(framing_cycle)]
        light = light_cycle[(slot + slot // max(len(environments), 1))
                            % len(light_cycle)]
        plan.append({
            "scene_id": scene["scene_id"],
            "environment": environment["slug"],
            "framing": framing,
            "light_state": light,
        })
    return plan


# --------------------------------------------------------------------------
# compilation
# --------------------------------------------------------------------------

def compile_scene_prompt(direction, environment, framing="medium",
                         light_state="steady", style="tag"):
    """One scene's prompt, assembled from the direction in a fixed order.

    Order is not cosmetic. Diffusion models weight earlier tokens more, so
    the subject and its environment lead; composition and light come next
    because they are what distinguishes this scene from its neighbours;
    palette, material and atmosphere follow as the identity that must not
    vary; the camera and render intent sit last, where they colour
    everything without displacing it.
    """
    identity = direction.get("identity") or {}
    camera = identity.get("camera") or {}
    parts = []

    description = (environment or {}).get("description", "")
    if description:
        parts.append(description)
    focal = (environment or {}).get("focal_point")
    if focal:
        parts.append(focal)
    # The environment's own scale ("medium interior view") and the scene's
    # framing ("close detail study") are both shot-size instructions, and
    # the scale is constant across the whole video. Emitting both put a
    # fixed shot size in front of a varying one in every prompt, and the
    # fixed one won: ten scenes that cycled through six framings rendered
    # as ten versions of the same medium wide shot. Whichever the scene
    # chose governs; the scale only speaks when nothing else does.
    scale = (environment or {}).get("scale")
    if framing in FRAMINGS:
        parts.append(FRAMINGS[framing])
    elif scale:
        parts.append(scale)
    light_phrase = LIGHT_STATES.get(light_state, "")
    if identity.get("lighting"):
        parts.append(identity["lighting"])
    if light_phrase:
        parts.append(light_phrase)
    if identity.get("palette"):
        parts.append(", ".join(identity["palette"]) + " palette")
    if identity.get("materials"):
        parts.append(", ".join(identity["materials"]))
    if identity.get("continuity_anchors"):
        parts.append(", ".join(identity["continuity_anchors"]))
    if identity.get("atmosphere"):
        parts.append(identity["atmosphere"])
    if camera.get("lens"):
        parts.append(camera["lens"])
    if camera.get("perspective"):
        parts.append(camera["perspective"])
    parts.append(DEPTH_OF_FIELD.get(camera.get("depth_of_field", "medium"),
                                    DEPTH_OF_FIELD["medium"]))
    if identity.get("render_intent"):
        parts.append(identity["render_intent"])

    parts = [p.strip().strip(",").strip() for p in parts if p and p.strip()]
    if style == "tag":
        # A facet written as a sentence ("...beside a low window frame.")
        # would otherwise join the next tag as ". ,", which reads to a
        # tag-conditioned model as an empty tag.
        parts = [p.rstrip(".").strip() for p in parts]
        parts = [p for p in parts if p]
    if style == "natural":
        # One model reads a comma salad as a list of things to include and
        # another reads it as a sentence it must parse. For the second kind,
        # give it sentences.
        head = parts[0][0].upper() + parts[0][1:] if parts else ""
        rest = "; ".join(parts[1:])
        return f"{head}. {rest}." if rest else f"{head}."
    return ", ".join(parts)


_STOPWORDS = frozenset((
    "and", "the", "with", "that", "this", "from", "into", "over", "very",
    "any", "all", "its", "for", "not", "too",
))


def _significant_words(term):
    return [w for w in re.findall(r"[a-z]+", term.lower())
            if len(w) > 3 and w not in _STOPWORDS]


def _is_non_visual(term):
    """Whether a negative term describes sound, pacing or edit rhythm."""
    words = set(re.findall(r"[a-z]+", term.lower()))
    return bool(words & set(NON_VISUAL_TERMS))


def _contradicts(term, positive):
    """Whether the positive prompts already ask for what this excludes.

    A negative that repeats the subject ("water" against a prompt built on
    condensation and pooling moisture) does not remove a failure mode, it
    fights the picture the direction asked for.
    """
    if not positive:
        return False
    words = _significant_words(term)
    if not words:
        return False
    # Whole words only: "text" must survive a prompt that says "textured".
    return all(re.search(r"(?<![a-z])" + re.escape(w) + r"(?![a-z])", positive)
               for w in words)


def compile_negative_prompt(direction, style="tag", base=None, positives=None):
    """What to keep out. Empty for dialects that have no negative channel.

    The concept-supplied sources - the project's negative prompt and the
    direction's avoid list - are written about the finished video, so they
    are filtered twice before they reach an image model: once for language
    that names something a frame cannot hold, and once for terms the
    compiled prompts themselves ask for.
    """
    if style != "tag":
        return ""
    positive = " ".join(positives or ()).lower()
    terms, dropped = [], []
    for source, filtered in ((base, True),
                             (direction.get("avoid") if direction else None, True),
                             (GENERATION_FAILURES, False)):
        for term in _terms(source, 40):
            if term.lower() in {t.lower() for t in terms}:
                continue
            if filtered:
                if _is_non_visual(term):
                    dropped.append((term, "not visual"))
                    continue
                if _contradicts(term, positive):
                    dropped.append((term, "asked for by the prompt"))
                    continue
            terms.append(term)
    if dropped:
        log.info("Negative prompt: dropped %s",
                 ", ".join(f"{t!r} ({why})" for t, why in dropped))
    return ", ".join(terms)


def compile_scene_prompts(direction, scenes, style="tag", base_negative=None,
                          max_distinct=None):
    """``{scene_id: prompt}`` for a whole storyboard, plus the shot plan.

    Returns ``(prompts, negative_prompt, plan)``. The mapping is exactly
    the shape ``storyboard.apply_scene_motifs`` already consumes, so this
    drops into the existing call site without a second wiring path.
    """
    plan = shot_plan(direction, scenes, max_distinct=max_distinct)
    by_slug = {env["slug"]: env for env in direction.get("environments") or []}
    prompts = {}
    for entry in plan:
        prompts[entry["scene_id"]] = compile_scene_prompt(
            direction, by_slug.get(entry["environment"]),
            framing=entry["framing"], light_state=entry["light_state"],
            style=style)
    return (prompts,
            compile_negative_prompt(direction, style, base_negative,
                                    positives=prompts.values()),
            plan)


def distinct_prompt_count(prompts):
    """How many genuinely different pictures a mapping asks for."""
    return len({p.strip().lower() for p in prompts.values() if p})
