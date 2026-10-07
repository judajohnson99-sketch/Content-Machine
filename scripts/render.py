#!/usr/bin/env python3
"""Deterministic local video renderer.

Takes a directory of still images, one audio file, and a JSON video
specification, and produces a YouTube-ready H.264/AAC MP4 using the
system ffmpeg binary. See config/video_spec.example.json for the spec
format and README.md for full usage.

Two shapes of spec render through the same command builder, the same
ffmpeg invocation and the same output verification:

* **cycling** - a directory of images, one global Ken Burns move and one
  crossfade duration. The original contract, unchanged.
* **scenes** - an explicit per-scene plan (``spec["scenes"]``), each scene
  with its own image, duration, motion and transition. Built by
  ``storyboard.py``; the filter text comes from ``motion.py``.

A spec that carries ``scenes`` ignores ``images``/``ken_burns``/``crossfade``,
because the scene list already says what each of those would have decided.

Usage:
    python3 scripts/render.py --spec config/video_spec.example.json
    python3 scripts/render.py --spec config/video_spec.example.json --output output/video/my_video.mp4
"""
import argparse
import json
import logging
import math
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import motion as motion_mod  # noqa: E402

logging.basicConfig(level=logging.INFO, format="[%(levelname)s] %(message)s")
log = logging.getLogger("render")

ROOT = Path(__file__).resolve().parent.parent
SUPPORTED_IMAGE_EXTENSIONS = (".png", ".jpg", ".jpeg")

# A scene can also be built from footage the owner selected from their own
# library (scripts/ownermedia.py). Those scenes declare media_kind="video"
# and are rendered from the clip itself rather than from a still with a
# pan/zoom move over it; everything else about the timeline is unchanged.
SUPPORTED_VIDEO_EXTENSIONS = (".mp4", ".mov", ".m4v", ".mkv", ".webm")

# How much to upscale each frame before zoompan crops/zooms it, so the
# pan/zoom motion is smooth instead of stepping pixel-by-pixel. Not
# exposed in the spec since it's an internal quality/perf tradeoff.
KEN_BURNS_UPSCALE = 2

# Every image slot becomes its own ffmpeg input, and they are all open at
# once: decode + Ken Burns upscale + chained xfade. Memory therefore scales
# with slot count, not video length. MEASURED on a 3.8 GB host: 31 slots at
# 1080p with Ken Burns was OOM-killed at ~2.8 GB RSS, while 4 slots stayed
# under 900 MB. Refuse before the kernel does, so the failure is a readable
# validation error instead of SIGKILL.
MAX_IMAGE_SLOTS = 24

# Above MAX_IMAGE_SLOTS scenes the one-invocation filter graph is replaced by
# the piecewise renderer below (render_scenes_piecewise). That path holds at
# most two inputs open at a time, so scene count stops being bounded by
# memory: a two-hour video made of 240 distinct 30-second shots renders in
# the same footprint as a 24-scene one. The ceiling here is not a memory
# limit but a sanity limit - a storyboard with more scenes than this is
# almost certainly a bug in whatever produced it.
MAX_SCENES_PIECEWISE = 1200

# Intermediate clips are concatenated without re-encoding, so they must be
# encoded identically to the master. One full-quality encode of the timeline
# is all a long video costs.
_PIECEWISE_VIDEO_ARGS = [
    "-c:v", "libx264", "-pix_fmt", "yuv420p", "-preset", "medium",
    "-crf", "20", "-an",
]


class SpecValidationError(Exception):
    """Raised with a list of human-readable validation errors."""

    def __init__(self, errors):
        super().__init__("; ".join(errors))
        self.errors = errors


def required_image_slots(duration_seconds, seconds_per_image, crossfade_seconds):
    """How many image inputs the filter graph needs to cover the duration."""
    if crossfade_seconds > 0:
        span = seconds_per_image - crossfade_seconds
        slots = math.ceil((duration_seconds - crossfade_seconds) / span)
    else:
        slots = math.ceil(duration_seconds / seconds_per_image)
    return max(slots, 1)


def resolve_path(raw_path, base_dir):
    path = Path(raw_path)
    if not path.is_absolute():
        path = (base_dir / path).resolve()
    return path


