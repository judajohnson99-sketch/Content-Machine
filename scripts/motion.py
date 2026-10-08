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
import hashlib
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
    "drift",
    "parallax",
    "parallax_in",
)

# Moves built from more than one layer of the same still. Listed so the
# storyboard can tell them apart from single-layer zoompan moves.
LAYERED_MOTIONS = ("parallax", "parallax_in")

MOTION_DESCRIPTIONS = {
    "static": "no camera move; the frame is held",
    "zoom_in": "slow push in, centred",
    "zoom_out": "slow pull out, centred",
    "pan_left": "camera travels left across the image",
    "pan_right": "camera travels right across the image",
    "pan_up": "camera travels up the image",
    "pan_down": "camera travels down the image",
    "pan_zoom": "push in while drifting right and down",
    "drift": "very slow push with a slight rotation and a diagonal float",
    "parallax": "2.5D orbit: a sharp, feathered foreground glides one way "
                "over a soft, blurred background gliding the other",
    "parallax_in": "2.5D dolly: the sharp foreground pushes in faster than "
                   "the soft background behind it",
}

# Optional atmosphere laid over a scene's finished move. ``fog`` is slow,
# soft light/haze built from a low-resolution sine field (no random numbers,
# so it is as reproducible as the move under it), upscaled so it has no
# texture of its own, and composited at low opacity.
AMBIENTS = ("none", "fog")

# Drift: how far the slow zoom travels, how much it rotates end to end, and
# how much larger than the frame the layer is rendered so a rotation never
# shows a corner. 0.006 rad is about a third of a degree - felt, not seen.
DEFAULT_DRIFT_AMOUNT = 0.06
DRIFT_ROTATION_RADIANS = 0.006
DRIFT_MARGIN = 1.04
DRIFT_DIRECTIONS = ("ne", "nw", "se", "sw")

# Parallax: foreground zoom headroom and how much of it is travelled, and how
# soft/dark the background layer is. Small on purpose: a single still has no
# depth map, so the two layers are the same picture and a large offset would
# read as a double exposure instead of depth.
DEFAULT_PARALLAX_AMOUNT = 0.10
PARALLAX_BG_BLUR_SIGMA = 4
PARALLAX_BG_BRIGHTNESS = -0.05

# Fog: peak opacity of the haze and the colour it lightens toward.
FOG_OPACITY = 0.16
FOG_COLOUR = "0xdde3ee"
FOG_FIELD = (160, 90)

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


def validate_motion(motion, fit=None, transition=None, ambient=None):
    """Return a list of human-readable problems. Empty means renderable."""
    problems = []
    if motion not in MOTIONS:
        problems.append(
            f"unknown motion {motion!r}; supported: {', '.join(MOTIONS)}")
    if ambient is not None and ambient not in AMBIENTS:
        problems.append(
            f"unknown ambient {ambient!r}; supported: {', '.join(AMBIENTS)}")
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


def _fit_chain(width, height, fit, prefix=""):
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
            f"split=2[{prefix}bg][{prefix}fg];"
            f"[{prefix}bg]scale={ws}:{hs}:force_original_aspect_ratio=increase,"
            f"crop={ws}:{hs},boxblur=luma_radius={max(ws // 60, 2)}:luma_power=2[{prefix}bgb];"
            f"[{prefix}fg]scale={ws}:{hs}:force_original_aspect_ratio=decrease[{prefix}fgs];"
            f"[{prefix}bgb][{prefix}fgs]overlay=(W-w)/2:(H-h)/2"
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


def _even(value):
    value = int(round(value))
    return value + (value % 2)


def _direction_for(scene, motion_cfg):
    """Which diagonal a drift floats along.

    Stated in the scene when the storyboard chose one; otherwise derived
    from the scene id with SHA-256 (never ``hash()``, which is salted per
    process), so the same scene always floats the same way.
    """
    direction = motion_cfg.get("direction")
    if direction in DRIFT_DIRECTIONS:
        return direction
    if direction is not None:
        raise MotionError(
            f"unknown drift direction {direction!r}; supported: "
            f"{', '.join(DRIFT_DIRECTIONS)}")
    key = str(scene.get("scene_id") or "")
    digest = hashlib.sha256(key.encode("utf-8")).hexdigest()
    return DRIFT_DIRECTIONS[int(digest[:8], 16) % len(DRIFT_DIRECTIONS)]


