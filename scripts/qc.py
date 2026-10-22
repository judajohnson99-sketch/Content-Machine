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
import re
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


def _storyboard_report(pdir, checks):
    """Same report shape as qc_video, keyed on the artefact it describes."""
    report = _finalize(Path(pdir) / "storyboard.json", checks)
    report["subject"] = report.pop("video")
    return report


def visual_diversity_warnings(scenes, motifs_recorded):
    """Flag *unplanned* repetition, not a deliberate low environment count.

    ``motifs_recorded`` is True when a scene-environment/motif step actually
    reasoned about this storyboard's visuals (``generate_scene_motifs`` or
    ``generate_scene_environments`` - see scripts/creative.py). When it has,
    a low or even single-environment result is that reasoning's deliberate
    call - a long ambient/slow-TV video legitimately may need only one
    excellent, concept-connected setting - so it is trusted, not
    second-guessed here. This only fires for the case no such reasoning ever
    ran: every scene reusing one fixed prompt is a planning gap, not a
    creative choice, and reads as repetitive visuals rather than a designed
    video. A render is not technically invalid for it, so this never fails
    the technical QC status here - see ``gate_blockers`` in scripts/project.py
    for where an unplanned case like this actually blocks review.
    """
    if motifs_recorded or len(scenes) < 4:
        return []
    # storyboard.py::apply_scene_motifs always appends ", scene N, <section>"
    # to every prompt, so even a raw, unplanned single-prompt reuse never
    # produces byte-identical strings - strip that bookkeeping suffix before
    # comparing, or this never fires for the exact case it exists to catch.
    prompts = [_strip_scene_suffix((s.get("image_prompt") or "").strip().lower())
              for s in scenes]
    distinct = len({p for p in prompts if p})
    if distinct <= 1:
        return [f"all {len(scenes)} scenes share one identical image prompt, and "
               "no scene-environment planning step ran for this storyboard - "
               "likely unplanned repetition rather than a designed video"]
    return []


_SCENE_SUFFIX_RE = re.compile(r",\s*scene\s+\d+\s*,\s*\S+\s*$")


def _strip_scene_suffix(prompt):
    """Remove the trailing per-scene bookkeeping tag so two prompts that
    depict the identical setting compare equal regardless of which scene
    index or section they were rendered for."""
    return _SCENE_SUFFIX_RE.sub("", prompt)


# --------------------------------------------------------------------------
# image assessment - is this picture obviously not worth keeping?
# --------------------------------------------------------------------------
#
# The narrow, honest question: not "is this image good" (no measurement
# here answers that, and the production-grade claim stays a human's), but
# "is this image so obviously broken that an unattended run should throw it
# away and ask again". A blank plate, a black frame, a blown-out white
# rectangle and two supposedly different scenes that came back as the same
# picture are all answerable from the pixels.

# Luma standard deviation, on 0-255, under which an image has essentially
# no structure in it - a flat fill or a near-flat gradient.
MIN_LUMA_STDDEV = 6.0
# Mean luma outside this range is a frame that is effectively black or
# effectively white, whatever it was supposed to depict.
MIN_MEAN_LUMA = 8.0
MAX_MEAN_LUMA = 247.0
# Peak-to-trough luma under this is a picture with no tonal range at all.
MIN_LUMA_RANGE = 24.0
# signalstats SATAVG is 0-255ish; past this the image is a poster, not a
# photograph, which is the classic over-cooked-diffusion look.
MAX_MEAN_SATURATION = 130.0
# Hamming distance between two 64-bit difference hashes, at or under which
# two images are the same picture for a viewer's purposes.
DUPLICATE_HASH_DISTANCE = 6

# The hash is computed from a 9x8 greyscale reduction: each row yields 8
# bits, one per "is this pixel brighter than the next". Small, stable under
# rescaling and compression, and entirely standard library once ffmpeg has
# done the resampling.
_HASH_WIDTH = 9
_HASH_HEIGHT = 8
_STATS_WIDTH = 32
_STATS_HEIGHT = 32


def _gray_bytes(path, width, height):
    """A greyscale downsample of an image as raw bytes, or None."""
    result = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", str(path),
         "-vf", f"scale={width}:{height},format=gray", "-frames:v", "1",
         "-f", "rawvideo", "-"],
        capture_output=True)
    data = result.stdout
    if result.returncode != 0 or len(data) < width * height:
        return None
    return data[:width * height]


def image_hash(path):
    """A 64-bit difference hash of an image, or None if it cannot be read."""
    data = _gray_bytes(path, _HASH_WIDTH, _HASH_HEIGHT)
    if data is None:
        return None
    bits = 0
    for row in range(_HASH_HEIGHT):
        offset = row * _HASH_WIDTH
        for col in range(_HASH_WIDTH - 1):
            bits = (bits << 1) | int(data[offset + col] > data[offset + col + 1])
    return f"{bits:016x}"


def hash_distance(a, b):
    """Hamming distance between two hashes from ``image_hash``."""
    if not a or not b:
        return None
    return bin(int(a, 16) ^ int(b, 16)).count("1")


