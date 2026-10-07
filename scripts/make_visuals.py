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
import colorsys
import hashlib
import logging
import random
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


# How a plate's own variation is derived. A storyboard asks for one picture
# per shot and expects them to differ; the old generator varied only the
# gradient's centre, so fifteen different prompts came back as fifteen copies
# of the same wash - which the scene checks correctly refused as duplicates.
# What varies now is the thing that actually makes two abstracts look unlike
# each other: hue, the number and placement of light sources, and the shape
# of the cloud field laid over them.
_HUE_FAMILIES = {
    # style -> (base hue degrees, how far a plate may wander from it)
    "deep-night": (232, 70),
    "storm-slate": (210, 40),
    "warm-ember": (26, 36),
    "muted-forest": (150, 44),
    "dust-archive": (36, 30),
}
# Lightness bands, 0-1. The floor keeps every plate clear of QC's black
# detection; the ceiling keeps a sleep plate from glowing like a monitor.
_CORE_LIGHTNESS = (0.26, 0.46)
_FIELD_LIGHTNESS = (0.09, 0.18)


def _plate_rng(style, index, seed, prompt=""):
    """A deterministic stream per (prompt, style, seed, index).

    Keyed on the prompt because that is what the storyboard varies: two
    scenes that asked for the same picture must get the same plate (the
    request digest already shares one render between them), and two scenes
    that asked for different pictures must not.
    """
    key = f"{prompt}\x00{style}\x00{seed}\x00{index}".encode("utf-8")
    return random.Random(int(hashlib.sha256(key).hexdigest()[:16], 16))


def _hsl_hex(hue, saturation, lightness):
    """``0xRRGGBB`` for an HSL triple. Small enough not to want a library."""
    red, green, blue = colorsys.hls_to_rgb((hue % 360) / 360.0, lightness, saturation)
    return "0x{:02x}{:02x}{:02x}".format(
        int(red * 255), int(green * 255), int(blue * 255))


def plate_palette(style, index, seed, prompt="", lift=1.0):
    """Three colours for one plate: two lights and the field they sit in.

    Returned rather than drawn so a caller (and a test) can see what a
    prompt resolved to without rendering anything.

    ``lift`` raises every lightness together. It is the retry knob for a
    plate that came out with too little structure in it, and it works where
    raising contrast does not: this generator's modulation is multiplicative,
    so a frame with a mean luma of 36 simply has less room to vary than one
    at 60. (Contrast is the wrong knob and was actively harmful - ffmpeg's
    eq pivots around mid-grey, so pushing contrast on a dark plate clips it
    into black and *lowers* its standard deviation.)
    """
    rng = _plate_rng(style, index, seed, prompt)
    base, spread = _HUE_FAMILIES.get(style, _HUE_FAMILIES["deep-night"])
    hue = base + rng.uniform(-spread, spread)
    # The second light sits away from the first, which is what stops the
    # frame reading as one flat wash in one colour.
    partner = hue + rng.choice((-1, 1)) * rng.uniform(28, 74)
    def lightness(low_high):
        return min(rng.uniform(*low_high) * lift, 0.72)

    return {
        "core": _hsl_hex(hue, rng.uniform(0.30, 0.55), lightness(_CORE_LIGHTNESS)),
        "accent": _hsl_hex(partner, rng.uniform(0.28, 0.52), lightness(_CORE_LIGHTNESS)),
        "field": _hsl_hex(hue + rng.uniform(-18, 18), rng.uniform(0.18, 0.38),
                          lightness(_FIELD_LIGHTNESS)),
        "rng": rng,
    }


