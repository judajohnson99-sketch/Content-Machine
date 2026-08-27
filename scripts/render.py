#!/usr/bin/env python3
"""Deterministic local video renderer.

Takes a directory of still images, one audio file, and a JSON video
specification, and produces a YouTube-ready H.264/AAC MP4 using the
system ffmpeg binary. See config/video_spec.example.json for the spec
format and README.md for full usage.

Usage:
    python3 scripts/render.py --spec config/video_spec.example.json
    python3 scripts/render.py --spec config/video_spec.example.json --output output/video/my_video.mp4
"""
import argparse
import json
import logging
import math
import shutil
import subprocess
import sys
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="[%(levelname)s] %(message)s")
log = logging.getLogger("render")

ROOT = Path(__file__).resolve().parent.parent
SUPPORTED_IMAGE_EXTENSIONS = (".png", ".jpg", ".jpeg")

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

    images_cfg = raw.get("images")
    image_paths = []
    seconds_per_image = 4.0
    if not isinstance(images_cfg, dict):
        errors.append("'images' must be an object with at least a 'source_dir' field")
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

    if (
        crossfade_enabled
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
        not errors
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
    }


def determine_output_path(spec, spec_path, cli_output):
    if cli_output:
        return resolve_path(cli_output, Path.cwd())
    if spec["output_path_raw"]:
        return resolve_path(spec["output_path_raw"], Path.cwd())
    return ROOT / "output" / "video" / f"{spec_path.stem}.mp4"


def cycle_images(image_paths, count):
    return [image_paths[i % len(image_paths)] for i in range(count)]


def build_ffmpeg_command(spec, output_path):
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
