#!/usr/bin/env python3
"""Quality control for rendered videos.

Inspects a finished MP4 with ffprobe/ffmpeg and reports whether it
matches the spec it was rendered from and is free of obvious render
faults (silent audio, fully black video). Importable as a module or
runnable directly:

    python3 scripts/qc.py --video path/to.mp4 --spec path/to/video_spec.json
"""
import argparse
import json
import logging
import subprocess
import sys
from pathlib import Path

log = logging.getLogger("qc")

# A render that drifts more than this from the requested duration is a bug.
DURATION_TOLERANCE_SECONDS = 0.5
# Below this mean level the audio track is effectively silence.
SILENCE_THRESHOLD_DB = -60.0
# A single black stretch longer than this suggests a broken image sequence.
MAX_BLACK_RUN_SECONDS = 1.5
# MEASURED boundary for the check above: blackdetect fired on plates with mean
# luma 22.7-25.3 and passed at 27.4+. Validation refuses source images below
# this so a long render is not spent on frames QC will then reject. This is the
# failure boundary, not a style target - generators should aim well above it.
BLACK_FLOOR_MEAN_LUMA = 26.0


def _run(cmd):
    return subprocess.run(cmd, capture_output=True, text=True)


def probe(path):
    """Return ffprobe's JSON view of a media file, or None if unreadable."""
    result = _run([
        "ffprobe", "-v", "error", "-print_format", "json",
        "-show_format", "-show_streams", str(path),
    ])
    if result.returncode != 0:
        return None
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError:
        return None


def _stream(probed, codec_type):
    for stream in probed.get("streams", []):
        if stream.get("codec_type") == codec_type:
            return stream
    return None


def probe_image(path):
    """Return (width, height) for a genuinely decodable still image, else None.

    ffprobe exits 0 on a corrupt image whose extension it recognises — it
    reports the codec but zero dimensions — so a successful probe is not
    on its own proof the file is usable. Non-zero dimensions are.
    """
    probed = probe(path)
    if not probed:
        return None
    stream = _stream(probed, "video")
    if not stream:
        return None
    width, height = stream.get("width") or 0, stream.get("height") or 0
    if width <= 0 or height <= 0:
        return None
    return width, height


def image_mean_luma(path):
    """Mean luma 0-255 of a still, or None if unmeasurable."""
    result = _run([
        "ffmpeg", "-hide_banner", "-i", str(path),
        "-vf", "signalstats,metadata=print:key=lavfi.signalstats.YAVG",
        "-f", "null", "-",
    ])
    for line in result.stderr.splitlines():
        if "YAVG" in line:
            try:
                return float(line.split("=")[-1].strip())
            except ValueError:
                return None
    return None


def probe_audio_seconds(path):
    """Return duration for a file with a real, non-empty audio stream, else None."""
    probed = probe(path)
    if not probed:
        return None
    if _stream(probed, "audio") is None:
        return None
    try:
        duration = float(probed["format"]["duration"])
    except (KeyError, ValueError, TypeError):
        return None
    return duration if duration > 0 else None


def _parse_rate(rate):
    """'30/1' -> 30.0"""
    try:
        num, _, den = rate.partition("/")
        return float(num) / float(den or 1)
    except (ValueError, ZeroDivisionError):
        return None


def mean_volume_db(path):
    """Mean dBFS across the whole audio track, or None if unmeasurable."""
    result = _run([
        "ffmpeg", "-hide_banner", "-i", str(path),
        "-af", "volumedetect", "-f", "null", "-",
    ])
    for line in result.stderr.splitlines():
        if "mean_volume:" in line:
            try:
                return float(line.split("mean_volume:")[1].strip().split()[0])
            except (IndexError, ValueError):
                return None
    return None


def longest_black_run_seconds(path):
    """Longest fully-black stretch in seconds (0.0 if none detected)."""
    result = _run([
        "ffmpeg", "-hide_banner", "-i", str(path),
        "-vf", "blackdetect=d=0.5:pic_th=0.98", "-f", "null", "-",
    ])
    longest = 0.0
    for line in result.stderr.splitlines():
        if "black_duration" in line:
            for token in line.split():
                if token.startswith("black_duration:"):
                    try:
                        longest = max(longest, float(token.split(":", 1)[1]))
                    except ValueError:
                        pass
    return longest


def check(name, passed, detail):
    return {"check": name, "passed": bool(passed), "detail": detail}