def build_still(style, index, out_path, seed, width=WIDTH, height=HEIGHT,
                prompt="", lift=1.0):
    """One plate: two coloured lights in a field, under a soft cloud texture.

    Still an abstract - no object is depicted and nothing here may be
    described as a photograph - but an abstract with structure: the blurred
    noise field laid over the gradients gives the frame real tonal variation,
    which is both what makes it watchable under slow motion and what keeps it
    clear of the "flat fill" check that rejects a plate with nothing in it.

    ``width``/``height`` default to the output resolution for the plain
    `visuals` path; a storyboard scene asks for its own (smaller) source
    size, and the plate must actually be that size or storyboard QC's
    dimension check - which exists to catch exactly that mismatch - fails.
    """
    width, height = int(width), int(height)
    palette = plate_palette(style, index, seed, prompt, lift=lift)
    rng = palette["rng"]

    # Two light centres, kept away from the exact middle and from each other
    # so the composition has a direction rather than a bullseye.
    x0, y0 = rng.uniform(0.18, 0.5), rng.uniform(0.2, 0.62)
    x1, y1 = rng.uniform(0.5, 0.86), rng.uniform(0.3, 0.84)
    noise_seed = rng.randrange(1, 1 << 24)
    # How many large shapes the frame is made of, and how fine the detail
    # inside them is. Varying the coarse grid per plate is most of why two
    # plates do not read as the same picture with the hue changed.
    coarse_w = rng.randint(9, 18)
    coarse_h = max(int(round(coarse_w * height / width)), 4)
    fine_w = coarse_w * rng.randint(4, 7)
    fine_h = max(int(round(fine_w * height / width)), 3)

    sources = [
        f"gradients=s={width}x{height}:c0={palette['core']}:c1={palette['field']}"
        f":type=radial:x0={int(width * x0)}:y0={int(height * y0)}"
        f":nb_colors=2:seed={noise_seed}:d=1",
        f"gradients=s={width}x{height}:c0={palette['accent']}:c1=0x000000"
        f":type=radial:x0={int(width * x1)}:y0={int(height * y1)}"
        f":nb_colors=2:seed={noise_seed + 1}:d=1",
        # The cloud fields. Generated *small* and scaled up rather than
        # generated large and blurred down: blurring white noise at output
        # size destroys almost all of its amplitude, which is how an earlier
        # version of this produced flat grey washes that the structure check
        # correctly rejected. Scaling a tiny noise field up keeps the full
        # contrast and turns each pixel into a large soft shape.
        f"nullsrc=s={coarse_w}x{coarse_h}:d=1",
        f"nullsrc=s={fine_w}x{fine_h}:d=1",
    ]
    filter_complex = (
        f"[1:v]gblur=sigma={max(width // 16, 18)}[accent];"
        f"[0:v][accent]blend=all_mode=screen:all_opacity={rng.uniform(0.6, 0.95):.2f}[lit];"
        f"[2:v]format=gray,noise=alls=100:allf=t+u:all_seed={noise_seed},"
        f"scale={width}:{height}:flags=bicubic,gblur=sigma={max(width / (coarse_w * 4.0), 2):.1f}[coarse];"
        f"[3:v]format=gray,noise=alls=100:allf=t+u:all_seed={noise_seed + 7},"
        f"scale={width}:{height}:flags=bicubic,gblur=sigma={max(width / (fine_w * 3.0), 1):.1f}[fine];"
        f"[coarse][fine]blend=all_mode=overlay:all_opacity={rng.uniform(0.35, 0.6):.2f}[cloud];"
        f"[lit][cloud]blend=all_mode=softlight:all_opacity={rng.uniform(0.92, 1.0):.2f}[tex];"
        f"[tex]gblur=sigma={rng.uniform(1.0, 3.0):.1f},"
        f"eq=contrast={rng.uniform(1.15, 1.35):.2f}:"
        f"brightness={rng.uniform(0.02, 0.10):.3f}:saturation={rng.uniform(1.0, 1.4):.2f},"
        f"vignette=PI/{rng.uniform(8, 12):.1f},"
        f"noise=alls=4:allf=t+u,format=yuv420p[out]"
    )
    cmd = ["ffmpeg", "-y", "-v", "error"]
    for source in sources:
        cmd += ["-f", "lavfi", "-i", source]
    cmd += ["-filter_complex", filter_complex, "-map", "[out]",
            "-frames:v", "1", str(out_path)]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        log.error("ffmpeg failed generating %s:\n%s", out_path.name,
                  result.stderr.strip()[-800:])
        raise SystemExit(1)


# Luma standard deviation a finished plate must carry. Deliberately above
# qc.MIN_LUMA_STDDEV: a plate that only just clears the check that rejects
# blank frames is still a blank frame to a viewer, and the margin is what
# stops a dark palette producing one.
MIN_PLATE_STDDEV = 7.5
# How much the palette is lifted on each retry, and how many times. Kept
# small and few: the point is to rescue a plate that came out too dark to
# carry any structure, not to turn a night scene into a day one.
_STRUCTURE_LIFTS = (1.0, 1.25, 1.5, 1.8)


def measure_luma_stddev(path, width=160, height=90):
    """Standard deviation of luma over a downsample, 0-255, or None.

    The same measurement qc.assess_image makes, computed here so the
    generator can check its own work before handing a plate on. Reading raw
    gray bytes out of ffmpeg keeps the stdlib-only rule intact.
    """
    result = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", str(path),
         "-vf", f"scale={width}:{height},format=gray", "-frames:v", "1",
         "-f", "rawvideo", "-pix_fmt", "gray", "-"],
        capture_output=True)
    data = result.stdout
    if result.returncode != 0 or not data:
        return None
    mean = sum(data) / len(data)
    variance = sum((v - mean) ** 2 for v in data) / len(data)
    return variance ** 0.5


def build_plate(style, index, out_path, seed, width=WIDTH, height=HEIGHT, prompt=""):
    """Draw a plate and keep drawing until it has structure in it.

    The generator checking its own output is the difference between a
    pipeline that emits blank frames and one that does not. It is bounded
    and deterministic: the same inputs always produce the same plate, and
    the escalation stops after the fixed list of lifts whether or not it
    succeeded - at which point the scene stage's own check is still there to
    refuse what this could not fix, rather than something here pretending.
    """
    stddev = None
    for lift in _STRUCTURE_LIFTS:
        build_still(style, index, out_path, seed, width=width, height=height,
                    prompt=prompt, lift=lift)
        stddev = measure_luma_stddev(out_path)
        if stddev is None or stddev >= MIN_PLATE_STDDEV:
            return stddev
        log.debug("%s: luma sd %.1f under %.1f; redrawing with %.2fx contrast",
                  out_path.name, stddev, MIN_PLATE_STDDEV, lift)
    return stddev


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