def _drift_chain(frames, width, height, fps, amount, direction):
    """A barely-there move for content meant to be fallen asleep to.

    The zoom creeps in by ``amount``, the viewport floats a little along one
    diagonal, and the whole frame turns about a third of a degree across the
    scene. The layer is rendered a few percent larger than the frame and
    rotated before the final crop, so the rotation never exposes a corner.
    Rotation is bilinear, so the move stays sub-pixel smooth at sleep speed.
    """
    p = _progress(frames)
    mw, mh = _even(width * DRIFT_MARGIN), _even(height * DRIFT_MARGIN)
    dx = 1 if direction in ("ne", "se") else -1
    dy = -1 if direction in ("ne", "nw") else 1
    z = f"1.04+{amount:.6f}*({p})"
    x = f"(iw-iw/zoom)*(0.5{0.35 * dx:+.2f}*(({p})-0.5))"
    y = f"(ih-ih/zoom)*(0.5{0.35 * dy:+.2f}*(({p})-0.5))"
    turn = DRIFT_ROTATION_RADIANS * dx
    rotate_p = "0" if frames <= 1 else f"min(n/{frames - 1},1)"
    return (f"zoompan=z='{z}':d={frames}:x='{x}':y='{y}':"
            f"s={mw}x{mh}:fps={fps},"
            f"rotate=a='{turn:.6f}*(2*{rotate_p}-1)':ow=iw:oh=ih:c=black,"
            f"crop={width}:{height}")


# Feathered ellipse: opaque in the middle, fading to nothing over the outer
# band, so the sharp layer has no edge a viewer could see move.
_FEATHER_MASK = ("255*clip((1-hypot((X-W/2)/(W*0.47),(Y-H/2)/(H*0.45)))"
                 "*3.2,0,1)")


def _parallax_parts(fit_chain, frames, width, height, fps, amount, motion,
                    source, out, prefix):
    """Two layers of one still moving at different rates.

    Everything before ``zoompan`` runs once on the single source frame (the
    blur, the darkening, the alpha mask), so the per-frame cost is two
    zoompans and one overlay - not a blur of every frame. That is what lets
    a long-form cycle afford this move on a CPU host.

    ``parallax``: an orbit - the sharp foreground travels one way across
    its headroom while the soft background travels the other.
    ``parallax_in``: a dolly - the foreground pushes in several times faster
    than the background, which barely moves.
    """
    p = _progress(frames)
    centre_x = "iw/2-(iw/zoom/2)"
    centre_y = "ih/2-(ih/zoom/2)"
    if motion == "parallax":
        bg_z = f"{1 + amount * 1.6:.6f}"
        bg_x = f"(iw-iw/zoom)*(0.5+0.30*(({p})-0.5))"
        fg_z = f"{1 + amount:.6f}"
        fg_x = f"(iw-iw/zoom)*(0.5-0.80*(({p})-0.5))"
        bg_y = fg_y = centre_y
    else:  # parallax_in
        bg_z = f"{1 + amount:.6f}+{amount * 0.25:.6f}*({p})"
        fg_z = f"1+{amount * 1.5:.6f}*({p})"
        bg_x = fg_x = centre_x
        bg_y = fg_y = centre_y

    def zp(z, x, y):
        return (f"zoompan=z='{z}':d={frames}:x='{x}':y='{y}':"
                f"s={width}x{height}:fps={fps}")

    b, f = f"{prefix}b", f"{prefix}f"
    ws, hs = width * WORKING_UPSCALE, height * WORKING_UPSCALE
    return [
        f"[{source}]{fit_chain},split=2[{b}0][{f}0]",
        # Blur at an eighth of the size: the same softness for a fraction of
        # the work, and it runs once per scene, not once per frame.
        (f"[{b}0]scale={max(ws // 8, 2)}:{max(hs // 8, 2)},"
         f"gblur=sigma={PARALLAX_BG_BLUR_SIGMA},"
         f"scale={ws}:{hs},eq=brightness={PARALLAX_BG_BRIGHTNESS}:saturation=0.9,"
         f"{zp(bg_z, bg_x, bg_y)}[{b}1]"),
        (f"[{f}0]format=yuva420p,geq=lum='lum(X,Y)':cb='cb(X,Y)':"
         f"cr='cr(X,Y)':a='{_FEATHER_MASK}',{zp(fg_z, fg_x, fg_y)}[{f}1]"),
        f"[{b}1][{f}1]overlay=0:0:shortest=1[{out}]",
    ]