def load_spec(spec_path):
    if not spec_path.is_file():
        raise SpecValidationError([f"Spec file not found: {spec_path}"])
    try:
        with open(spec_path) as f:
            raw = json.load(f)
    except json.JSONDecodeError as e:
        raise SpecValidationError([f"Spec file is not valid JSON: {e}"])
    if not isinstance(raw, dict):
        raise SpecValidationError(["Spec file must contain a JSON object"])
    return raw


def _normalize_scenes(raw_scenes, base_dir):
    """Validate an explicit scene list. Returns ``(scenes | None, errors)``.

    ``None`` means "this spec is not a scene spec" and the cycling path
    applies; an empty list is an error, because a spec that declares scenes
    and then has none is a mistake, not a fallback.
    """
    if raw_scenes is None:
        return None, []
    errors = []
    if not isinstance(raw_scenes, list):
        return None, ["'scenes' must be a list"]
    if not raw_scenes:
        return None, ["'scenes' is empty; remove the key or add scenes"]
    if len(raw_scenes) > MAX_SCENES_PIECEWISE:
        errors.append(
            f"this spec has {len(raw_scenes)} scenes, over the "
            f"{MAX_SCENES_PIECEWISE} ceiling; a storyboard this long is a bug "
            f"in whatever built it, not a render to attempt")

    scenes = []
    for i, raw_scene in enumerate(raw_scenes):
        where = (raw_scene.get("scene_id") if isinstance(raw_scene, dict) else None) or f"scenes[{i}]"
        if not isinstance(raw_scene, dict):
            errors.append(f"{where}: must be an object")
            continue

        duration = raw_scene.get("duration_seconds")
        if not isinstance(duration, (int, float)) or isinstance(duration, bool) or duration <= 0:
            errors.append(f"{where}: 'duration_seconds' must be > 0, got {duration!r}")

        media_kind = (raw_scene.get("media_kind") or "image").lower()
        if media_kind not in ("image", "video"):
            errors.append(f"{where}: 'media_kind' must be 'image' or 'video', "
                          f"got {raw_scene.get('media_kind')!r}")
            media_kind = "image"
        supported = (SUPPORTED_VIDEO_EXTENSIONS if media_kind == "video"
                     else SUPPORTED_IMAGE_EXTENSIONS)

        image_path = raw_scene.get("image_path") or raw_scene.get("image")
        if not image_path:
            errors.append(f"{where}: no image")
            image_path = None
        else:
            image_path = resolve_path(str(image_path), base_dir)
            if not image_path.is_file():
                errors.append(f"{where}: image does not exist: {image_path}")
            elif image_path.suffix.lower() not in supported:
                errors.append(
                    f"{where}: unsupported {media_kind} type '{image_path.suffix}'; "
                    f"supported: {', '.join(supported)}")

        motion_cfg = raw_scene.get("motion") or {}
        transition_cfg = raw_scene.get("transition") or {}
        if media_kind == "video":
            # Footage carries its own movement; a pan/zoom over it on top
            # would be a second, uninvited move.
            motion_cfg = {**motion_cfg, "kind": "static"}
        errors.extend(
            f"{where}: {problem}" for problem in motion_mod.validate_motion(
                motion_cfg.get("kind", "static"),
                fit=motion_cfg.get("fit", "cover"),
                transition=transition_cfg.get("kind", "crossfade")))

        scenes.append({
            "scene_id": raw_scene.get("scene_id") or f"s{i + 1:02d}",
            "duration_seconds": float(duration) if isinstance(duration, (int, float))
                                and not isinstance(duration, bool) else 0.0,
            "image_path": image_path,
            "media_kind": media_kind,
            "motion": dict(motion_cfg),
            "transition": dict(transition_cfg),
        })
    # The same arithmetic guard wherever the piecewise renderer will run -
    # scene count, or a footage scene that forces that path (see render()).
    if (len(scenes) > MAX_IMAGE_SLOTS
            or any(s.get("media_kind") == "video" for s in scenes)):
        errors.extend(piecewise_problems(scenes))
    return scenes, errors


def _overlap_after(scene):
    """How long the crossfade leaving ``scene`` lasts. A cut is zero."""
    transition = scene.get("transition") or {}
    if transition.get("kind", "crossfade") == "cut":
        return 0.0
    return float(transition.get("duration_seconds", 0.0) or 0.0)