def _signalstats(path):
    result = _run(["ffmpeg", "-hide_banner", "-nostats", "-i", str(path),
                   "-vf", "signalstats,metadata=print", "-f", "null", "-"])
    stats = {}
    for line in result.stderr.splitlines():
        if "lavfi.signalstats." not in line:
            continue
        key, _, value = line.split("lavfi.signalstats.")[1].partition("=")
        try:
            stats[key.strip()] = float(value)
        except ValueError:
            pass
    return stats


def assess_image(path):
    """Measure one generated image and say what is obviously wrong with it.

    Returns ``{"measurement": {...}, "findings": [...], "verdict": ...}``
    with the same three severities the audio assessment uses, and the same
    rule about what they mean: ``block`` is for defects with no legitimate
    reading, so an unattended run may discard and retry on one; ``warn`` is
    reported and left to a person. Passing every check means nothing
    measurable is wrong, never that the image is good.
    """
    path = Path(path)
    findings = []
    if not path.is_file():
        return {"measurement": {}, "verdict": "BLOCKED",
                "findings": [check_finding("missing", "block", f"no such file: {path}")]}

    stats = _signalstats(path)
    data = _gray_bytes(path, _STATS_WIDTH, _STATS_HEIGHT)
    measurement = {
        "mean_luma": round(stats.get("YAVG"), 2) if "YAVG" in stats else None,
        "min_luma": stats.get("YMIN"),
        "max_luma": stats.get("YMAX"),
        "mean_saturation": round(stats.get("SATAVG"), 2) if "SATAVG" in stats else None,
        "hash": image_hash(path),
    }
    if data:
        mean = sum(data) / len(data)
        variance = sum((v - mean) ** 2 for v in data) / len(data)
        measurement["luma_stddev"] = round(variance ** 0.5, 2)
    if measurement.get("mean_luma") is None and "luma_stddev" not in measurement:
        return {"measurement": measurement, "verdict": "BLOCKED",
                "findings": [check_finding(
                    "unreadable", "block",
                    f"{path.name} could not be measured at all")]}

    stddev = measurement.get("luma_stddev")
    if stddev is not None and stddev < MIN_LUMA_STDDEV:
        findings.append(check_finding(
            "blank", "block",
            f"{path.name} has almost no structure (luma sd {stddev:.1f}); "
            "it is a flat fill, not a depicted scene"))

    mean_luma = measurement.get("mean_luma")
    if mean_luma is not None and mean_luma < MIN_MEAN_LUMA:
        findings.append(check_finding(
            "black_frame", "block",
            f"{path.name} is effectively black (mean luma {mean_luma:.0f})"))
    elif mean_luma is not None and mean_luma > MAX_MEAN_LUMA:
        findings.append(check_finding(
            "blown_out", "block",
            f"{path.name} is effectively white (mean luma {mean_luma:.0f})"))

    low, high = measurement.get("min_luma"), measurement.get("max_luma")
    if low is not None and high is not None and (high - low) < MIN_LUMA_RANGE:
        findings.append(check_finding(
            "no_tonal_range", "warn",
            f"{path.name} spans only {high - low:.0f} luma levels - no "
            "highlights or shadows to read depth from"))

    saturation = measurement.get("mean_saturation")
    if saturation is not None and saturation > MAX_MEAN_SATURATION:
        findings.append(check_finding(
            "oversaturated", "warn",
            f"{path.name} averages {saturation:.0f} saturation - the "
            "over-cooked look that reads as machine-generated"))

    return {"measurement": measurement, "findings": findings,
            "verdict": _verdict(findings)}


def check_finding(code, severity, detail):
    return {"code": code, "severity": severity, "detail": detail}


def _verdict(findings):
    severities = {f["severity"] for f in findings}
    if "block" in severities:
        return "BLOCKED"
    if "warn" in severities:
        return "REVIEW"
    return "OK"


def duplicate_scene_findings(scenes, pdir):
    """Scenes that asked for different pictures and got the same one.

    Two scenes sharing a prompt share a render by design (that is the
    picture-identity rule, and it is a saving, not a defect). Two scenes
    with *different* prompts coming back as visually the same image is the
    opposite: the variation the direction asked for did not survive
    generation, and a reviewer would see the same wallpaper twice.
    """
    entries = []
    for scene in scenes:
        image = scene.get("image")
        if not image:
            continue
        path = Path(pdir) / image
        digest = (scene.get("generation") or {}).get("request_digest")
        entries.append((scene, digest, image, image_hash(path)))

    findings, seen = [], set()
    for i, (scene_a, digest_a, image_a, hash_a) in enumerate(entries):
        for scene_b, digest_b, image_b, hash_b in entries[i + 1:]:
            if digest_a == digest_b or image_a == image_b:
                continue  # the same picture on purpose
            distance = hash_distance(hash_a, hash_b)
            if distance is None or distance > DUPLICATE_HASH_DISTANCE:
                continue
            key = tuple(sorted((scene_a["scene_id"], scene_b["scene_id"])))
            if key in seen:
                continue
            seen.add(key)
            findings.append(check_finding(
                "near_duplicate_scenes", "warn",
                f"{key[0]} and {key[1]} asked for different pictures but came "
                f"back visually identical (hash distance {distance})"))
    return findings


