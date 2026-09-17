#!/usr/bin/env python3
"""Still-image motion: deterministic ffmpeg filter construction.

One scene = one still image + one motion + one transition into the next
scene. This module owns *only* the translation from that vocabulary into an
ffmpeg filter fragment. It knows nothing about projects, storyboards,
generation or provenance, which is what lets a richer backend (parallax,
fog, grain, or a real image-to-video provider) be added later without any
of those layers changing.

The contract a backend implements:

    build_scene_filter(index, label, scene, target) -> filter string
        writes exactly one output pad named ``label``

``target`` carries width/height/fps. ``scene`` carries duration, motion and
fit. Everything is derived from those inputs alone - no clocks, no random
numbers, no environment - so the same storyboard always yields byte-identical
filter text, which is what makes the render reproducible and reviewable.

Source images are generated at a card-appropriate resolution (512x512 on the
current GPU worker) and are scaled up here. That is a *presentation* step:
the generation digest still records the size the image was really made at.
Nothing in this module may be used to claim otherwise.
"""
import logging

log = logging.getLogger("motion")

# Work at a multiple of the target before cropping, so pan/zoom steps land
# between source pixels instead of jumping a whole one. Same reasoning as
# render.KEN_BURNS_UPSCALE, kept separate because the scene path may tune it
# independently once parallax lands.
WORKING_UPSCALE = 2

# How far a zoom travels over a scene, and how much headroom a pan needs.
# Deliberately gentle: this is ambient/explainer motion, not a whip pan.
DEFAULT_ZOOM_AMOUNT = 0.12
DEFAULT_PAN_ZOOM = 1.18

# Every motion the first backend can render. Adding one here is the only
# place a new move has to be declared; storyboard validation reads this set,
# so an unknown motion is rejected before ffmpeg is ever launched.
MOTIONS = (
    "static",
    "zoom_in",
    "zoom_out",
    "pan_left",
    "pan_right",
    "pan_up",
    "pan_down",
    "pan_zoom",
)

MOTION_DESCRIPTIONS = {
    "static": "no camera move; the frame is held",
    "zoom_in": "slow push in, centred",
    "zoom_out": "slow pull out, centred",
    "pan_left": "camera travels left across the image",
    "pan_right": "camera travels right across the image",
    "pan_up": "camera travels up the image",
    "pan_down": "camera travels down the image",
    "pan_zoom": "push in while drifting right and down",
}

# How the next scene arrives. ``cut`` is a hard join; ``crossfade`` and its
# alias ``dissolve`` are the same ffmpeg xfade transition.
TRANSITIONS = ("cut", "crossfade", "dissolve")
_XFADE = {"crossfade": "fade", "dissolve": "fade"}

# How a source image that is not the target aspect ratio is fitted.
FITS = ("cover", "blur_pad")

BACKENDS = ("ffmpeg",)
DEFAULT_BACKEND = "ffmpeg"


class MotionError(Exception):
    """Raised when a motion/transition/fit cannot be rendered."""


def describe(motion):
    return MOTION_DESCRIPTIONS.get(motion, "unknown motion")


def validate_motion(motion, fit=None, transition=None):
    """Return a list of human-readable problems. Empty means renderable."""
    problems = []
    if motion not in MOTIONS:
        problems.append(
            f"unknown motion {motion!r}; supported: {', '.join(MOTIONS)}")
    if fit is not None and fit not in FITS:
        problems.append(f"unknown fit {fit!r}; supported: {', '.join(FITS)}")
    if transition is not None and transition not in TRANSITIONS:
        problems.append(
            f"unknown transition {transition!r}; supported: {', '.join(TRANSITIONS)}")
    return problems


def frames_for(duration_seconds, fps):
    """Frames a scene occupies. At least one, so a zero-length scene still
    produces a decodable pad rather than an ffmpeg error nobody can read."""
    return max(int(round(float(duration_seconds) * float(fps))), 1)