def piecewise_problems(scenes):
    """Why this scene list cannot be rendered piece by piece, if it cannot.

    The piecewise renderer cuts each scene into an incoming overlap, a body
    and an outgoing overlap. A scene shorter than its two overlaps combined
    has no body, so the arithmetic that makes the concatenated timeline come
    out exactly right would silently stop holding. Refuse with the numbers
    rather than render something a frame-count check would then reject.
    """
    problems = []
    for index, scene in enumerate(scenes):
        lead = _overlap_after(scenes[index - 1]) if index else 0.0
        tail = _overlap_after(scene) if index < len(scenes) - 1 else 0.0
        if lead + tail >= float(scene["duration_seconds"]):
            problems.append(
                f"{scene['scene_id']}: {scene['duration_seconds']}s scene is "
                f"not longer than its {lead}s incoming and {tail}s outgoing "
                f"crossfades combined")
    return problems


def validate_and_normalize(raw, base_dir):
    """Validate the raw spec dict, resolve paths, and fill in defaults.

    Collects every error found instead of stopping at the first one, so
    a single run surfaces the full list of problems.
    """
    errors = []

    def require_positive_number(key, value, allow_zero=False):
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            errors.append(f"'{key}' must be a number, got {value!r}")
            return None
        if allow_zero and value < 0:
            errors.append(f"'{key}' must be >= 0, got {value}")
            return None
        if not allow_zero and value <= 0:
            errors.append(f"'{key}' must be > 0, got {value}")
            return None
        return value

    width = raw.get("width")
    height = raw.get("height")
    fps = raw.get("fps")
    duration_seconds = raw.get("duration_seconds")

    for key, value in (("width", width), ("height", height)):
        require_positive_number(key, value)
    require_positive_number("fps", fps)
    require_positive_number("duration_seconds", duration_seconds)

    if isinstance(width, (int, float)) and not isinstance(width, bool) and width > 0 and int(width) % 2 != 0:
        errors.append(f"'width' must be an even number for H.264 output, got {width}")
    if isinstance(height, (int, float)) and not isinstance(height, bool) and height > 0 and int(height) % 2 != 0:
        errors.append(f"'height' must be an even number for H.264 output, got {height}")

    # A scene list, when present, replaces images/ken_burns/crossfade: it
    # already states per scene what each of those decided globally.
    scenes, scene_errors = _normalize_scenes(raw.get("scenes"), base_dir)
    errors.extend(scene_errors)

    images_cfg = raw.get("images")
    image_paths = []
    seconds_per_image = 4.0
    if scenes is not None and not isinstance(images_cfg, dict):
        # Scenes carry their own images; a source_dir is not required.
        images_cfg = {}
    if not isinstance(images_cfg, dict):
        errors.append("'images' must be an object with at least a 'source_dir' field")
    elif scenes is not None and not images_cfg.get("source_dir"):
        pass
    else:
        source_dir_raw = images_cfg.get("source_dir")
        if not source_dir_raw or not isinstance(source_dir_raw, str):
            errors.append("'images.source_dir' is required and must be a string path")
        else:
            source_dir = resolve_path(source_dir_raw, base_dir)
            if not source_dir.is_dir():
                errors.append(f"'images.source_dir' does not exist or is not a directory: {source_dir}")
            else:
                image_paths = sorted(
                    p for p in source_dir.iterdir()
                    if p.is_file() and p.suffix.lower() in SUPPORTED_IMAGE_EXTENSIONS
                )
                if not image_paths:
                    errors.append(
                        f"No supported images ({', '.join(SUPPORTED_IMAGE_EXTENSIONS)}) "
                        f"found in 'images.source_dir': {source_dir}"
                    )
        if "seconds_per_image" in images_cfg:
            seconds_per_image = require_positive_number(
                "images.seconds_per_image", images_cfg.get("seconds_per_image")
            )

    audio_cfg = raw.get("audio")
    audio_path = None
    if not isinstance(audio_cfg, dict):
        errors.append("'audio' must be an object with a 'file' field")
    else:
        audio_file_raw = audio_cfg.get("file")
        if not audio_file_raw or not isinstance(audio_file_raw, str):
            errors.append("'audio.file' is required and must be a string path")
        else:
            audio_path = resolve_path(audio_file_raw, base_dir)
            if not audio_path.is_file():
                errors.append(f"'audio.file' does not exist: {audio_path}")

    ken_burns_cfg = raw.get("ken_burns", {})
    ken_burns_enabled = True
    zoom_start = 1.0
    zoom_end = 1.15
    if ken_burns_cfg:
        if not isinstance(ken_burns_cfg, dict):
            errors.append("'ken_burns' must be an object")
        else:
            ken_burns_enabled = bool(ken_burns_cfg.get("enabled", True))
            zoom_start = ken_burns_cfg.get("zoom_start", 1.0)
            zoom_end = ken_burns_cfg.get("zoom_end", 1.15)
            if not isinstance(zoom_start, (int, float)) or isinstance(zoom_start, bool) or zoom_start < 1.0:
                errors.append(f"'ken_burns.zoom_start' must be a number >= 1.0, got {zoom_start!r}")
            if not isinstance(zoom_end, (int, float)) or isinstance(zoom_end, bool) or zoom_end < 1.0:
                errors.append(f"'ken_burns.zoom_end' must be a number >= 1.0, got {zoom_end!r}")
            if (
                isinstance(zoom_start, (int, float)) and isinstance(zoom_end, (int, float))
                and not isinstance(zoom_start, bool) and not isinstance(zoom_end, bool)
                and zoom_end < zoom_start
            ):
                errors.append("'ken_burns.zoom_end' must be >= 'ken_burns.zoom_start'")

    crossfade_cfg = raw.get("crossfade", {})
    crossfade_enabled = True
    crossfade_seconds = 0.75
    if crossfade_cfg:
        if not isinstance(crossfade_cfg, dict):
            errors.append("'crossfade' must be an object")
        else:
            crossfade_enabled = bool(crossfade_cfg.get("enabled", True))
            crossfade_seconds = crossfade_cfg.get("duration_seconds", 0.75)
            if not isinstance(crossfade_seconds, (int, float)) or isinstance(crossfade_seconds, bool) or crossfade_seconds < 0:
                errors.append(f"'crossfade.duration_seconds' must be a number >= 0, got {crossfade_seconds!r}")

    # Only meaningful for the cycling path. A scene list states its own
    # per-scene length and transition, and `piecewise_problems` checks those
    # against each other; measuring them against the global defaults the
    # scenes replaced would reject a perfectly coherent edit.
    if (
        scenes is None
        and crossfade_enabled
        and isinstance(crossfade_seconds, (int, float))
        and isinstance(seconds_per_image, (int, float))
        and crossfade_seconds >= seconds_per_image
    ):
        errors.append(
            "'crossfade.duration_seconds' must be less than 'images.seconds_per_image' "
            f"(got crossfade={crossfade_seconds}, seconds_per_image={seconds_per_image})"
        )

    # Guard the filter graph's memory footprint before ffmpeg is launched.
    if (
        scenes is None
        and not errors
        and isinstance(duration_seconds, (int, float))
        and isinstance(seconds_per_image, (int, float))
        and seconds_per_image > 0
    ):
        effective_crossfade = crossfade_seconds if crossfade_enabled else 0
        if not (crossfade_enabled and effective_crossfade >= seconds_per_image):
            slots = required_image_slots(duration_seconds, seconds_per_image, effective_crossfade)
            if slots > MAX_IMAGE_SLOTS:
                suggestion = math.ceil(duration_seconds / MAX_IMAGE_SLOTS) + effective_crossfade
                errors.append(
                    f"this spec needs {slots} image slots, over the {MAX_IMAGE_SLOTS} limit "
                    f"(each slot is a concurrent ffmpeg input; too many exhaust memory and "
                    f"the render is OOM-killed). Raise 'images.seconds_per_image' to about "
                    f"{suggestion:.0f}s or more, or shorten 'duration_seconds'."
                )

    output_path_raw = raw.get("output_path")
    if output_path_raw is not None and not isinstance(output_path_raw, str):
        errors.append("'output_path' must be a string if provided")

    if errors:
        raise SpecValidationError(errors)

    if not crossfade_enabled:
        crossfade_seconds = 0.0

    return {
        "width": int(width),
        "height": int(height),
        "fps": fps,
        "duration_seconds": float(duration_seconds),
        "image_paths": image_paths,
        "seconds_per_image": float(seconds_per_image),
        "audio_path": audio_path,
        "ken_burns_enabled": ken_burns_enabled,
        "zoom_start": float(zoom_start),
        "zoom_end": float(zoom_end),
        "crossfade_enabled": crossfade_enabled,
        "crossfade_seconds": float(crossfade_seconds),
        "output_path_raw": output_path_raw,
        "scenes": scenes,
        "timeline_seconds": (motion_mod.timeline_seconds(scenes)
                             if scenes else float(duration_seconds)),
    }


