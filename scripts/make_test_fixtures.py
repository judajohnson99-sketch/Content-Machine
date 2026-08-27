#!/usr/bin/env python3
"""Generate small deterministic test fixtures (images + audio) for the
render pipeline, using only the system ffmpeg binary. Safe to re-run;
overwrites existing fixtures.

Usage:
    python3 scripts/make_test_fixtures.py
"""
import logging
import shutil
import subprocess
import sys
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="[%(levelname)s] %(message)s")
log = logging.getLogger("make_test_fixtures")

ROOT = Path(__file__).resolve().parent.parent
IMAGES_DIR = ROOT / "tests" / "fixtures" / "images"
AUDIO_DIR = ROOT / "tests" / "fixtures" / "audio"

# name -> (R, G, B) so each fixture image is visually distinct.
IMAGE_COLORS = {
    "01_red.png": (196, 60, 60),
    "02_orange.png": (214, 130, 40),
    "03_green.png": (60, 150, 90),
    "04_blue.png": (50, 90, 170),
    "05_purple.png": (110, 70, 160),
}

IMAGE_SIZE = "1280x720"
AUDIO_SECONDS = 12  # shorter than the 30s test render, to exercise audio looping
FONT_CANDIDATES = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
]


def run(cmd):
    log.info("Running: %s", " ".join(cmd))
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        log.error("Command failed (exit %d): %s", result.returncode, " ".join(cmd))
        log.error(result.stderr.strip()[-2000:])
        raise SystemExit(1)


def find_font():
    for candidate in FONT_CANDIDATES:
        if Path(candidate).is_file():
            return candidate
    return None


def make_images(ffmpeg):
    IMAGES_DIR.mkdir(parents=True, exist_ok=True)
    font = find_font()
    for filename, (r, g, b) in IMAGE_COLORS.items():
        out_path = IMAGES_DIR / filename
        color = f"0x{r:02x}{g:02x}{b:02x}"
        if font:
            label = filename.split("_", 1)[1].rsplit(".", 1)[0].upper()
            vf = (
                f"drawtext=fontfile={font}:text='{label}':"
                "fontcolor=white:fontsize=72:x=(w-text_w)/2:y=(h-text_h)/2"
            )
            cmd = [
                ffmpeg, "-y", "-f", "lavfi",
                "-i", f"color=c={color}:s={IMAGE_SIZE}:d=1",
                "-vf", vf,
                "-frames:v", "1",
                str(out_path),
            ]
        else:
            cmd = [
                ffmpeg, "-y", "-f", "lavfi",
                "-i", f"color=c={color}:s={IMAGE_SIZE}:d=1",
                "-frames:v", "1",
                str(out_path),
            ]
        run(cmd)
    log.info("Wrote %d test images to %s", len(IMAGE_COLORS), IMAGES_DIR)


def make_audio(ffmpeg):
    AUDIO_DIR.mkdir(parents=True, exist_ok=True)
    out_path = AUDIO_DIR / "test_tone.wav"
    cmd = [
        ffmpeg, "-y", "-f", "lavfi",
        "-i", f"sine=frequency=440:duration={AUDIO_SECONDS}:sample_rate=44100",
        "-ac", "2",
        str(out_path),
    ]
    run(cmd)
    log.info("Wrote %ds test audio tone to %s", AUDIO_SECONDS, out_path)


def main():
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        log.error("ffmpeg not found on PATH. Install ffmpeg first.")
        sys.exit(1)
    make_images(ffmpeg)
    make_audio(ffmpeg)
    log.info("Test fixtures ready.")


if __name__ == "__main__":
    main()
