#!/usr/bin/env python3
"""Deterministic procedural still generation with ffmpeg.

These are genuinely generated abstract plates - gradients, vignetting,
fine dither. They are NOT photographs and must never be described as
such. For concepts whose appeal depends on a depicted scene (rain on a
window, a firelit cabin), these are placeholders and the project should
record production_grade=false until real imagery exists.

    python3 scripts/make_visuals.py --style deep-night --out DIR --count 1

Styles are tuned to stay above the QC blackdetect luminance floor
(perceived luma >= ~26) while still reading as dark.
"""
import argparse
import binascii
import logging
import shutil
import struct
import subprocess
import sys
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="[%(levelname)s] %(message)s")
log = logging.getLogger("make_visuals")

WIDTH, HEIGHT = 1920, 1080

# style -> (inner colour, outer colour, description)
# Inner colours sit above the blackdetect floor so dark plates still pass QC.
STYLES = {
    "deep-night": ("0x2e3757", "0x161a2e",
                   "Deep blue-black radial wash for sleep ambience"),
    "storm-slate": ("0x323f52", "0x171d28",
                    "Cold slate gradient suggesting an overcast night"),
    "warm-ember": ("0x56433a", "0x2a2019",
                   "Muted warm gradient for calm narrated content"),
    "muted-forest": ("0x374b41", "0x1d2723",
                     "Desaturated green-black wash"),
    "dust-archive": ("0x453f36", "0x201d18",
                     "Aged paper tone for calm factual narration"),
}

# QC's blackdetect trips when almost the whole frame sits below its threshold.
# Plates are generated to clear this with margin rather than sitting on it.
MIN_MEAN_LUMA = 34.0


def build_still(style, index, out_path, seed):
    """One plate: radial gradient, vignette, and a little noise to stop banding."""
    inner, outer = STYLES[style][0], STYLES[style][1]
    # Vary a sequence by moving the gradient's centre, NOT by rotating the
    # frame: rotating a 16:9 plate leaves black corners (and near 90 degrees,
    # black most of the frame), which trips QC's blackdetect.
    offsets = ((0.50, 0.50), (0.38, 0.44), (0.62, 0.56),
               (0.44, 0.62), (0.58, 0.40), (0.50, 0.58))
    fx, fy = offsets[index % len(offsets)]
    source = (f"gradients=s={WIDTH}x{HEIGHT}:c0={inner}:c1={outer}"
              f":type=radial:x0={int(WIDTH * fx)}:y0={int(HEIGHT * fy)}"
              f":nb_colors=2:seed={seed + index}:d=1")
    # Slight blur smooths gradient steps; noise adds dither so large flat
    # areas do not band under h264. A gentle vignette shapes the edges
    # without dragging the frame under the black threshold.
    chain = (f"gblur=sigma=18,"
             f"vignette=PI/9,"
             f"noise=alls=6:allf=t+u,"
             f"format=yuv420p")
    result = subprocess.run([
        "ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", source,
        "-vf", chain, "-frames:v", "1", str(out_path),
    ], capture_output=True, text=True)
    if result.returncode != 0:
        log.error("ffmpeg failed generating %s:\n%s", out_path.name,
                  result.stderr.strip()[-800:])
        raise SystemExit(1)


def measure_luma(path):
    """Mean luma 0-255, so callers can confirm the QC floor is cleared."""
    result = subprocess.run([
        "ffmpeg", "-hide_banner", "-i", str(path),
        "-vf", "signalstats,metadata=print:key=lavfi.signalstats.YAVG",
        "-f", "null", "-",
    ], capture_output=True, text=True)
    for line in result.stderr.splitlines():
        if "YAVG" in line:
            try:
                return float(line.split("=")[-1].strip())
            except ValueError:
                return None
    return None


# --- Provenance stamping ---------------------------------------------------
# A plate must be identifiable as procedural from the *file itself*, not from
# a metadata field a later edit can flip. The publication gate reads this
# stamp, so a placeholder cannot be relabelled into a production asset.

PROVENANCE_KEYWORD = "Software"
PROVENANCE_VALUE = "content-machine/make_visuals"
# Downsample used for colour-diversity sampling. Small enough to be cheap,
# large enough that a real photograph shows thousands of distinct colours.
_SAMPLE_W, _SAMPLE_H = 160, 90
# A radial gradient with dither samples at ~4% unique colours; a flat fill at
# ~0.01%. Real photographs sit far above both. Only the unambiguous case is
# claimed here: below this, the frame carries no depicted detail at all.
FLAT_PLATE_UNIQUE_RATIO = 0.01


def _png_chunk(chunk_type, data):
    """One PNG chunk: length, type, data, CRC32 over type+data."""
    body = chunk_type + data
    return (struct.pack(">I", len(data)) + body
            + struct.pack(">I", binascii.crc32(body) & 0xFFFFFFFF))