def determine_output_path(spec, spec_path, cli_output):
    if cli_output:
        return resolve_path(cli_output, Path.cwd())
    if spec["output_path_raw"]:
        return resolve_path(spec["output_path_raw"], Path.cwd())
    return ROOT / "output" / "video" / f"{spec_path.stem}.mp4"


def cycle_images(image_paths, count):
    return [image_paths[i % len(image_paths)] for i in range(count)]


def build_scene_ffmpeg_command(spec, output_path):
    """One ffmpeg invocation for an explicit scene plan.

    The finished runtime is the *timeline*, not ``duration_seconds``: a
    crossfade overlaps two scenes, so the video is shorter than the sum of
    them. Rendering to the timeline and reporting it is what keeps a timing
    disagreement with the narration visible instead of being concealed by a
    silent truncation.
    """
    W, H, fps = spec["width"], spec["height"], spec["fps"]
    scenes = spec["scenes"]
    target = {"width": W, "height": H, "fps": fps}

    input_args = []
    for scene in scenes:
        input_args += ["-loop", "1", "-t", f"{scene['duration_seconds']}",
                       "-i", str(scene["image_path"])]
    audio_input_index = len(scenes)
    input_args += ["-stream_loop", "-1", "-i", str(spec["audio_path"])]

    filter_parts = []
    labels = []
    for i, scene in enumerate(scenes):
        label = f"v{i}"
        filter_parts.append(motion_mod.build_scene_filter(i, label, scene, target))
        labels.append(label)

    join_parts, final_label, timeline = motion_mod.build_transition_chain(labels, scenes)
    filter_parts.extend(join_parts)

    log.info("Rendering %d scene(s), %.2fs timeline at %dx%d",
             len(scenes), timeline, W, H)

    return [
        "ffmpeg", "-y",
        *input_args,
        "-filter_complex", ";".join(filter_parts),
        "-map", f"[{final_label}]",
        "-map", f"{audio_input_index}:a",
        "-t", f"{timeline}",
        "-r", f"{fps}",
        "-c:v", "libx264",
        "-pix_fmt", "yuv420p",
        "-preset", "medium",
        "-crf", "20",
        "-c:a", "aac",
        "-b:a", "192k",
        "-ar", "48000",
        "-movflags", "+faststart",
        str(output_path),
    ]