def _fog_parts(base, out, frames, width, height, fps, prefix):
    """Slow soft haze over a finished scene pad.

    A 160x90 field of a few slow sine waves - deterministic, no noise
    generator - upscaled bicubically to the frame (which is all the blur it
    needs), used as the alpha of a pale colour layer and laid over the
    scene. Peak opacity is FOG_OPACITY, so it lifts the shadows a little and
    never hides the picture.
    """
    fw, fh = FOG_FIELD
    seconds = frames / float(fps) + 1.0
    peak = round(255 * FOG_OPACITY)
    field = (f"{peak}*clip(0.45+0.22*sin(X/11+T*0.13)+0.22*sin(Y/7-T*0.09)"
             f"+0.18*sin((X+Y)/17+T*0.07)-0.15,0,1)")
    return [
        (f"color=c={FOG_COLOUR}:s={width}x{height}:r={fps}:d={seconds:.3f},"
         f"format=yuva420p[{prefix}fc]"),
        (f"color=c=black:s={fw}x{fh}:r={fps}:d={seconds:.3f},format=gray,"
         f"geq=lum='{field}',scale={width}:{height}:flags=bicubic[{prefix}fa]"),
        f"[{prefix}fc][{prefix}fa]alphamerge[{prefix}fg]",
        f"[{base}][{prefix}fg]overlay=0:0:shortest=1[{out}]",
    ]


def build_scene_filter(index, label, scene, target):
    """One input pad -> one finished scene pad.

    ``index`` is the ffmpeg input index, ``label`` the output pad name.
    Raises MotionError rather than emitting a filter that ffmpeg would
    reject with an unreadable message. Layered moves and the fog overlay
    expand to several ``;``-joined chains; every intermediate pad is named
    after ``label`` so many scenes can share one filter graph.
    """
    width = int(target["width"])
    height = int(target["height"])
    fps = target["fps"]

    motion_cfg = scene.get("motion") or {}
    kind = motion_cfg.get("kind", "static")
    fit = motion_cfg.get("fit", "cover")
    ambient = motion_cfg.get("ambient") or "none"
    amount = motion_cfg.get("amount")
    if amount is None:
        if kind.startswith("pan"):
            amount = DEFAULT_PAN_ZOOM - 1
        elif kind == "drift":
            amount = DEFAULT_DRIFT_AMOUNT
        elif kind in LAYERED_MOTIONS:
            amount = DEFAULT_PARALLAX_AMOUNT
        else:
            amount = DEFAULT_ZOOM_AMOUNT
    amount = float(amount)
    if amount < 0:
        raise MotionError(f"motion amount must be >= 0, got {amount}")

    problems = validate_motion(kind, fit=fit, ambient=ambient)
    if problems:
        raise MotionError("; ".join(problems))

    frames = frames_for(scene["duration_seconds"], fps)
    chain = _fit_chain(width, height, fit, prefix=f"{label}_")
    moved = f"{label}_mv"
    finish = "format=yuv420p,setsar=1"

    if kind in LAYERED_MOTIONS:
        parts = _parallax_parts(chain, frames, width, height, fps, amount, kind,
                                f"{index}:v", moved, f"{label}_")
    elif kind == "drift":
        drift = _drift_chain(frames, width, height, fps, amount,
                             _direction_for(scene, motion_cfg))
        parts = [f"[{index}:v]{chain},{drift}[{moved}]"]
    else:
        zoompan = _zoompan(kind, frames, width, height, fps, amount)
        if ambient == "none":
            # The original single-chain form, byte for byte.
            return f"[{index}:v]{chain},{zoompan},{finish}[{label}]"
        parts = [f"[{index}:v]{chain},{zoompan}[{moved}]"]

    if ambient == "fog":
        fogged = f"{label}_fo"
        parts += _fog_parts(moved, fogged, frames, width, height, fps, f"{label}_")
        moved = fogged
    parts.append(f"[{moved}]{finish}[{label}]")
    return ";".join(parts)


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


def scene_start_times(scenes):
    """When each scene begins on the finished timeline.

    Same crossfade arithmetic as timeline_seconds(), so a caller that needs
    to point at one scene's frames (thumbnail extraction) lands inside that
    scene rather than near it.
    """
    starts = []
    position = 0.0
    for index, scene in enumerate(scenes):
        if index:
            previous = scenes[index - 1]
            transition = previous.get("transition") or {}
            overlap = 0.0 if transition.get("kind", "crossfade") == "cut" else float(
                transition.get("duration_seconds", 0.0) or 0.0)
            position += float(previous["duration_seconds"]) - overlap
        starts.append(round(position, 3))
    return starts


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


def cycle_seconds(scenes):
    """Runtime of a scene list played as a seamless loop.

    The last scene dissolves back into the first, so one cycle is the
    linear timeline minus that closing overlap. Repeating it N times is
    N * cycle_seconds with no seam, which is what lets a three-hour video
    be a stream copy of a twenty-minute one.
    """
    if not scenes:
        return 0.0
    last = scenes[-1].get("transition") or {}
    closing = 0.0 if last.get("kind", "crossfade") == "cut" else float(
        last.get("duration_seconds", 0.0) or 0.0)
    return round(timeline_seconds(scenes) - closing, 3)