def _progress(frames):
    """zoompan expression for 0.0 -> 1.0 across the scene.

    ``on`` is the output frame counter. Clamped at both ends so the
    expression is safe whether the build of ffmpeg starts it at 0 or 1, and
    so the last frame lands exactly on 1.0 instead of overshooting.
    """
    if frames <= 1:
        return "0"
    return f"min(max((on-1)/{frames - 1},0),1)"


def _fit_chain(width, height, fit):
    """Scale a source of any aspect ratio to the working canvas.

    ``cover`` fills the frame and crops the overflow - the right default for
    a square source going to 16:9, since letterboxing a whole video is worse
    than losing the top and bottom of a plate.

    ``blur_pad`` keeps the whole source visible on a blurred enlargement of
    itself. Useful when the subject would be cropped away.
    """
    ws, hs = width * WORKING_UPSCALE, height * WORKING_UPSCALE
    if fit == "cover":
        return (f"scale={ws}:{hs}:force_original_aspect_ratio=increase,"
                f"crop={ws}:{hs}")
    if fit == "blur_pad":
        # split -> blurred cover as background, contained copy on top.
        return (
            f"split=2[bg][fg];"
            f"[bg]scale={ws}:{hs}:force_original_aspect_ratio=increase,"
            f"crop={ws}:{hs},boxblur=luma_radius={max(ws // 60, 2)}:luma_power=2[bgb];"
            f"[fg]scale={ws}:{hs}:force_original_aspect_ratio=decrease[fgs];"
            f"[bgb][fgs]overlay=(W-w)/2:(H-h)/2"
        )
    raise MotionError(f"unknown fit {fit!r}; supported: {', '.join(FITS)}")


def _zoompan(motion, frames, width, height, fps, amount):
    """The zoompan expressions for one motion.

    Absolute functions of the frame counter rather than ffmpeg's incremental
    ``zoom+step`` form: an absolute expression cannot drift, and it makes the
    filter text a pure function of the scene, which is what the tests pin.
    """
    p = _progress(frames)
    centre_x = "iw/2-(iw/zoom/2)"
    centre_y = "ih/2-(ih/zoom/2)"

    if motion == "static":
        z, x, y = "1", centre_x, centre_y
    elif motion == "zoom_in":
        z, x, y = f"1+{amount:.6f}*({p})", centre_x, centre_y
    elif motion == "zoom_out":
        z = f"{1 + amount:.6f}-{amount:.6f}*({p})"
        x, y = centre_x, centre_y
    elif motion in ("pan_left", "pan_right", "pan_up", "pan_down"):
        # A pan needs somewhere to travel, so it holds a fixed zoom and
        # moves the viewport across the headroom that zoom creates.
        held = 1 + amount
        z = f"{held:.6f}"
        span_x = "(iw-iw/zoom)"
        span_y = "(ih-ih/zoom)"
        if motion == "pan_right":
            x, y = f"{span_x}*({p})", centre_y
        elif motion == "pan_left":
            x, y = f"{span_x}*(1-({p}))", centre_y
        elif motion == "pan_down":
            x, y = centre_x, f"{span_y}*({p})"
        else:  # pan_up
            x, y = centre_x, f"{span_y}*(1-({p}))"
    elif motion == "pan_zoom":
        z = f"1+{amount:.6f}*({p})"
        x = f"(iw-iw/zoom)*(0.5+0.25*({p}))"
        y = f"(ih-ih/zoom)*(0.5+0.25*({p}))"
    else:
        raise MotionError(
            f"unknown motion {motion!r}; supported: {', '.join(MOTIONS)}")

    return (f"zoompan=z='{z}':d={frames}:x='{x}':y='{y}':"
            f"s={width}x{height}:fps={fps}")