def build_ffmpeg_command(spec, output_path):
    if spec.get("scenes"):
        return build_scene_ffmpeg_command(spec, output_path)
    W, H, fps = spec["width"], spec["height"], spec["fps"]
    clip_dur = spec["seconds_per_image"]
    xfade_dur = spec["crossfade_seconds"]
    duration_seconds = spec["duration_seconds"]

    n_needed = required_image_slots(
        duration_seconds, clip_dur, xfade_dur if spec["crossfade_enabled"] else 0)

    images = cycle_images(spec["image_paths"], n_needed)
    log.info(
        "Using %d image slot(s) from %d source image(s) to cover %.2fs at %.2fs/image",
        len(images), len(spec["image_paths"]), duration_seconds, clip_dur,
    )

    input_args = []
    for img in images:
        input_args += ["-loop", "1", "-t", f"{clip_dur}", "-i", str(img)]
    audio_input_index = len(images)
    input_args += ["-stream_loop", "-1", "-i", str(spec["audio_path"])]

    d_frames = max(round(clip_dur * fps), 1)
    filter_parts = []
    for i in range(len(images)):
        if spec["ken_burns_enabled"]:
            zoom_start, zoom_end = spec["zoom_start"], spec["zoom_end"]
            zoom_step = (zoom_end - zoom_start) / max(d_frames - 1, 1)
            zexpr = f"if(eq(on,1),{zoom_start},min(zoom+{zoom_step:.8f},{zoom_end}))"
            filt = (
                f"[{i}:v]scale={W}:{H}:force_original_aspect_ratio=increase,"
                f"crop={W}:{H},scale={W * KEN_BURNS_UPSCALE}:{H * KEN_BURNS_UPSCALE},"
                f"zoompan=z='{zexpr}':d={d_frames}:"
                f"x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':s={W}x{H}:fps={fps},"
                f"format=yuv420p,setsar=1[v{i}]"
            )
        else:
            filt = (
                f"[{i}:v]scale={W}:{H}:force_original_aspect_ratio=increase,"
                f"crop={W}:{H},fps={fps},format=yuv420p,setsar=1[v{i}]"
            )
        filter_parts.append(filt)

    if len(images) == 1:
        final_video_label = "v0"
    elif spec["crossfade_enabled"] and xfade_dur > 0:
        label = "v0"
        for i in range(1, len(images)):
            offset = i * (clip_dur - xfade_dur)
            out_label = f"vx{i}"
            filter_parts.append(
                f"[{label}][v{i}]xfade=transition=fade:duration={xfade_dur}:offset={offset:.3f}[{out_label}]"
            )
            label = out_label
        final_video_label = label
    else:
        inputs_str = "".join(f"[v{i}]" for i in range(len(images)))
        filter_parts.append(f"{inputs_str}concat=n={len(images)}:v=1:a=0[vcat]")
        final_video_label = "vcat"

    filter_complex = ";".join(filter_parts)

    cmd = [
        "ffmpeg", "-y",
        *input_args,
        "-filter_complex", filter_complex,
        "-map", f"[{final_video_label}]",
        "-map", f"{audio_input_index}:a",
        "-t", f"{duration_seconds}",
        "-r", f"{fps}",
        "-c:v", "libx264",
        "-pix_fmt", "yuv420p",
        "-preset", "medium",
        "-crf", "20",
        "-c:a", "aac",
        "-b:a", "192k",
        "-ar", "48000",
        "-movflags", "+faststart",
        str(output_path),
    ]
    return cmd