def qc_storyboard(storyboard, pdir, audio_seconds=None):
    """Pre-render QC for a scene plan.

    Everything here is checkable before a single frame is encoded, which is
    the point: a 20-minute render that fails on a missing scene asset has
    wasted 20 minutes to learn something ffprobe could have said in a second.

    Images are inspected with ffprobe rather than trusted by extension, so a
    truncated download or a .png that is really HTML is caught here.
    """
    checks = []
    scenes = storyboard.get("scenes") or []
    checks.append(check("storyboard_has_scenes", bool(scenes),
                        f"{len(scenes)} scene(s)"))
    if not scenes:
        report = _storyboard_report(pdir, checks)
        report["visual_diversity_warnings"] = []
        return report

    missing = [s.get("scene_id") for s in scenes if not s.get("image")]
    checks.append(check(
        "scene_assets_present", not missing,
        "every scene has an image" if not missing
        else f"no image for: {', '.join(str(m) for m in missing)}"))

    # A scene with no image and no job is simply unplanned; a scene with a
    # job id and no image is work still in flight on the GPU queue. They are
    # different situations and a reviewer needs to be able to tell them apart.
    pending = [s.get("scene_id") for s in scenes
               if not s.get("image") and (s.get("generation") or {}).get("job_id")]
    checks.append(check(
        "generation_jobs_resolved", not pending,
        "no generation job outstanding" if not pending
        else f"still awaiting generation: {', '.join(str(p) for p in pending)}"))

    unreadable, wrong_size = [], []
    expected_w = (storyboard.get("source_generation") or {}).get("width")
    expected_h = (storyboard.get("source_generation") or {}).get("height")
    for scene in scenes:
        image = scene.get("image")
        if not image:
            continue
        path = Path(image)
        if not path.is_absolute():
            path = pdir / image
        dimensions = probe_image(path)
        if dimensions is None:
            unreadable.append(f"{scene.get('scene_id')} ({path.name})")
            continue
        width, height = dimensions
        if width <= 0 or height <= 0:
            unreadable.append(f"{scene.get('scene_id')} ({path.name})")
        elif (scene.get("source") or {}).get("kind") == "owner_media":
            # The owner's own picture is whatever size they shot it at. The
            # storyboard's source_generation describes what *generation* was
            # asked for, so comparing the two would reject a perfectly good
            # photograph for not having been generated. The renderer scales
            # and crops it to the frame like any other source.
            continue
        elif expected_w and expected_h and (width, height) != (expected_w, expected_h):
            wrong_size.append(
                f"{scene.get('scene_id')} is {width}x{height}, "
                f"storyboard declares {expected_w}x{expected_h}")

    checks.append(check("scene_images_readable", not unreadable,
                        "all scene images decode" if not unreadable
                        else f"unreadable: {', '.join(unreadable)}"))
    checks.append(check(
        "scene_image_dimensions", not wrong_size,
        f"all sources {expected_w}x{expected_h}" if not wrong_size
        else "; ".join(wrong_size)))

    target = storyboard.get("target") or {}
    for key in ("width", "height", "fps", "duration_seconds"):
        value = target.get(key)
        if not isinstance(value, (int, float)) or isinstance(value, bool) or value <= 0:
            checks.append(check("target_valid", False,
                                f"target.{key} is {value!r}"))
            break
    else:
        checks.append(check(
            "target_valid", True,
            f"{target['width']}x{target['height']} @ {target['fps']}fps"))

    timeline = storyboard.get("timeline_seconds")
    requested = target.get("duration_seconds")
    if isinstance(timeline, (int, float)) and isinstance(requested, (int, float)):
        drift = abs(float(timeline) - float(requested))
        checks.append(check(
            "timeline_matches_target", drift <= DURATION_TOLERANCE_SECONDS,
            f"timeline {timeline:.2f}s vs target {requested:.2f}s "
            f"(drift {drift:.2f}s)"))

    # Narration that does not fit the picture is a real editorial problem, so
    # it is reported rather than fixed by stretching or cutting the audio.
    if audio_seconds is not None and isinstance(timeline, (int, float)):
        drift = abs(float(audio_seconds) - float(timeline))
        checks.append(check(
            "audio_matches_timeline", drift <= DURATION_TOLERANCE_SECONDS,
            f"audio {audio_seconds:.2f}s vs timeline {timeline:.2f}s "
            f"(drift {drift:.2f}s)"))

    report = _storyboard_report(pdir, checks)
    motifs_recorded = bool(storyboard.get("scene_motifs"))
    report["visual_diversity_warnings"] = visual_diversity_warnings(scenes, motifs_recorded)
    return report


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