def build_scene_filter(index, label, scene, target):
    """One input pad -> one finished scene pad.

    ``index`` is the ffmpeg input index, ``label`` the output pad name.
    Raises MotionError rather than emitting a filter that ffmpeg would
    reject with an unreadable message.
    """
    width = int(target["width"])
    height = int(target["height"])
    fps = target["fps"]

    motion_cfg = scene.get("motion") or {}
    kind = motion_cfg.get("kind", "static")
    fit = motion_cfg.get("fit", "cover")
    amount = motion_cfg.get("amount")
    if amount is None:
        amount = DEFAULT_PAN_ZOOM - 1 if kind.startswith("pan") else DEFAULT_ZOOM_AMOUNT
    amount = float(amount)
    if amount < 0:
        raise MotionError(f"motion amount must be >= 0, got {amount}")

    problems = validate_motion(kind, fit=fit)
    if problems:
        raise MotionError("; ".join(problems))

    frames = frames_for(scene["duration_seconds"], fps)
    chain = _fit_chain(width, height, fit)
    zoompan = _zoompan(kind, frames, width, height, fps, amount)
    return (f"[{index}:v]{chain},{zoompan},"
            f"format=yuv420p,setsar=1[{label}]")


def build_transition_chain(labels, scenes, out_prefix="sx"):
    """Join finished scene pads into one video pad.

    Returns ``(filter_parts, final_label, timeline_seconds)``.

    A crossfade *overlaps* two scenes, so the finished video is shorter than
    the sum of the scene durations. The overlap is returned rather than
    hidden: the caller writes it into the storyboard and QC compares it with
    the audio, so a timing disagreement is visible instead of being papered
    over by stretching or truncating narration.
    """
    if not labels:
        raise MotionError("no scenes to join")
    if len(labels) == 1:
        return [], labels[0], float(scenes[0]["duration_seconds"])

    parts = []
    current = labels[0]
    elapsed = float(scenes[0]["duration_seconds"])
    for i in range(1, len(labels)):
        prev = scenes[i - 1]
        transition = (prev.get("transition") or {})
        kind = transition.get("kind", "crossfade")
        if kind not in TRANSITIONS:
            raise MotionError(
                f"unknown transition {kind!r}; supported: {', '.join(TRANSITIONS)}")
        duration = float(transition.get("duration_seconds", 0.0) or 0.0)
        if kind == "cut":
            duration = 0.0
        if duration < 0:
            raise MotionError(f"transition duration must be >= 0, got {duration}")
        if duration >= float(prev["duration_seconds"]):
            raise MotionError(
                f"transition of {duration}s is not shorter than the "
                f"{prev['duration_seconds']}s scene it leaves "
                f"({prev.get('scene_id', f'scene {i - 1}')})")

        offset = elapsed - duration
        out = f"{out_prefix}{i}"
        if duration > 0:
            parts.append(
                f"[{current}][{labels[i]}]xfade=transition={_XFADE[kind]}:"
                f"duration={duration:.3f}:offset={offset:.3f}[{out}]")
        else:
            # A hard cut is an xfade of zero length in every other respect;
            # concat keeps the graph honest about that rather than emitting
            # a degenerate xfade ffmpeg may round differently.
            parts.append(f"[{current}][{labels[i]}]concat=n=2:v=1:a=0[{out}]")
        current = out
        elapsed = offset + float(scenes[i]["duration_seconds"])
    return parts, current, elapsed


def timeline_seconds(scenes):
    """Finished runtime of a scene list, accounting for crossfade overlap.

    Pure arithmetic, so the storyboard can be checked against the audio
    before a single frame is rendered.
    """
    if not scenes:
        return 0.0
    total = float(scenes[0]["duration_seconds"])
    for i in range(1, len(scenes)):
        transition = (scenes[i - 1].get("transition") or {})
        kind = transition.get("kind", "crossfade")
        overlap = 0.0 if kind == "cut" else float(
            transition.get("duration_seconds", 0.0) or 0.0)
        total += float(scenes[i]["duration_seconds"]) - overlap
    return round(total, 3)