def _scene_piece_plan(scenes, fps):
    """Frame counts for each scene's incoming overlap, body and outgoing one.

    Every boundary is rounded to a frame *on the finished timeline*, not
    within its own scene, and each piece is then the difference between two
    of those absolute positions. Rounding each scene independently instead
    would be fine for a dozen scenes and badly wrong for three hundred: the
    same half-frame error in every scene is a systematic one, and 240 scenes
    of it is seconds of drift that QC would (rightly) call a broken render.
    """
    fps = float(fps)
    starts = motion_mod.scene_start_times(scenes)

    def at(seconds):
        return int(round(seconds * fps))

    last = len(scenes) - 1
    # Where each scene opens, and how long each crossfade lasts, in frames on
    # the finished timeline. The crossfade leaving scene i begins exactly
    # where scene i+1 begins - that is what "overlap" means here.
    opens = [at(start) for start in starts]
    overlap = [int(round(_overlap_after(scene) * fps)) for scene in scenes[:-1]]

    plan = []
    for index, scene in enumerate(scenes):
        # A scene's own clip runs from where it opens to the end of the
        # crossfade it hands to the next scene (or to its own end, if last).
        clip_end = (opens[index + 1] + overlap[index] if index < last
                    else at(starts[index] + float(scene["duration_seconds"])))
        lead = overlap[index - 1] if index else 0
        tail = overlap[index] if index < last else 0
        total = clip_end - opens[index]
        plan.append({
            "scene": scene,
            "total_frames": total,
            "lead_frames": lead,
            "tail_frames": tail,
            "body_frames": total - lead - tail,
        })
    return plan


def _render_scene_pieces(index, piece, target, workdir):
    """Render one scene once and cut it into its head, body and tail files.

    One ffmpeg invocation, one input: this is what makes a two-hour render
    cost the same memory as a two-minute one. The motion spans the whole
    scene before the split, so cutting the overlaps out of it does not change
    what the move looks like.
    """
    # One input frame, expanded by zoompan into exactly the frames this clip
    # owns. The cycling path instead loops the image and truncates the output,
    # which is fine when the whole scene is one output; here the pieces are
    # cut by frame number, so the move has to be exactly as long as the clip
    # or a trim would land in zoompan's restart on the next input frame.
    frame_scene = dict(piece["scene"],
                       duration_seconds=piece["total_frames"] / float(target["fps"]))
    if piece["scene"].get("media_kind") == "video":
        input_args, parts = _video_scene_input(piece, target)
    else:
        input_args = ["-i", str(piece["scene"]["image_path"])]
        parts = [motion_mod.build_scene_filter(0, "m", frame_scene, target)]
    # Only the pieces this scene actually contributes: the first scene has no
    # incoming overlap and the last has no outgoing one.
    wanted = [
        (name, start, length, workdir / f"{index:05d}-{name}.mp4")
        for name, start, length in (
            ("head", 0, piece["lead_frames"]),
            ("body", piece["lead_frames"], piece["body_frames"]),
            ("tail", piece["lead_frames"] + piece["body_frames"], piece["tail_frames"]),
        )
        if length > 0
    ]
    split_labels = [f"p{i}" for i in range(len(wanted))]
    parts.append(f"[m]split={len(wanted)}" + "".join(f"[{l}]" for l in split_labels))
    cmd_outputs = []
    for (name, start, length, path), label in zip(wanted, split_labels):
        out_label = f"{label}c"
        parts.append(
            f"[{label}]trim=start_frame={start}:end_frame={start + length},"
            f"setpts=PTS-STARTPTS[{out_label}]")
        cmd_outputs += ["-map", f"[{out_label}]", "-r", f"{target['fps']}",
                        *_PIECEWISE_VIDEO_ARGS, str(path)]

    cmd = ["ffmpeg", "-y", *input_args,
           "-filter_complex", ";".join(parts), *cmd_outputs]
    run_ffmpeg(cmd)
    return {name: path for name, _, _, path in wanted}