def stamp_provenance(path, style, seed, index):
    """Embed tEXt provenance chunks into a finished PNG, before IEND.

    Written with the stdlib rather than PIL: the renderer pipeline
    deliberately carries no Python image dependency.
    """
    raw = path.read_bytes()
    idx = raw.rfind(b"\x00\x00\x00\x00IEND")
    if idx == -1:
        raise ValueError(f"{path} is not a well-formed PNG (no IEND)")
    fields = [
        (PROVENANCE_KEYWORD, PROVENANCE_VALUE),
        ("Comment", "Procedural abstract plate. NOT a photograph and not "
                    "depicted imagery. Generated by scripts/make_visuals.py."),
        ("Source", f"style={style};seed={seed};index={index}"),
    ]
    chunks = b"".join(
        _png_chunk(b"tEXt", k.encode("latin-1") + b"\x00" + v.encode("latin-1"))
        for k, v in fields
    )
    path.write_bytes(raw[:idx] + chunks + raw[idx:])


def read_provenance(path):
    """Return the stamped provenance dict for a PNG, or None if unstamped."""
    try:
        raw = Path(path).read_bytes()
    except OSError:
        return None
    if not raw.startswith(b"\x89PNG\r\n\x1a\n"):
        return None
    found, i = {}, 8
    while i + 8 <= len(raw):
        try:
            length = struct.unpack(">I", raw[i:i + 4])[0]
        except struct.error:
            break
        ctype = raw[i + 4:i + 8]
        if ctype == b"IEND":
            break
        if ctype == b"tEXt":
            payload = raw[i + 8:i + 8 + length]
            if b"\x00" in payload:
                k, v = payload.split(b"\x00", 1)
                found[k.decode("latin-1", "replace")] = v.decode("latin-1", "replace")
        i += 12 + length
    if found.get(PROVENANCE_KEYWORD) == PROVENANCE_VALUE:
        return found
    return None


def unique_colour_ratio(path):
    """Fraction of distinct colours in a downsample, or None if unreadable."""
    result = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", str(path),
         "-vf", f"scale={_SAMPLE_W}:{_SAMPLE_H}", "-frames:v", "1",
         "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
        capture_output=True)
    buf = result.stdout
    if result.returncode != 0 or len(buf) < 3:
        return None
    pixels = [buf[i:i + 3] for i in range(0, len(buf) - 2, 3)]
    return len(set(pixels)) / len(pixels)


def classify(path):
    """Classify an image as production evidence.

    Returns ``(kind, detail)`` where kind is one of:

    ``"procedural"``  stamped by make_visuals - certainly not depicted.
    ``"flat"``        no stamp, but carries no depicted detail at all.
    ``"unknown"``     no artifact-level evidence either way.

    Deliberately conservative: only the two unambiguous cases are claimed,
    so this never overrules a genuine asset it merely cannot recognise.
    """
    stamp = read_provenance(path)
    if stamp is not None:
        return "procedural", stamp.get("Source", "procedural plate")
    ratio = unique_colour_ratio(path)
    if ratio is not None and ratio < FLAT_PLATE_UNIQUE_RATIO:
        return "flat", f"{ratio * 100:.2f}% unique colours in a {_SAMPLE_W}x{_SAMPLE_H} sample"
    return "unknown", "no provenance stamp; colour diversity inconclusive"


def main():
    parser = argparse.ArgumentParser(description="Generate procedural still plates.")
    parser.add_argument("--style", required=True, choices=sorted(STYLES))
    parser.add_argument("--out", required=True)
    parser.add_argument("--count", type=int, default=1)
    parser.add_argument("--seed", type=int, default=20260827)
    parser.add_argument("--prefix", default=None)
    args = parser.parse_args()

    if not shutil.which("ffmpeg"):
        log.error("ffmpeg not found on PATH.")
        return 1

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    prefix = args.prefix or args.style

    too_dark = []
    for i in range(args.count):
        path = out_dir / f"{prefix}_{i + 1:02d}.png"
        build_still(args.style, i, path, args.seed)
        stamp_provenance(path, args.style, args.seed, i)
        luma = measure_luma(path)
        if luma is None:
            log.error("%s: could not measure luminance", path.name)
            return 1
        if luma < MIN_MEAN_LUMA:
            too_dark.append((path.name, luma))
        log.info("%s  mean luma %.1f%s", path.name, luma,
                 "  <-- BELOW QC BLACK FLOOR" if luma < MIN_MEAN_LUMA else "")

    # Fail loudly. A warning here is worthless: it gets swallowed by any
    # caller that captures output, and the problem only resurfaces as a QC
    # failure after a long render has already been paid for.
    if too_dark:
        log.error("%d plate(s) below the QC luminance floor (%.0f); refusing to emit them:",
                  len(too_dark), MIN_MEAN_LUMA)
        for name, luma in too_dark:
            log.error("  - %s (mean luma %.1f)", name, luma)
        log.error("Brighten the style's colours in STYLES, then regenerate.")
        return 1

    log.info("%d plate(s) in %s - style '%s': %s",
             args.count, out_dir, args.style, STYLES[args.style][2])
    log.info("These are procedural abstracts, not photographs.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