def qc_video(video_path, expected=None, source_images=None):
    """Run every QC check and return a machine-readable report.

    expected: dict with width/height/fps/duration_seconds to compare against.
    source_images: iterable of image paths to verify are still decodable.
    """
    video_path = Path(video_path)
    checks = []

    if not video_path.is_file():
        checks.append(check("file_exists", False, f"missing: {video_path}"))
        return _finalize(video_path, checks)
    checks.append(check("file_exists", True, str(video_path)))

    size = video_path.stat().st_size
    checks.append(check("file_non_trivial", size > 1024, f"{size} bytes"))

    probed = probe(video_path)
    if probed is None:
        checks.append(check("file_opens", False, "ffprobe could not read the file"))
        return _finalize(video_path, checks)
    checks.append(check("file_opens", True, "ffprobe read the container"))

    video = _stream(probed, "video")
    audio = _stream(probed, "audio")

    checks.append(check("video_stream_present", video is not None, "video stream"))
    checks.append(check("audio_stream_present", audio is not None, "audio stream"))

    if video:
        checks.append(check(
            "video_codec_h264", video.get("codec_name") == "h264",
            f"codec_name={video.get('codec_name')}",
        ))
        checks.append(check(
            "pixel_format_yuv420p", video.get("pix_fmt") == "yuv420p",
            f"pix_fmt={video.get('pix_fmt')}",
        ))
    if audio:
        checks.append(check(
            "audio_codec_aac", audio.get("codec_name") == "aac",
            f"codec_name={audio.get('codec_name')}",
        ))

    duration = None
    try:
        duration = float(probed["format"]["duration"])
    except (KeyError, ValueError, TypeError):
        checks.append(check("duration_readable", False, "no duration in container"))
    else:
        checks.append(check("duration_readable", True, f"{duration:.3f}s"))

    if expected:
        if video and expected.get("width") and expected.get("height"):
            ok = (video.get("width") == expected["width"]
                  and video.get("height") == expected["height"])
            checks.append(check(
                "resolution_matches_spec", ok,
                f"got {video.get('width')}x{video.get('height')}, "
                f"expected {expected['width']}x{expected['height']}",
            ))
        if video and expected.get("fps"):
            actual_fps = _parse_rate(video.get("r_frame_rate", ""))
            ok = actual_fps is not None and abs(actual_fps - float(expected["fps"])) < 0.01
            checks.append(check(
                "fps_matches_spec", ok,
                f"got {actual_fps}, expected {expected['fps']}",
            ))
        if duration is not None and expected.get("duration_seconds"):
            drift = abs(duration - float(expected["duration_seconds"]))
            checks.append(check(
                "duration_matches_spec", drift <= DURATION_TOLERANCE_SECONDS,
                f"got {duration:.3f}s, expected {expected['duration_seconds']}s "
                f"(drift {drift:.3f}s, tolerance {DURATION_TOLERANCE_SECONDS}s)",
            ))

    if audio:
        level = mean_volume_db(video_path)
        if level is None:
            checks.append(check("audio_not_silent", False, "could not measure audio level"))
        else:
            checks.append(check(
                "audio_not_silent", level > SILENCE_THRESHOLD_DB,
                f"mean_volume={level} dB (threshold {SILENCE_THRESHOLD_DB} dB)",
            ))

    black = longest_black_run_seconds(video_path)
    checks.append(check(
        "no_long_black_segment", black <= MAX_BLACK_RUN_SECONDS,
        f"longest black run {black:.2f}s (max {MAX_BLACK_RUN_SECONDS}s)",
    ))

    if source_images:
        bad = [str(p) for p in source_images if probe_image(p) is None]
        checks.append(check(
            "source_images_decodable", not bad,
            "all source images readable" if not bad else f"unreadable: {bad}",
        ))

    return _finalize(video_path, checks)


def _finalize(video_path, checks):
    failed = [c for c in checks if not c["passed"]]
    return {
        "video": str(video_path),
        "status": "PASS" if not failed else "FAIL",
        "checks_run": len(checks),
        "checks_failed": len(failed),
        "failures": [c["check"] for c in failed],
        "checks": checks,
    }


def log_report(report):
    for c in report["checks"]:
        level = logging.INFO if c["passed"] else logging.ERROR
        log.log(level, "  [%s] %-26s %s",
                "PASS" if c["passed"] else "FAIL", c["check"], c["detail"])
    log.info("QC status: %s (%d/%d checks passed)",
             report["status"], report["checks_run"] - report["checks_failed"],
             report["checks_run"])


def main():
    logging.basicConfig(level=logging.INFO, format="[%(levelname)s] %(message)s")
    parser = argparse.ArgumentParser(description="QC a rendered MP4.")
    parser.add_argument("--video", required=True)
    parser.add_argument("--spec", default=None, help="video_spec.json to compare against")
    parser.add_argument("--json", action="store_true", help="print the report as JSON")
    args = parser.parse_args()

    expected = None
    if args.spec:
        with open(args.spec) as f:
            raw = json.load(f)
        expected = {k: raw.get(k) for k in ("width", "height", "fps", "duration_seconds")}

    report = qc_video(args.video, expected=expected)
    if args.json:
        print(json.dumps(report, indent=2))
    else:
        log_report(report)
    sys.exit(0 if report["status"] == "PASS" else 1)


if __name__ == "__main__":
    main()