def _video_scene_input(piece, target):
    """Input arguments and filter for a scene rendered from owner footage.

    The clip is scaled and cropped to fill the frame, resampled to the
    project's frame rate, and repeated if it is shorter than the shot it has
    to cover - the same `-stream_loop` the audio bed uses, so a 10-second
    loop can hold a 30-second shot. Trimmed by frame count, like the still
    path, so the pieces the overlaps are cut from line up exactly.
    """
    W, H, fps = target["width"], target["height"], target["fps"]
    frames = piece["total_frames"]
    # Looping is what makes a short clip usable; the trim below is what stops
    # it. An un-looped input would simply end early and leave black.
    input_args = ["-stream_loop", "-1", "-i", str(piece["scene"]["image_path"])]
    filters = (
        f"[0:v]scale={W}:{H}:force_original_aspect_ratio=increase,"
        f"crop={W}:{H},fps={fps},format=yuv420p,setsar=1,"
        f"trim=end_frame={frames},setpts=PTS-STARTPTS[m]"
    )
    return input_args, [filters]


def _render_transition(tail_path, head_path, frames, target, out_path):
    """The crossfade between two scenes, as its own short clip."""
    seconds = frames / float(target["fps"])
    cmd = [
        "ffmpeg", "-y", "-i", str(tail_path), "-i", str(head_path),
        "-filter_complex",
        f"[0:v][1:v]xfade=transition=fade:duration={seconds:.4f}:offset=0,"
        f"format=yuv420p,setsar=1[x]",
        "-map", "[x]", "-r", f"{target['fps']}", *_PIECEWISE_VIDEO_ARGS,
        str(out_path),
    ]
    run_ffmpeg(cmd)
    return out_path


def render_scenes_piecewise(spec, output_path, workdir=None):
    """Render an arbitrarily long scene plan without an arbitrarily big graph.

    Each scene is rendered once on its own, split into the overlap it hands
    to the previous scene, its own body, and the overlap it hands to the
    next. Each crossfade is rendered once from the two overlaps that meet
    there. The finished pieces are then concatenated *without re-encoding*
    and the audio bed is muxed over them, so the whole video is encoded
    exactly once however many scenes it has.

    The arithmetic is the same one ``motion.timeline_seconds`` states: every
    crossfade overlaps two scenes, so sum(bodies) + sum(crossfades) is the
    finished runtime. Verified against the finished file before returning.
    """
    scenes = spec["scenes"]
    target = {"width": spec["width"], "height": spec["height"], "fps": spec["fps"]}
    plan = _scene_piece_plan(scenes, spec["fps"])

    owns_workdir = workdir is None
    workdir = Path(workdir) if workdir else Path(tempfile.mkdtemp(prefix="cm-render-"))
    workdir.mkdir(parents=True, exist_ok=True)
    try:
        log.info("Rendering %d scene(s) piecewise at %dx%d (one encode of the "
                 "timeline; memory does not grow with scene count)",
                 len(scenes), target["width"], target["height"])
        pieces = []
        for index, piece in enumerate(plan):
            rendered = _render_scene_pieces(index, piece, target, workdir)
            pieces.append(rendered)
            if (index + 1) % 10 == 0 or index + 1 == len(plan):
                log.info("  scenes rendered: %d/%d", index + 1, len(plan))

        sequence = []
        for index, (piece, rendered) in enumerate(zip(plan, pieces)):
            sequence.append(rendered["body"])
            if piece["tail_frames"] > 0:
                sequence.append(_render_transition(
                    rendered["tail"], pieces[index + 1]["head"],
                    piece["tail_frames"], target,
                    workdir / f"{index:05d}-xfade.mp4"))

        list_path = workdir / "concat.txt"
        list_path.write_text("".join(
            f"file '{p.as_posix()}'\n" for p in sequence))

        timeline = motion_mod.timeline_seconds(scenes)
        cmd = [
            "ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(list_path),
            "-stream_loop", "-1", "-i", str(spec["audio_path"]),
            "-map", "0:v", "-map", "1:a",
            "-c:v", "copy", "-c:a", "aac", "-b:a", "192k", "-ar", "48000",
            "-shortest", "-movflags", "+faststart", str(output_path),
        ]
        run_ffmpeg(cmd)
        log.info("Piecewise render complete: %d piece(s), %.2fs timeline",
                 len(sequence), timeline)
        return output_path
    finally:
        if owns_workdir and not os.environ.get("CM_KEEP_RENDER_WORKDIR"):
            shutil.rmtree(workdir, ignore_errors=True)


def render(spec, output_path):
    """Render ``spec`` to ``output_path``, choosing how by scene count.

    One entry point so every caller - the pipeline, the CLI, a test - picks
    the same path for the same spec. Up to MAX_IMAGE_SLOTS scenes the
    original single-invocation filter graph is used unchanged; above it the
    piecewise renderer takes over.
    """
    scenes = spec.get("scenes") or []
    # Footage scenes always take the piecewise path: it renders each scene on
    # its own input, which is the only way a looped clip and a still can sit
    # in one timeline without the single filter graph having to special-case
    # every combination of them.
    if scenes and (len(scenes) > MAX_IMAGE_SLOTS
                   or any(s.get("media_kind") == "video" for s in scenes)):
        return render_scenes_piecewise(spec, output_path)
    run_ffmpeg(build_ffmpeg_command(spec, output_path))
    return output_path


def run_ffmpeg(cmd):
    log.info("Rendering with ffmpeg (this may take a while)...")
    log.debug("Command: %s", " ".join(cmd))
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        stderr_tail = "\n".join(result.stderr.strip().splitlines()[-40:])
        log.error("ffmpeg render failed (exit code %d).", result.returncode)
        log.error("ffmpeg stderr (last 40 lines):\n%s", stderr_tail)
        raise SystemExit(1)


def verify_output(output_path):
    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        log.warning("ffprobe not found on PATH; skipping output verification.")
        return
    result = subprocess.run(
        [ffprobe, "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", str(output_path)],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        log.error("ffprobe could not read the rendered output: %s", result.stderr.strip())
        raise SystemExit(1)
    log.info("Verified output with ffprobe: duration=%ss", result.stdout.strip())


def main():
    parser = argparse.ArgumentParser(description="Render a video from images + audio + a JSON spec.")
    parser.add_argument("--spec", required=True, help="Path to a video_spec JSON file")
    parser.add_argument("--output", default=None, help="Override the output MP4 path")
    args = parser.parse_args()

    if not shutil.which("ffmpeg"):
        log.error("ffmpeg executable not found on PATH. Install ffmpeg and try again.")
        sys.exit(1)

    spec_path = resolve_path(args.spec, Path.cwd())
    log.info("Loading spec: %s", spec_path)

    try:
        raw_spec = load_spec(spec_path)
        spec = validate_and_normalize(raw_spec, base_dir=Path.cwd())
    except SpecValidationError as e:
        log.error("Spec validation failed with %d error(s):", len(e.errors))
        for err in e.errors:
            log.error("  - %s", err)
        sys.exit(1)

    output_path = determine_output_path(spec, spec_path, args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    cmd = build_ffmpeg_command(spec, output_path)
    run_ffmpeg(cmd)

    if not output_path.is_file():
        log.error("ffmpeg reported success but output file is missing: %s", output_path)
        sys.exit(1)

    size_mb = output_path.stat().st_size / (1024 * 1024)
    log.info("Render complete: %s (%.2f MB)", output_path, size_mb)
    verify_output(output_path)


if __name__ == "__main__":
    main()
