#!/usr/bin/env python3
"""Provider-agnostic audio subsystem.

Builds a complete audio track from layered sources, each supplied by a
named provider, then mixes, fits to an exact duration, normalises, and
records provenance and licensing for every layer.

    python3 scripts/audio.py providers
    python3 scripts/audio.py voices
    python3 scripts/audio.py compose --plan plan.json --output track.wav

Design:
- Every provider returns (wav_path, provenance). Provenance always carries
  a licence determination; a layer whose rights cannot be established is
  refused rather than silently used.
- Generated sources are synthesised at the exact target duration, so they
  have no loop seam at all. Only file-backed sources need looping, and
  those can be crossfaded at the join.
- Output is 48 kHz stereo PCM, matching what the renderer encodes to, so
  no resampling surprises downstream.
"""
import argparse
import json
import logging
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="[%(levelname)s] %(message)s")
log = logging.getLogger("audio")

ROOT = Path(__file__).resolve().parent.parent
VOICES_DIR = Path.home() / ".local" / "share" / "piper-voices"

SAMPLE_RATE = 48000
CHANNELS = 2
# Quiet enough for sleep/ambient use; loud enough to survive platform normalisation.
DEFAULT_LUFS = -18.0

# Fixed seed for noise sources: without it ffmpeg seeds from entropy and the
# same plan yields different audio on every run.
DEFAULT_NOISE_SEED = 20260827

# Rights for anything we synthesise ourselves: no third-party interest exists.
GENERATED_LICENCE = {
    "source": "synthesised locally with ffmpeg",
    "creator": "content-machine",
    "license": "generated-original",
    "commercial_use": True,
    "attribution_required": False,
    "evidence": "Signal is generated from ffmpeg oscillators/noise sources; no third-party recording involved.",
}

# Voice licence determinations. Each was read from the voice's MODEL_CARD in
# the rhasspy/piper-voices repository - never assumed.
DEFAULT_VOICE = "en_US-libritts_r-medium"

VOICES = {
    "en_US-libritts_r-medium": {
        "model": "en_US-libritts_r-medium.onnx",
        "dataset": "LibriTTS-R (openslr.org/141)",
        "license": "CC BY 4.0",
        "commercial_use": True,
        "attribution_required": True,
        "attribution_text": "Voice synthesised with Piper using the LibriTTS-R voice (CC BY 4.0).",
        "evidence": "MODEL_CARD at rhasspy/piper-voices en/en_US/libritts_r/medium states License: CC BY 4.0.",
        "speakers": 904,
    },
}

# Voices deliberately excluded, recorded so the decision is not re-litigated
# or accidentally reversed later.
REJECTED_VOICES = {
    "en_US-lessac-medium": "Blizzard Challenge 2013 licence - research/non-commercial use only.",
    "en_US-hfc_female-medium": "CC BY-NC-SA 4.0 - NonCommercial clause forbids monetized use.",
}


class AudioError(Exception):
    """Raised with a list of human-readable problems."""

    def __init__(self, problems):
        if isinstance(problems, str):
            problems = [problems]
        super().__init__("; ".join(problems))
        self.problems = problems


def utc_now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def run_ffmpeg(cmd, what):
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        tail = "\n".join(result.stderr.strip().splitlines()[-15:])
        raise AudioError([f"ffmpeg failed while {what} (exit {result.returncode}):\n{tail}"])


def probe_seconds(path):
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=nw=1:nk=1", str(path)],
        capture_output=True, text=True)
    if result.returncode != 0:
        return None
    try:
        return float(result.stdout.strip())
    except ValueError:
        return None


def mean_volume_db(path):
    result = subprocess.run(
        ["ffmpeg", "-hide_banner", "-i", str(path), "-af", "volumedetect", "-f", "null", "-"],
        capture_output=True, text=True)
    for line in result.stderr.splitlines():
        if "mean_volume:" in line:
            try:
                return float(line.split("mean_volume:")[1].strip().split()[0])
            except (IndexError, ValueError):
                return None
    return None


def _lavfi(source_expr, seconds, out_path, extra_filter=None):
    """Render a lavfi audio source to an exact-length stereo WAV."""
    chain = [f"atrim=0:{seconds}", f"aformat=sample_rates={SAMPLE_RATE}:channel_layouts=stereo"]
    if extra_filter:
        chain.insert(0, extra_filter)
    run_ffmpeg([
        "ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", source_expr,
        "-af", ",".join(chain), "-t", str(seconds),
        "-c:a", "pcm_s16le", "-ar", str(SAMPLE_RATE), "-ac", str(CHANNELS), str(out_path),
    ], f"generating {out_path.name}")


# --------------------------------------------------------------------------
# providers
# --------------------------------------------------------------------------

def provider_noise(params, seconds, workdir, index):
    """Coloured noise. Generated at exact length, so there is no loop seam.

    anoisesrc seeds from entropy unless told otherwise, which would make
    every render of the same plan different. Seeding it keeps the pipeline's
    determinism guarantee intact.
    """
    colour = params.get("color", "brown")
    allowed = ("white", "pink", "brown", "blue", "violet", "velvet")
    if colour not in allowed:
        raise AudioError([f"noise color must be one of {allowed}, got {colour!r}"])
    amplitude = float(params.get("amplitude", 0.5))
    seed = int(params.get("seed", DEFAULT_NOISE_SEED))
    out = workdir / f"layer{index}_noise.wav"
    _lavfi(f"anoisesrc=color={colour}:amplitude={amplitude}:seed={seed}"
           f":sample_rate={SAMPLE_RATE}", seconds, out)
    return out, dict(GENERATED_LICENCE, provider="noise",
                     parameters={"color": colour, "amplitude": amplitude, "seed": seed})


def provider_tone(params, seconds, workdir, index):
    """Sine drone, optionally softened with a lowpass - useful under ambience."""
    frequency = float(params.get("frequency", 110.0))
    lowpass = params.get("lowpass_hz")
    out = workdir / f"layer{index}_tone.wav"
    extra = f"lowpass=f={float(lowpass)}" if lowpass else None
    _lavfi(f"sine=frequency={frequency}:sample_rate={SAMPLE_RATE}", seconds, out, extra_filter=extra)
    return out, dict(GENERATED_LICENCE, provider="tone",
                     parameters={"frequency": frequency, "lowpass_hz": lowpass})


# Interval ratios (semitones above the root) for a few evocative chord
# colours a mood word can select. Small and deliberately not exhaustive -
# the point is a layer that is not one static frequency, not a synthesiser.
PAD_CHORDS = {
    "warm": (0, 4, 7), "cozy": (0, 4, 7), "cinematic": (0, 4, 7),
    "calm": (0, 5, 7), "dreamy": (0, 5, 7),
    "mysterious": (0, 3, 7), "melancholy": (0, 3, 7),
    "eerie": (0, 3, 6), "tense": (0, 3, 6),
}
DEFAULT_PAD_CHORD = (0, 5, 7)

# A gentle root-note drift so a pad held for hours does not sit on one exact
# frequency the whole time: the tonic, up a perfect fourth, back to the
# tonic, down a perfect fourth. Same chord shape throughout (only the root
# moves), so the drift is always consonant - a slow I-IV-I breathing motion,
# never a harmonic surprise.
DEFAULT_ROOT_DRIFT_RATIOS = (1.0, 2 ** (5 / 12), 1.0, 2 ** (-5 / 12))
PAD_SEGMENT_SECONDS = 480.0        # ~8 minutes on each root before it drifts
PAD_DRIFT_CROSSFADE_SECONDS = 20.0


def _render_pad_segment(root, chord, seed, seconds, out_path):
    """One fixed-root pad render: three sine voices at a chosen interval,
    each independently and slowly tremolo'd at a slightly different rate so
    the mix breathes rather than sitting static, then thickened with a
    chorus effect so it reads as a pad rather than three bare sine tones -
    a real, bounded perceptual improvement, not a claim that the result is
    professional-grade music (nothing here can verify that; see
    ``_quality_check``'s docstring).
    """
    intervals = PAD_CHORDS.get(chord, DEFAULT_PAD_CHORD)
    inputs, filters, labels = [], [], []
    for i, semitones in enumerate(intervals):
        freq = root * (2 ** (semitones / 12.0))
        # Each voice's tremolo rate is nudged apart so the voices do not
        # beat in lockstep. ffmpeg's tremolo filter requires f >= 0.1.
        rate = 0.12 + 0.03 * i + (seed % 7) * 0.001
        inputs += ["-f", "lavfi", "-i", f"sine=frequency={freq:.3f}:sample_rate={SAMPLE_RATE}"]
        label = f"v{i}"
        filters.append(
            f"[{i}:a]tremolo=f={rate:.4f}:d=0.25,volume={-6 - 3 * i}dB,"
            f"aformat=sample_rates={SAMPLE_RATE}:channel_layouts=stereo[{label}]")
        labels.append(f"[{label}]")
    filters.append(f"{''.join(labels)}amix=inputs={len(labels)}:duration=longest:"
                   f"normalize=0,lowpass=f=2200,"
                   f"chorus=0.7:0.9:55:0.4:0.25:2[mix]")
    run_ffmpeg([
        "ffmpeg", "-y", "-v", "error", *inputs,
        "-filter_complex", ";".join(filters), "-map", "[mix]",
        "-t", str(seconds), "-c:a", "pcm_s16le",
        "-ar", str(SAMPLE_RATE), "-ac", str(CHANNELS), str(out_path),
    ], f"generating {out_path.name}")


def _crossfade_concat(paths, crossfade, out, seconds):
    """Join distinct clips end to end with an ``acrossfade`` at each seam,
    trimmed to ``seconds`` - the multi-clip counterpart of
    ``_looped_with_crossfade``, which only ever repeats one clip.
    """
    inputs, filters = [], []
    for path in paths:
        inputs += ["-i", str(path)]
    for i in range(len(paths)):
        filters.append(f"[{i}:a]aformat=sample_rates={SAMPLE_RATE}:channel_layouts=stereo[c{i}]")
    label = "c0"
    for i in range(1, len(paths)):
        nxt = f"x{i}"
        filters.append(f"[{label}][c{i}]acrossfade=d={crossfade}:c1=tri:c2=tri[{nxt}]")
        label = nxt
    run_ffmpeg([
        "ffmpeg", "-y", "-v", "error", *inputs,
        "-filter_complex", ";".join(filters), "-map", f"[{label}]",
        "-t", str(seconds), "-c:a", "pcm_s16le",
        "-ar", str(SAMPLE_RATE), "-ac", str(CHANNELS), str(out),
    ], f"crossfade-concatenating {len(paths)} pad segment(s)")


def provider_pad(params, seconds, workdir, index):
    """A slowly evolving multi-oscillator harmonic pad - the "music" layer.

    With no ``root_drift_ratios`` this renders one fixed-root pad, exactly
    as before. With two or more ratios, the root drifts between them every
    ``segment_seconds`` (crossfaded, no audible seam) - the point being a
    piece that is not frozen on one frequency for a four-hour listen, while
    the chord shape stays constant so the movement is always gentle, never a
    harmonic surprise. Rights-clean like tone/noise: nothing here is a
    recording.
    """
    root = float(params.get("root_hz", 110.0))
    chord = params.get("chord")
    seed = int(params.get("seed", DEFAULT_NOISE_SEED))
    ratios = params.get("root_drift_ratios")
    out = workdir / f"layer{index}_pad.wav"

    if not ratios or len(ratios) < 2:
        _render_pad_segment(root, chord, seed, seconds, out)
    else:
        segment_seconds = float(params.get("segment_seconds", PAD_SEGMENT_SECONDS))
        crossfade = min(PAD_DRIFT_CROSSFADE_SECONDS, segment_seconds / 4)
        n_segments = max(int(seconds // segment_seconds) + 2, 2)
        segment_paths = []
        for i in range(n_segments):
            ratio = ratios[i % len(ratios)]
            seg_out = workdir / f"layer{index}_pad_seg{i}.wav"
            _render_pad_segment(root * ratio, chord, seed, segment_seconds + crossfade, seg_out)
            segment_paths.append(seg_out)
        _crossfade_concat(segment_paths, crossfade, out, seconds)

    intervals = PAD_CHORDS.get(chord, DEFAULT_PAD_CHORD)
    return out, dict(GENERATED_LICENCE, provider="pad",
                     parameters={"root_hz": root, "chord": chord or "default",
                                "intervals": list(intervals),
                                "root_drift_ratios": (list(ratios) if ratios and len(ratios) > 1
                                                      else None)})


# --------------------------------------------------------------------------
# generative ambient music
# --------------------------------------------------------------------------
#
# The difference between "a musical layer is present" and "this is music
# somebody would leave on for four hours" is harmonic movement, timbre and
# space. `pad` has none of the three: it holds one chord on bare sine
# voices with no tail. This provider adds all three, and is the only
# procedural source this codebase will describe as music:
#
#   * a chord *progression*, not a chord - each chord held for the better
#     part of a minute and crossfaded into the next, so the piece resolves
#     and moves instead of droning;
#   * voices built from a fundamental plus two quieter partials, detuned
#     against each other, so the timbre is an instrument-like tone rather
#     than an oscillator;
#   * sparse bell voices on chord tones whose repeat periods are mutually
#     prime, so they phase against one another and never settle into an
#     audible loop inside a cycle (the Music-for-Airports trick);
#   * a reverb tail, which is what makes the result sound like a room
#     rather than a signal generator.
#
# None of that is a claim that the output is *good*. It cannot be: no
# measurement available here evaluates whether a human finds music
# pleasant. It is a claim that the technique is the one the genre actually
# uses, and the judgement stays with a listener (see `_quality_check`, and
# provenance.audio.production_grade in scripts/project.py).

# (root offset in semitones, chord shape) per step. Every progression stays
# diatonic and resolves back to its first chord, so looping a cycle is
# musically seamless rather than merely crossfaded.
MUSIC_PROGRESSIONS = {
    "warm":       ((0, (0, 4, 7, 11)), (9, (0, 3, 7, 10)), (5, (0, 4, 7, 11)), (7, (0, 4, 7, 9))),
    "cozy":       ((0, (0, 4, 7, 11)), (9, (0, 3, 7, 10)), (5, (0, 4, 7, 11)), (7, (0, 4, 7, 9))),
    "calm":       ((0, (0, 4, 7, 11)), (5, (0, 4, 7, 11)), (9, (0, 3, 7, 10)), (2, (0, 3, 7, 10))),
    "dreamy":     ((0, (0, 2, 7, 11)), (7, (0, 2, 7, 9)), (5, (0, 4, 7, 11)), (2, (0, 3, 7, 10))),
    "cinematic":  ((0, (0, 4, 7, 11)), (7, (0, 4, 7, 9)), (9, (0, 3, 7, 10)), (5, (0, 4, 7, 11))),
    "melancholy": ((0, (0, 3, 7, 10)), (8, (0, 4, 7, 11)), (3, (0, 4, 7, 11)), (5, (0, 3, 7, 10))),
    "mysterious": ((0, (0, 3, 7, 10)), (5, (0, 3, 7, 10)), (10, (0, 4, 7, 11)), (3, (0, 4, 7, 11))),
    "eerie":      ((0, (0, 3, 7, 10)), (1, (0, 4, 7, 11)), (0, (0, 3, 7, 10)), (8, (0, 4, 7, 11))),
}
DEFAULT_MUSIC_PROGRESSION = MUSIC_PROGRESSIONS["calm"]

MUSIC_CHORD_SECONDS = 42.0          # one chord's hold before it moves
MUSIC_CHORD_CROSSFADE = 8.0         # long enough that no chord change is a "cut"
MUSIC_CYCLE_CROSSFADE = 12.0        # seam when the whole progression repeats
# Mutually prime so two bell voices coincide only once every ~2 minutes.
MUSIC_BELL_PERIODS = (7.0, 11.0, 17.0)
# Past this duration a single cycle is audibly a loop, so the piece is
# built from several differently-voiced passes instead.
MUSIC_VARIATION_SECONDS = 900.0
DEFAULT_MUSIC_VARIATIONS = 3
MAX_MUSIC_VARIATIONS = 6
# Far enough apart that two variations share no bell phase relationship.
MUSIC_VARIATION_SEED_STEP = 101
MUSIC_REVERB = "aecho=0.8:0.85:320|610|1050|1700:0.38|0.28|0.2|0.12"


def _music_chord_segment(root, shape, seed, seconds, out_path, bell=True):
    """One chord of the progression: pad voices, bell voices, reverb."""
    inputs, filters, labels = [], [], []
    index = 0
    for voice, semitones in enumerate(shape):
        base = root * (2 ** (semitones / 12.0))
        # A fundamental alone is an oscillator. Two quieter partials, each
        # detuned by a few cents against the others, is a tone.
        for partial, (multiple, gain) in enumerate(((1.0, -9.0), (2.0, -19.0), (3.0, -26.0))):
            detune = 1.0 + ((seed + voice * 7 + partial * 3) % 5 - 2) * 0.0009
            freq = base * multiple * detune
            if freq > 5000:
                continue
            # ffmpeg's tremolo refuses f < 0.1, so this is the slowest
            # breathing rate it will accept, nudged apart per voice.
            rate = 0.10 + 0.019 * voice + 0.007 * partial
            inputs += ["-f", "lavfi", "-i",
                       f"sine=frequency={freq:.3f}:sample_rate={SAMPLE_RATE}"]
            label = f"p{index}"
            filters.append(
                f"[{index}:a]tremolo=f={rate:.4f}:d=0.22,"
                f"volume={gain - 2.0 * voice}dB,"
                f"aformat=sample_rates={SAMPLE_RATE}:channel_layouts=stereo[{label}]")
            labels.append(f"[{label}]")
            index += 1

    if bell:
        for bell_index, period in enumerate(MUSIC_BELL_PERIODS):
            semitones = shape[(bell_index + 1) % len(shape)]
            # Bells sit two octaves above the pad so they read as a separate
            # instrument rather than thickening the chord.
            freq = root * (2 ** (semitones / 12.0)) * 4
            offset = (seed + bell_index * 13) % int(period)
            inputs += ["-f", "lavfi", "-i",
                       f"sine=frequency={freq:.3f}:sample_rate={SAMPLE_RATE}"]
            label = f"b{bell_index}"
            # A struck note: instant attack, exponential decay, silence
            # until the next strike. `mod` makes it repeat on its own
            # period without needing one input per note.
            envelope = (f"exp(-8*mod(t+{offset},{period:g}))"
                        f"*lt(mod(t+{offset},{period:g}),3)")
            filters.append(
                f"[{index}:a]volume='{envelope}':eval=frame,"
                f"volume=-17dB,"
                f"aformat=sample_rates={SAMPLE_RATE}:channel_layouts=stereo[{label}]")
            labels.append(f"[{label}]")
            index += 1

    filters.append(
        f"{''.join(labels)}amix=inputs={len(labels)}:duration=longest:normalize=0,"
        f"lowpass=f=3200,{MUSIC_REVERB},"
        f"chorus=0.6:0.9:50|70:0.35|0.3:0.25|0.4:2|1.3[mix]")
    run_ffmpeg([
        "ffmpeg", "-y", "-v", "error", *inputs,
        "-filter_complex", ";".join(filters), "-map", "[mix]",
        "-t", str(seconds), "-c:a", "pcm_s16le",
        "-ar", str(SAMPLE_RATE), "-ac", str(CHANNELS), str(out_path),
    ], f"generating {out_path.name}")


def provider_music(params, seconds, workdir, index):
    """Generative ambient music: a resolving chord progression with bells.

    Renders one cycle of the progression (a few minutes) and loops it with
    a long crossfade, so a four-hour track costs the same handful of ffmpeg
    runs as a four-minute one. Rights-clean for the same reason tone/noise
    are: every sample is synthesised here, and no recording is involved.
    """
    root = float(params.get("root_hz", 110.0))
    mood = params.get("mood") or params.get("chord")
    progression = MUSIC_PROGRESSIONS.get(mood, DEFAULT_MUSIC_PROGRESSION)
    seed = int(params.get("seed", DEFAULT_NOISE_SEED))
    chord_seconds = float(params.get("chord_seconds", MUSIC_CHORD_SECONDS))
    crossfade = min(MUSIC_CHORD_CROSSFADE, chord_seconds / 3)
    out = workdir / f"layer{index}_music.wav"

    # A cycle can be no longer than the track itself.
    steps = max(2, min(len(progression),
                       int(seconds // max(chord_seconds, 1.0)) or 2))
    chord_seconds = min(chord_seconds, max(seconds / steps, 4.0))
    crossfade = min(crossfade, chord_seconds / 3)

    # How many differently-voiced passes through the progression are
    # rendered before anything repeats. One cycle looped for four hours is
    # the same 168 seconds eighty-six times, which is the single most
    # audible "this was generated" tell in long-form ambient. Each
    # variation keeps the harmony (same chords, same order, same key) and
    # changes only its surface - the bell phase seeds and the voicing
    # inversion - so the piece stays coherent while never repeating
    # verbatim inside a listening session.
    variations = max(1, min(int(params.get("variations", 1)),
                            MAX_MUSIC_VARIATIONS))
    if seconds >= MUSIC_VARIATION_SECONDS and "variations" not in params:
        variations = DEFAULT_MUSIC_VARIATIONS
    # A variation can only exist if there is room for it to play.
    variations = max(1, min(variations,
                            int(seconds // max(steps * chord_seconds, 1.0)) or 1))

    segments = []
    for variation in range(variations):
        for step in range(steps):
            offset, shape = progression[step % len(progression)]
            if variation % 2:
                # First inversion: the root moves up an octave, so the same
                # chord sits differently against the one before it.
                shape = tuple(sorted(shape[1:] + (shape[0] + 12,)))
            seg = workdir / f"layer{index}_music_v{variation}c{step}.wav"
            _music_chord_segment(
                root * (2 ** (offset / 12.0)), shape,
                seed + step + variation * MUSIC_VARIATION_SEED_STEP,
                chord_seconds + crossfade, seg, bell=chord_seconds >= 8.0)
            segments.append(seg)

    cycle = workdir / f"layer{index}_music_cycle.wav"
    cycle_seconds = min(seconds, variations * steps * chord_seconds)
    _crossfade_concat(segments, crossfade, cycle, cycle_seconds)

    source_seconds = probe_seconds(cycle) or cycle_seconds
    if source_seconds >= seconds - 0.05:
        run_ffmpeg([
            "ffmpeg", "-y", "-v", "error", "-i", str(cycle), "-t", str(seconds),
            "-c:a", "pcm_s16le", "-ar", str(SAMPLE_RATE), "-ac", str(CHANNELS), str(out),
        ], f"trimming {out.name}")
    else:
        _looped_with_crossfade(
            cycle, out, seconds, source_seconds,
            min(MUSIC_CYCLE_CROSSFADE, source_seconds / 4), workdir, index)

    return out, dict(GENERATED_LICENCE, provider="music",
                     parameters={"root_hz": root, "mood": mood or "calm",
                                 "progression": [list(step[1]) for step in progression[:steps]],
                                 "chord_seconds": chord_seconds,
                                 "variations": variations,
                                 "cycle_seconds": round(source_seconds, 2),
                                 "seed": seed})


def provider_rain(params, seconds, workdir, index):
    """Procedural rain-like bed: shaped noise, no recording and no licensing.

    Not a substitute for a real field recording, but it is rights-clean and
    costs nothing, which makes it usable for testing the format before
    committing to licensed audio.
    """
    intensity = str(params.get("intensity", "steady"))
    presets = {
        "light": (900, 6500), "steady": (500, 5000), "heavy": (300, 3500),
    }
    if intensity not in presets:
        raise AudioError([f"rain intensity must be one of {tuple(presets)}, got {intensity!r}"])
    high, low = presets[intensity]
    seed = int(params.get("seed", DEFAULT_NOISE_SEED))
    out = workdir / f"layer{index}_rain.wav"
    # Band-limit brown noise, then apply slow tremolo so it breathes rather
    # than sitting perfectly static.
    shaping = f"highpass=f={high},lowpass=f={low},tremolo=f=0.3:d=0.12"
    _lavfi(f"anoisesrc=color=brown:amplitude=0.8:seed={seed}:sample_rate={SAMPLE_RATE}",
           seconds, out, extra_filter=shaping)
    return out, dict(GENERATED_LICENCE, provider="rain",
                     parameters={"intensity": intensity, "seed": seed},
                     notes="Procedural approximation, not a field recording.")


def provider_silence(params, seconds, workdir, index):
    out = workdir / f"layer{index}_silence.wav"
    _lavfi(f"anullsrc=r={SAMPLE_RATE}:cl=stereo", seconds, out)
    return out, dict(GENERATED_LICENCE, provider="silence", parameters={})


def provider_file(params, seconds, workdir, index):
    """A local audio asset. Rights must be supplied explicitly - we never guess."""
    raw_path = params.get("path")
    if not raw_path:
        raise AudioError(["file provider requires 'path'"])
    src = Path(raw_path)
    if not src.is_absolute():
        src = (ROOT / src).resolve()
    if not src.is_file():
        raise AudioError([f"audio file not found: {src}"])
    if probe_seconds(src) is None:
        raise AudioError([f"unreadable audio file: {src}"])

    licence = params.get("license") or {}
    missing = [f for f in ("source", "license", "commercial_use") if f not in licence]
    if missing:
        raise AudioError([
            f"file provider refuses '{src.name}': licence fields missing {missing}. "
            "External audio must declare its rights; nothing is assumed to be usable."
        ])
    if not licence.get("commercial_use"):
        raise AudioError([
            f"file provider refuses '{src.name}': licence declares commercial_use=false."
        ])

    out = workdir / f"layer{index}_file.wav"
    crossfade = float(params.get("crossfade_loop_seconds", 0))
    source_seconds = probe_seconds(src)

    if crossfade > 0 and source_seconds and source_seconds < seconds:
        _looped_with_crossfade(src, out, seconds, source_seconds, crossfade, workdir, index)
    else:
        # -stream_loop repeats the input; -t trims to the exact target.
        run_ffmpeg([
            "ffmpeg", "-y", "-v", "error", "-stream_loop", "-1", "-i", str(src),
            "-t", str(seconds), "-af", f"aformat=sample_rates={SAMPLE_RATE}:channel_layouts=stereo",
            "-c:a", "pcm_s16le", "-ar", str(SAMPLE_RATE), "-ac", str(CHANNELS), str(out),
        ], f"looping {src.name}")

    provenance = {
        "provider": "file",
        "source": licence["source"],
        "creator": licence.get("creator"),
        "license": licence["license"],
        "commercial_use": bool(licence["commercial_use"]),
        "attribution_required": bool(licence.get("attribution_required", False)),
        "attribution_text": licence.get("attribution_text"),
        "evidence": licence.get("evidence", "Declared by the project's audio plan."),
        "retrieved_utc": licence.get("retrieved_utc"),
        "parameters": {"path": str(src), "crossfade_loop_seconds": crossfade},
    }
    return out, provenance


def _looped_with_crossfade(src, out, seconds, source_seconds, crossfade, workdir, index):
    """Repeat a clip with acrossfade at each join, removing the loop click."""
    if crossfade >= source_seconds:
        raise AudioError([
            f"crossfade_loop_seconds ({crossfade}) must be shorter than the "
            f"source audio ({source_seconds:.2f}s)"
        ])
    stride = source_seconds - crossfade
    copies = max(int(seconds // stride) + 2, 2)
    inputs, filters, label = [], [], None
    for i in range(copies):
        inputs += ["-i", str(src)]
    for i in range(copies):
        filters.append(f"[{i}:a]aformat=sample_rates={SAMPLE_RATE}:channel_layouts=stereo[c{i}]")
    label = "c0"
    for i in range(1, copies):
        nxt = f"x{i}"
        filters.append(f"[{label}][c{i}]acrossfade=d={crossfade}:c1=tri:c2=tri[{nxt}]")
        label = nxt
    run_ffmpeg([
        "ffmpeg", "-y", "-v", "error", *inputs,
        "-filter_complex", ";".join(filters), "-map", f"[{label}]",
        "-t", str(seconds), "-c:a", "pcm_s16le",
        "-ar", str(SAMPLE_RATE), "-ac", str(CHANNELS), str(out),
    ], f"crossfade-looping {src.name}")


def provider_tts(params, seconds, workdir, index):
    """Local Piper TTS. Offline, free, and deterministic when noise is zeroed."""
    text = (params.get("text") or "").strip()
    if not text:
        raise AudioError(["tts provider requires non-empty 'text'"])
    voice_name = params.get("voice", DEFAULT_VOICE)
    if voice_name in REJECTED_VOICES:
        raise AudioError([
            f"voice '{voice_name}' is not usable: {REJECTED_VOICES[voice_name]}"
        ])
    voice = VOICES.get(voice_name)
    if not voice:
        raise AudioError([
            f"unknown voice '{voice_name}'. Known: {', '.join(VOICES)}"
        ])
    model_path = VOICES_DIR / voice["model"]
    if not model_path.is_file():
        raise AudioError([
            f"voice model not installed: {model_path}. "
            "See README 'Narration' for the one-time download command."
        ])

    python = _piper_python()
    if not python:
        raise AudioError([
            "piper-tts is not installed. Install with: .venv/bin/pip install piper-tts"
        ])

    raw = workdir / f"layer{index}_tts_raw.wav"
    # Zeroed noise scales make synthesis byte-reproducible, preserving the
    # pipeline's determinism guarantee. It also flattens prosody, which suits
    # calm narration.
    cmd = [
        python, "-m", "piper", "--model", str(model_path),
        "--speaker", str(params.get("speaker", 0)),
        "--length-scale", str(params.get("length_scale", 1.0)),
        "--noise-scale", str(params.get("noise_scale", 0.0)),
        "--noise-w-scale", str(params.get("noise_w_scale", 0.0)),
        "--sentence-silence", str(params.get("sentence_silence", 0.5)),
        "--output-file", str(raw),
    ]
    result = subprocess.run(cmd, input=text, capture_output=True, text=True)
    if result.returncode != 0 or not raw.is_file():
        tail = "\n".join(result.stderr.strip().splitlines()[-15:])
        raise AudioError([f"piper synthesis failed (exit {result.returncode}):\n{tail}"])

    spoken = probe_seconds(raw) or 0.0
    out = workdir / f"layer{index}_tts.wav"
    # Narration is padded, never looped: repeating speech would be nonsense.
    run_ffmpeg([
        "ffmpeg", "-y", "-v", "error", "-i", str(raw),
        "-af", f"aformat=sample_rates={SAMPLE_RATE}:channel_layouts=stereo,apad",
        "-t", str(seconds), "-c:a", "pcm_s16le",
        "-ar", str(SAMPLE_RATE), "-ac", str(CHANNELS), str(out),
    ], "padding narration")

    provenance = {
        "provider": "tts",
        "engine": "piper",
        "voice": voice_name,
        "source": voice["dataset"],
        "license": voice["license"],
        "commercial_use": voice["commercial_use"],
        "attribution_required": voice["attribution_required"],
        "attribution_text": voice.get("attribution_text"),
        "evidence": voice["evidence"],
        "parameters": {
            "speaker": params.get("speaker", 0),
            "length_scale": params.get("length_scale", 1.0),
            "noise_scale": params.get("noise_scale", 0.0),
            "noise_w_scale": params.get("noise_w_scale", 0.0),
        },
        "spoken_seconds": round(spoken, 3),
        "deterministic": params.get("noise_scale", 0.0) == 0.0 and params.get("noise_w_scale", 0.0) == 0.0,
    }
    if spoken > seconds:
        provenance["warning"] = (
            f"narration is {spoken:.1f}s but the target is {seconds:.1f}s; it was truncated"
        )
    return out, provenance


def narration_status(voice_name=DEFAULT_VOICE):
    """Can this host narrate right now, and if not, exactly what is missing?

    Both halves are checked, because either one alone is a green light that
    lies: piper-tts imports fine with no voice on disk, and a downloaded
    voice is useless in an environment that never got the package. The
    control center showed a hardcoded green "synth - piper" light before this
    existed, which is precisely the kind of claim this codebase does not make
    on a human's behalf.

    Ordered cheapest-first: the model check is a stat, so a host without the
    voice never pays for the import probe.
    """
    if voice_name in REJECTED_VOICES:
        return {"available": False, "engine": "piper", "voice": voice_name,
                "detail": f"voice '{voice_name}' is not usable: {REJECTED_VOICES[voice_name]}"}
    voice = VOICES.get(voice_name)
    if not voice:
        return {"available": False, "engine": "piper", "voice": voice_name,
                "detail": f"unknown voice '{voice_name}'. Known: {', '.join(VOICES)}"}
    model_path = VOICES_DIR / voice["model"]
    if not model_path.is_file():
        return {"available": False, "engine": "piper", "voice": voice_name,
                "detail": f"voice model not installed: {model_path}. "
                          "See README 'Narration' for the one-time download command."}
    if not _piper_python():
        return {"available": False, "engine": "piper", "voice": voice_name,
                "detail": "piper-tts is not installed. Install with: "
                          ".venv/bin/pip install piper-tts"}
    return {"available": True, "engine": "piper", "voice": voice_name,
            "detail": f"local Piper narration, voice {voice_name}"}


def _piper_python():
    """Piper lives in the venv; the rest of the pipeline runs on system python."""
    venv_python = ROOT / ".venv" / "bin" / "python"
    for candidate in (venv_python, Path(sys.executable)):
        if not candidate.exists():
            continue
        probe = subprocess.run([str(candidate), "-c", "import piper"],
                               capture_output=True, text=True)
        if probe.returncode == 0:
            return str(candidate)
    return None


# --------------------------------------------------------------------------
# licensed music library
# --------------------------------------------------------------------------

MUSIC_LIBRARY_PATH = ROOT / "assets" / "music" / "library.json"

# Every field a track must declare before this codebase will play it.
# Deliberately the same set `provider_file` enforces at render time: a
# library entry is a promise about rights, and an unprovable promise is
# refused here rather than discovered halfway through a four-hour render.
REQUIRED_TRACK_FIELDS = ("id", "path", "source", "license", "commercial_use")


def load_music_library(path=None):
    """Rights-declared local music, or ``[]`` when there is no library.

    The one path by which a real recording - something an actual musician
    played - can become a video's bed. Empty is the normal state for a
    fresh checkout; it is not an error, and nothing substitutes for it
    silently (see ``creative.route_audio``).
    """
    path = Path(path) if path else MUSIC_LIBRARY_PATH
    if not path.is_file():
        return []
    payload = json.loads(path.read_text())
    tracks = payload.get("tracks") if isinstance(payload, dict) else payload
    usable = []
    for track in tracks or []:
        if not isinstance(track, dict):
            continue
        if any(not track.get(f) for f in REQUIRED_TRACK_FIELDS):
            log.warning("music library: skipping %r - missing %s",
                        track.get("id") or track.get("path"),
                        [f for f in REQUIRED_TRACK_FIELDS if not track.get(f)])
            continue
        src = Path(track["path"])
        if not src.is_absolute():
            src = (ROOT / src)
        if not src.is_file():
            log.warning("music library: %r declares a file that is not there: %s",
                        track["id"], src)
            continue
        usable.append(dict(track, path=str(src)))
    return usable


def select_library_track(mood=None, tags=(), library=None):
    """The best rights-clean library track for a mood, or ``None``.

    Scored, not filtered: a library with nothing tagged for this mood still
    offers its most generally-suitable track rather than nothing, because a
    real recording that is merely imperfect still beats a synthesiser.
    """
    tracks = library if library is not None else load_music_library()
    if not tracks:
        return None
    wanted = {t.strip().lower() for t in tags if t}
    if mood:
        wanted.add(str(mood).strip().lower())

    def score(track):
        have = {str(t).strip().lower() for t in (track.get("tags") or [])}
        if track.get("mood"):
            have.add(str(track["mood"]).strip().lower())
        return (len(have & wanted), float(track.get("duration_seconds") or 0))

    return max(tracks, key=score)


# --------------------------------------------------------------------------
# environmental ambience and sparse sound events
# --------------------------------------------------------------------------
#
# A bed alone is a texture; a *place* is a bed plus the things that happen
# in it. These two providers are what let a plan say "a room at night, with
# a clock and the occasional creak" instead of "brown noise". Both are
# synthesised, so both are rights-clean, and neither claims to be a field
# recording - the manifest says procedural in as many words.

# element -> (noise colour, amplitude, band shaping, (lfo rate Hz, depth))
# The movement is what keeps a bed from reading as a test tone: real
# environments breathe, and a perfectly static one is the single most
# recognisable "this is synthesised" tell. The rates that matter here are
# far below ffmpeg's `tremolo` floor of 0.1 Hz - a swell every half minute,
# not a wobble - so movement is an explicit volume envelope instead.
AMBIENCE_ELEMENTS = {
    "room_tone":       ("brown", 0.35, "highpass=f=30,lowpass=f=260",    (0.017, 0.18)),
    "wind_low":        ("brown", 0.80, "highpass=f=60,lowpass=f=700",    (0.033, 0.55)),
    "wind_high":       ("pink",  0.60, "highpass=f=350,lowpass=f=2600",  (0.055, 0.60)),
    "ocean_surf":      ("brown", 0.90, "highpass=f=40,lowpass=f=1300",   (0.090, 0.70)),
    "stream":          ("pink",  0.60, "highpass=f=420,lowpass=f=6500",  (0.400, 0.12)),
    "fireplace":       ("brown", 0.80, "highpass=f=70,lowpass=f=1100",   (1.900, 0.45)),
    "night_air":       ("pink",  0.35, "highpass=f=1800,lowpass=f=9000", (0.025, 0.25)),
    "distant_traffic": ("brown", 0.70, "highpass=f=40,lowpass=f=420",    (0.021, 0.30)),
    "cabin_hum":       ("brown", 0.50, "highpass=f=45,lowpass=f=190",    (0.013, 0.12)),
    "snowfall":        ("pink",  0.40, "highpass=f=900,lowpass=f=5200",  (0.037, 0.35)),
    "rain_on_glass":   ("pink",  0.55, "highpass=f=500,lowpass=f=7000",  (0.060, 0.20)),
}


def lfo_volume(rate_hz, depth):
    """A slow amplitude swell, as a `volume` expression.

    ffmpeg's `tremolo` refuses anything under 0.1 Hz, which rules out every
    rate a bed actually wants - one breath every thirty seconds, not ten a
    second. An explicit envelope has no such floor and is exact.
    """
    rate = max(float(rate_hz), 0.0)
    depth = min(max(float(depth), 0.0), 1.0)
    if rate <= 0 or depth <= 0:
        return None
    return (f"volume='{1 - depth:.4f}+{depth:.4f}*(0.5+0.5*sin(2*PI*{rate:.5f}*t))'"
            ":eval=frame")


def provider_ambience(params, seconds, workdir, index):
    """A named environmental bed, synthesised at the exact target length.

    Unlike ``noise``/``tone`` this is chosen by *place* rather than by
    signal - "fireplace", "ocean_surf" - which is the vocabulary a sound
    designer (or the design pass in ``scripts/sound_design.py``) actually
    works in. The recipe behind each name is deliberately visible in
    ``AMBIENCE_ELEMENTS`` rather than hidden in a model.
    """
    element = str(params.get("element", "room_tone"))
    if element not in AMBIENCE_ELEMENTS:
        raise AudioError([
            f"unknown ambience element {element!r}; available: "
            f"{', '.join(sorted(AMBIENCE_ELEMENTS))}"])
    colour, amplitude, shaping, movement = AMBIENCE_ELEMENTS[element]
    seed = int(params.get("seed", DEFAULT_NOISE_SEED))
    amplitude = float(params.get("amplitude", amplitude))
    chain = shaping
    if params.get("movement", True):
        rate, depth = movement
        envelope = lfo_volume(params.get("movement_rate_hz", rate),
                              params.get("movement_depth", depth))
        if envelope:
            chain = f"{chain},{envelope}"
    out = workdir / f"layer{index}_ambience_{element}.wav"
    _lavfi(f"anoisesrc=color={colour}:amplitude={amplitude}:seed={seed}:sample_rate={SAMPLE_RATE}",
           seconds, out, extra_filter=chain)
    return out, dict(GENERATED_LICENCE, provider="ambience",
                     parameters={"element": element, "seed": seed,
                                 "amplitude": amplitude},
                     notes="Procedural environmental bed, not a field recording.")


# element -> recipe for one short sound. 'decay' is the exponent of the
# amplitude envelope (higher = shorter), 'seconds' the tail it is given.
EVENT_ELEMENTS = {
    "chime":           {"kind": "tone",  "frequency": 880.0, "partials": (1.0, 2.0, 3.01), "decay": 1.6, "seconds": 3.0, "reverb": True},
    "bell":            {"kind": "tone",  "frequency": 392.0, "partials": (1.0, 2.76, 5.40), "decay": 0.9, "seconds": 5.0, "reverb": True},
    "drip":            {"kind": "tone",  "frequency": 1400.0, "partials": (1.0, 2.0), "decay": 14.0, "seconds": 0.6, "reverb": True},
    "bird":            {"kind": "tone",  "frequency": 2600.0, "partials": (1.0,), "decay": 9.0, "seconds": 0.7, "vibrato": True},
    "wood_creak":      {"kind": "noise", "colour": "brown", "shaping": "highpass=f=120,lowpass=f=900", "decay": 2.2, "seconds": 1.6},
    "crackle":         {"kind": "noise", "colour": "white", "shaping": "highpass=f=900,lowpass=f=6000", "decay": 30.0, "seconds": 0.25},
    "page_turn":       {"kind": "noise", "colour": "pink",  "shaping": "highpass=f=600,lowpass=f=7000", "decay": 7.0, "seconds": 0.7},
    "distant_thunder": {"kind": "noise", "colour": "brown", "shaping": "highpass=f=20,lowpass=f=180",  "decay": 0.6, "seconds": 7.0, "reverb": True},
    "wind_gust":       {"kind": "noise", "colour": "brown", "shaping": "highpass=f=60,lowpass=f=700",  "decay": 0.45, "seconds": 8.0},
}

EVENT_REVERB = "aecho=0.7:0.8:290|570|910:0.32|0.22|0.14"
# A ceiling on how many one-shots go into a single pattern block. Past this
# the events stop reading as occasional and the filter graph gets silly.
MAX_EVENTS_PER_BLOCK = 48


def _event_one_shot(element, seed, out_path):
    """Synthesise one instance of a named event to its own WAV."""
    recipe = EVENT_ELEMENTS[element]
    tail = float(recipe["seconds"])
    decay = float(recipe["decay"])
    envelope = f"volume='exp(-{decay}*t)':eval=frame"

    if recipe["kind"] == "tone":
        partials = recipe["partials"]
        inputs, filters, labels = [], [], []
        for voice, ratio in enumerate(partials):
            freq = float(recipe["frequency"]) * float(ratio)
            inputs += ["-f", "lavfi", "-i",
                       f"sine=frequency={freq:.3f}:duration={tail}:sample_rate={SAMPLE_RATE}"]
            gain = 1.0 / (voice + 1) ** 1.5
            filters.append(f"[{voice}:a]volume={gain:.4f}[v{voice}]")
            labels.append(f"[v{voice}]")
        chain = [envelope]
        if recipe.get("vibrato"):
            chain.insert(0, "vibrato=f=7:d=0.35")
        if recipe.get("reverb"):
            chain.append(EVENT_REVERB)
        chain.append(f"aformat=sample_rates={SAMPLE_RATE}:channel_layouts=stereo")
        filters.append(
            f"{''.join(labels)}amix=inputs={len(labels)}:normalize=0,"
            f"{','.join(chain)}[shot]")
        run_ffmpeg([
            "ffmpeg", "-y", "-v", "error", *inputs,
            "-filter_complex", ";".join(filters), "-map", "[shot]",
            "-t", str(tail), "-c:a", "pcm_s16le",
            "-ar", str(SAMPLE_RATE), "-ac", str(CHANNELS), str(out_path),
        ], f"synthesising {element}")
        return tail

    chain = [recipe["shaping"], envelope]
    if recipe.get("reverb"):
        chain.append(EVENT_REVERB)
    _lavfi(f"anoisesrc=color={recipe['colour']}:amplitude=0.9:seed={seed}:sample_rate={SAMPLE_RATE}",
           tail, out_path, extra_filter=",".join(chain))
    return tail


def _event_offsets(count, block_seconds, guard, seed):
    """Deterministic, non-clustered placements inside one pattern block.

    Placed one per slot with jitter inside the slot rather than uniformly at
    random: uniform placement clumps, and a clump of three chimes inside two
    seconds is the thing a listener notices as wrong.
    """
    import random
    rng = random.Random(seed)
    usable = max(block_seconds - 2 * guard, 0.0)
    if count <= 0 or usable <= 0:
        return []
    slot = usable / count
    return [round(guard + i * slot + rng.uniform(0.05, 0.95) * slot, 3)
            for i in range(count)]


def provider_events(params, seconds, workdir, index):
    """Sparse one-shot sounds scattered over the full duration.

    Rendered as one pattern block that is then crossfade-looped, so a
    four-hour track costs the same handful of ffmpeg calls as a four-minute
    one. Both ends of the block are kept deliberately empty, so the loop
    seam never lands in the middle of an event.
    """
    element = str(params.get("element", "chime"))
    if element not in EVENT_ELEMENTS:
        raise AudioError([
            f"unknown event element {element!r}; available: "
            f"{', '.join(sorted(EVENT_ELEMENTS))}"])
    every = float(params.get("every_seconds", 45.0))
    if every <= 0:
        raise AudioError([f"events every_seconds must be > 0, got {every}"])
    seed = int(params.get("seed", DEFAULT_NOISE_SEED))

    shot = workdir / f"layer{index}_shot_{element}.wav"
    tail = _event_one_shot(element, seed, shot)

    block_seconds = min(float(seconds), max(60.0, every * 12.0))
    guard = max(tail + 1.0, block_seconds * 0.04)
    count = max(1, min(MAX_EVENTS_PER_BLOCK,
                       int(round((block_seconds - 2 * guard) / every))))
    offsets = _event_offsets(count, block_seconds, guard, seed)
    if not offsets:
        offsets = [round(block_seconds / 2, 3)]

    block = workdir / f"layer{index}_events_{element}.wav"
    filters = [f"[1:a]asplit={len(offsets)}" + "".join(f"[e{i}]" for i in range(len(offsets)))]
    labels = ["[0:a]"]
    for i, offset in enumerate(offsets):
        ms = int(offset * 1000)
        filters.append(f"[e{i}]adelay={ms}|{ms}[d{i}]")
        labels.append(f"[d{i}]")
    filters.append(
        f"{''.join(labels)}amix=inputs={len(labels)}:duration=first:normalize=0,"
        f"aformat=sample_rates={SAMPLE_RATE}:channel_layouts=stereo[block]")
    run_ffmpeg([
        "ffmpeg", "-y", "-v", "error",
        "-f", "lavfi", "-i", f"anullsrc=r={SAMPLE_RATE}:cl=stereo",
        "-i", str(shot),
        "-filter_complex", ";".join(filters), "-map", "[block]",
        "-t", str(block_seconds), "-c:a", "pcm_s16le",
        "-ar", str(SAMPLE_RATE), "-ac", str(CHANNELS), str(block),
    ], f"placing {element} events")

    out = workdir / f"layer{index}_events.wav"
    if block_seconds >= float(seconds) - 0.001:
        run_ffmpeg([
            "ffmpeg", "-y", "-v", "error", "-i", str(block), "-t", str(seconds),
            "-c:a", "pcm_s16le", "-ar", str(SAMPLE_RATE), "-ac", str(CHANNELS), str(out),
        ], f"trimming {out.name}")
    else:
        _looped_with_crossfade(block, out, float(seconds), block_seconds,
                               min(4.0, block_seconds / 8), workdir, index)

    return out, dict(GENERATED_LICENCE, provider="events",
                     parameters={"element": element, "every_seconds": every,
                                 "seed": seed, "events_per_block": len(offsets),
                                 "block_seconds": round(block_seconds, 2)},
                     notes="Procedural one-shots, not sampled recordings.")


PROVIDERS = {
    "noise": provider_noise,
    "music": provider_music,
    "tone": provider_tone,
    "pad": provider_pad,
    "rain": provider_rain,
    "ambience": provider_ambience,
    "events": provider_events,
    "silence": provider_silence,
    "file": provider_file,
    "tts": provider_tts,
}


# --------------------------------------------------------------------------
# composition
# --------------------------------------------------------------------------

def validate_plan(plan):
    problems = []
    if not isinstance(plan, dict):
        raise AudioError(["audio plan must be a JSON object"])

    target = plan.get("target_seconds")
    if not isinstance(target, (int, float)) or isinstance(target, bool) or target <= 0:
        problems.append(f"'target_seconds' must be a positive number, got {target!r}")

    layers = plan.get("layers")
    if not isinstance(layers, list) or not layers:
        problems.append("'layers' must be a non-empty list")
        layers = []

    for i, layer in enumerate(layers):
        label = f"layers[{i}]"
        if not isinstance(layer, dict):
            problems.append(f"{label} must be an object")
            continue
        provider = layer.get("provider")
        if provider not in PROVIDERS:
            problems.append(f"{label}: unknown provider {provider!r}; "
                            f"available: {', '.join(sorted(PROVIDERS))}")
        for key in ("gain_db", "fade_in_seconds", "fade_out_seconds", "start_seconds",
                    "highpass_hz", "lowpass_hz", "width"):
            if key in layer and not isinstance(layer[key], (int, float)):
                problems.append(f"{label}.{key} must be a number")
        if layer.get("start_seconds", 0) < 0:
            problems.append(f"{label}.start_seconds must be >= 0")
        swell = layer.get("swell")
        if swell is not None and not isinstance(swell, dict):
            problems.append(f"{label}.swell must be an object with rate_hz/depth")

    # Ducking names another layer, so it can only be checked once every id
    # is known. A typo here would otherwise silently produce an unducked
    # mix - narration buried under its own bed, and nothing saying so.
    ids = {layer.get("id") for layer in layers if isinstance(layer, dict)}
    for i, layer in enumerate(layers):
        if not isinstance(layer, dict):
            continue
        key = layer.get("duck_under")
        if key is None:
            continue
        if key not in ids:
            problems.append(
                f"layers[{i}].duck_under={key!r} names no layer in this plan; "
                f"ids present: {sorted(x for x in ids if x)}")
        elif key == layer.get("id"):
            problems.append(f"layers[{i}].duck_under cannot reference itself")

    master = plan.get("master")
    if master is not None and not isinstance(master, dict):
        problems.append("'master' must be an object")

    if problems:
        raise AudioError(problems)
    return float(target), layers


def _quality_check(layers, provenance):
    """A cheap, honest signal for "this is one flat layer", not a block.

    compose() still returns the track either way - refusing to render audio
    over a design judgement would be the wrong kind of fail-closed - but the
    manifest says plainly when a plan is likely to read as a monotonous
    tone rather than a designed soundscape, instead of that going unnoticed
    until someone actually listens.
    """
    distinct_providers = sorted({p.get("provider") for p in provenance if p.get("provider")})
    has_fades = any(
        float(layer.get("fade_in_seconds", 0) or 0) > 0
        or float(layer.get("fade_out_seconds", 0) or 0) > 0
        for layer in layers)
    varied = len(distinct_providers) > 1 or has_fades
    warnings = []
    if not varied:
        warnings.append(
            "single static layer with no fades - likely to read as a "
            "monotonous tone rather than a designed soundscape; consider "
            "an additional layer (e.g. a 'pad') or fade automation")
    # Deliberately no "is this good/musical/pleasant" field: every signal
    # available here (layer count, provider names, fades) is structural, not
    # perceptual. A synthesized 'pad' layer being present is not evidence
    # the result is actually pleasant to listen to - only a human listening
    # can judge that, so this check never claims to.
    return {
        "layer_count": len(layers),
        "distinct_providers": distinct_providers,
        "has_variation": varied,
        "warnings": warnings,
        "advisories": [],
    }


def compose(plan, output_path, workdir=None):
    """Build the final track. Returns a machine-readable manifest."""
    target, layers = validate_plan(plan)
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    owns_workdir = workdir is None
    workdir = Path(workdir or tempfile.mkdtemp(prefix="cm-audio-"))
    workdir.mkdir(parents=True, exist_ok=True)

    try:
        rendered, provenance = [], []
        for index, layer in enumerate(layers):
            provider_name = layer["provider"]
            log.info("Layer %d/%d: %s", index + 1, len(layers), provider_name)
            path, prov = PROVIDERS[provider_name](
                layer.get("params", {}), target, workdir, index)
            prov["layer_id"] = layer.get("id", f"layer{index}")
            prov["gain_db"] = layer.get("gain_db", 0.0)
            layer = dict(layer)
            if plan.get("gain_staging"):
                trim, measured_lufs = stage_gain(path, float(
                    plan.get("layer_reference_lufs", LAYER_REFERENCE_LUFS)))
                layer["_staging_trim_db"] = trim
                prov["measured_lufs"] = measured_lufs
                prov["staging_trim_db"] = trim
                prov["mix_level_db"] = round(trim + float(layer.get("gain_db", 0) or 0), 2)
            rendered.append((path, layer))
            provenance.append(prov)

        mixed = _mix(rendered, target, workdir)
        final = _finalise(mixed, output_path, plan, target)

        actual = probe_seconds(final)
        level = mean_volume_db(final)
        if actual is None:
            raise AudioError([f"composed audio is unreadable: {final}"])
        if abs(actual - target) > 0.15:
            raise AudioError([
                f"composed audio is {actual:.3f}s but {target:.3f}s was requested"
            ])
        if level is None or level < -60:
            raise AudioError([
                f"composed audio is silent or unmeasurable (mean_volume={level})"
            ])

        attributions = [p["attribution_text"] for p in provenance
                        if p.get("attribution_required") and p.get("attribution_text")]

        settings = master_settings(plan)
        structure = _quality_check(layers, provenance)
        structure["normalized"] = settings["normalize"]
        criteria = criteria_for(plan.get("listening_context"),
                                plan.get("quality_criteria"))
        measurement = measure(final, skip_head=settings["fade_in_seconds"],
                              skip_tail=settings["fade_out_seconds"])
        findings = assess(measurement, criteria, structure)

        # Only findings with no plausible legitimate reading join the
        # blocking channel (`quality.warnings`, which scripts/project.py's
        # gate reads). Everything else is recorded as an advisory: visible
        # to a reviewer, never a silent veto on a deliberate choice.
        structure["advisories"] = [f["detail"] for f in findings
                                   if f["severity"] != "block"]
        structure["warnings"] = structure["warnings"] + [
            f["detail"] for f in findings if f["severity"] == "block"]

        manifest = {
            "output": str(final),
            "generated_utc": utc_now(),
            "target_seconds": target,
            "actual_seconds": round(actual, 3),
            "mean_volume_db": level,
            "sample_rate": SAMPLE_RATE,
            "channels": CHANNELS,
            "normalized": settings["normalize"],
            "target_lufs": settings["lufs"] if settings["normalize"] else None,
            "master": settings,
            "gain_staging": bool(plan.get("gain_staging")),
            "layers": provenance,
            "quality": structure,
            "measurement": measurement,
            "assessment": {
                "criteria": criteria,
                "findings": findings,
                "verdict": verdict(findings),
            },
            "commercial_use_cleared": all(p.get("commercial_use") for p in provenance),
            "attributions_required": attributions,
            "status": "OK",
        }
        return manifest
    finally:
        if owns_workdir:
            shutil.rmtree(workdir, ignore_errors=True)


def _mix(rendered, target, workdir):
    """Apply per-layer shaping, duck what needs ducking, then mix down.

    Per-layer shaping is the difference between layers that are merely
    present and layers that sit in a mix: band-limiting keeps a bed out of
    the voice's range, stereo width pushes ambience outward so the middle
    stays clear, and a slow swell keeps a static source alive. Ducking is
    the one genuinely relational move - a layer that declares
    ``duck_under: "<id>"`` is compressed by that layer's own signal, so a
    bed gets out of the way of narration automatically instead of being
    mixed low enough to be inaudible on its own.
    """
    inputs, filters = [], []
    labels = {}          # layer index -> current stream label
    by_id = {}           # layer id -> layer index

    for index, (path, layer) in enumerate(rendered):
        inputs += ["-i", str(path)]
        chain = []
        start = float(layer.get("start_seconds", 0) or 0)
        if start > 0:
            chain.append(f"adelay={int(start * 1000)}|{int(start * 1000)}")
        highpass = float(layer.get("highpass_hz", 0) or 0)
        if highpass > 0:
            chain.append(f"highpass=f={highpass}")
        lowpass = float(layer.get("lowpass_hz", 0) or 0)
        if lowpass > 0:
            chain.append(f"lowpass=f={lowpass}")
        swell = layer.get("swell") or {}
        if swell:
            envelope = lfo_volume(swell.get("rate_hz", 0.05),
                                  swell.get("depth", 0.2))
            if envelope:
                chain.append(envelope)
        width = float(layer.get("width", 1.0) or 1.0)
        if abs(width - 1.0) > 0.01:
            chain.append(f"extrastereo=m={width}")
        # Staging first, then the authored balance: the plan's gain is a
        # statement about this layer relative to the others, not about the
        # absolute output of whichever provider made it.
        staging = float(layer.get("_staging_trim_db", 0) or 0)
        if staging:
            chain.append(f"volume={staging}dB")
        gain = float(layer.get("gain_db", 0) or 0)
        if gain:
            chain.append(f"volume={gain}dB")
        fade_in = float(layer.get("fade_in_seconds", 0) or 0)
        if fade_in > 0:
            chain.append(f"afade=t=in:st={start}:d={fade_in}")
        fade_out = float(layer.get("fade_out_seconds", 0) or 0)
        if fade_out > 0:
            chain.append(f"afade=t=out:st={max(target - fade_out, 0)}:d={fade_out}")
        chain.append(f"aformat=sample_rates={SAMPLE_RATE}:channel_layouts=stereo")
        label = f"m{index}"
        filters.append(f"[{index}:a]{','.join(chain)}[{label}]")
        labels[index] = label
        if layer.get("id"):
            by_id[layer["id"]] = index

    # Ducking pass: each key layer is split once, into its own place in the
    # mix plus one copy per layer that ducks under it.
    duckers = {}
    for index, (_, layer) in enumerate(rendered):
        key = layer.get("duck_under")
        if key and key in by_id and by_id[key] != index:
            duckers.setdefault(by_id[key], []).append((index, layer))
    for key_index, targets in duckers.items():
        key_label = labels[key_index]
        outs = [f"sc{key_index}_{n}" for n in range(len(targets))]
        filters.append(
            f"[{key_label}]asplit={len(outs) + 1}[{key_label}k]"
            + "".join(f"[{o}]" for o in outs))
        labels[key_index] = f"{key_label}k"
        for (target_index, layer), sidechain in zip(targets, outs):
            params = layer.get("duck") or {}
            threshold = float(params.get("threshold", 0.035))
            ratio = float(params.get("ratio", 6.0))
            attack = float(params.get("attack_ms", 25.0))
            release = float(params.get("release_ms", 900.0))
            ducked = f"{labels[target_index]}d"
            filters.append(
                f"[{labels[target_index]}][{sidechain}]sidechaincompress="
                f"threshold={threshold}:ratio={ratio}:attack={attack}:"
                f"release={release}[{ducked}]")
            labels[target_index] = ducked

    ordered = [f"[{labels[i]}]" for i in range(len(rendered))]
    if len(ordered) == 1:
        filters.append(f"{ordered[0]}anull[mix]")
    else:
        # normalize=0 keeps each layer at its authored gain instead of
        # silently attenuating everything by the layer count.
        filters.append(f"{''.join(ordered)}amix=inputs={len(ordered)}:duration=longest:normalize=0[mix]")

    mixed = workdir / "mixed.wav"
    run_ffmpeg([
        "ffmpeg", "-y", "-v", "error", *inputs,
        "-filter_complex", ";".join(filters), "-map", "[mix]",
        "-t", str(target), "-c:a", "pcm_s16le",
        "-ar", str(SAMPLE_RATE), "-ac", str(CHANNELS), str(mixed),
    ], "mixing layers")
    return mixed


def master_settings(plan):
    """The master-bus settings this plan asks for, with defaults filled in.

    ``normalize``/``target_lufs`` are the original spelling and still work
    unchanged; ``master`` is the richer one, and where both are present the
    explicit ``master`` block wins because it is the more specific
    statement of intent.
    """
    master = dict(plan.get("master") or {})
    normalize = bool(master.get("normalize", plan.get("normalize", True)))
    lufs = master.get("lufs", plan.get("target_lufs", DEFAULT_LUFS))
    return {
        "normalize": normalize,
        "lufs": float(lufs) if lufs is not None else DEFAULT_LUFS,
        "true_peak_dbfs": float(master.get("true_peak_dbfs", -1.5)),
        "loudness_range_lu": float(master.get("loudness_range_lu", 11.0)),
        "highpass_hz": float(master.get("highpass_hz", 0) or 0),
        "lowpass_hz": float(master.get("lowpass_hz", 0) or 0),
        "limiter": bool(master.get("limiter", True)),
        "fade_in_seconds": float(master.get("fade_in_seconds",
                                            plan.get("fade_in_seconds", 0)) or 0),
        "fade_out_seconds": float(master.get("fade_out_seconds",
                                             plan.get("fade_out_seconds", 0)) or 0),
    }


def _finalise(mixed, output_path, plan, target):
    """Master bus: corrective EQ, loudness, a safety limiter, master fades.

    Order matters and is the conventional one: shape before you measure
    level, set level, then catch anything the level move pushed at the
    ceiling. The limiter is last and is a safety net, not a loudness tool -
    it is set at the same true-peak ceiling loudnorm was asked for, so on a
    well-behaved track it does nothing at all.
    """
    settings = master_settings(plan)
    chain = []
    if settings["highpass_hz"] > 0:
        chain.append(f"highpass=f={settings['highpass_hz']}")
    if settings["lowpass_hz"] > 0:
        chain.append(f"lowpass=f={settings['lowpass_hz']}")
    if settings["normalize"]:
        chain.append(
            f"loudnorm=I={settings['lufs']}:TP={settings['true_peak_dbfs']}"
            f":LRA={settings['loudness_range_lu']}")
    if settings["fade_in_seconds"] > 0:
        chain.append(f"afade=t=in:st=0:d={settings['fade_in_seconds']}")
    if settings["fade_out_seconds"] > 0:
        chain.append(
            f"afade=t=out:st={max(target - settings['fade_out_seconds'], 0)}"
            f":d={settings['fade_out_seconds']}")
    if settings["limiter"]:
        ceiling = 10 ** (settings["true_peak_dbfs"] / 20.0)
        chain.append(f"alimiter=limit={ceiling:.6f}:level=disabled")
    chain.append(f"aformat=sample_rates={SAMPLE_RATE}:channel_layouts=stereo")

    run_ffmpeg([
        "ffmpeg", "-y", "-v", "error", "-i", str(mixed),
        "-af", ",".join(chain), "-t", str(target),
        "-c:a", "pcm_s16le", "-ar", str(SAMPLE_RATE), "-ac", str(CHANNELS),
        str(output_path),
    ], "finalising track")
    return output_path


# --------------------------------------------------------------------------
# measurement - what is actually in the file, not what the plan asked for
# --------------------------------------------------------------------------

# Long-form ambience is the format this project exists for, and scanning
# four hours of it to learn its loudness is four hours of I/O for a number
# a ten-minute excerpt gives to a tenth of a dB. Past this length the track
# is measured over a centred excerpt, and the manifest says so.
MEASURE_WINDOW_SECONDS = 600.0

# Band split used for spectral balance. Low is where a sleep bed should
# live, high is where listening fatigue comes from.
BAND_LOW_HZ = 250.0
BAND_HIGH_HZ = 4000.0

# EBU R128 integrates over 400 ms blocks gated against the programme; under
# a few seconds the "integrated" figure describes the gate, not the track.
MIN_INTEGRATION_SECONDS = 4.0

# Where every layer is parked before the authored mix balance is applied.
# Without this step a layer's `gain_db` is relative to whatever level its
# provider happens to synthesise at - the `music` provider and the
# `ambience` provider differ by more than 10 dB - so "-22 dB under the bed"
# means something different for each one, and a designed balance is not a
# balance at all. Normalising each layer to one reference first is what
# makes the numbers in a sound design mean what they say.
LAYER_REFERENCE_LUFS = -20.0
# A layer this quiet is silence or near it; trimming it up would only
# amplify a noise floor.
MIN_STAGEABLE_LUFS = -55.0
# No layer is moved further than this. A trim past it means the layer is
# not what the plan thought it was, and quietly applying 30 dB of makeup
# gain would hide that rather than show it.
MAX_STAGING_TRIM_DB = 18.0


def _ffmpeg_stderr(cmd):
    return subprocess.run(cmd, capture_output=True, text=True).stderr


def _parse_loudness(stderr):
    """Pull ebur128's summary block and astats' time-domain numbers out."""
    out = {}
    lines = [l.strip() for l in stderr.splitlines()]
    for i, line in enumerate(lines):
        for key, label in (("integrated_lufs", "I:"), ("loudness_range_lu", "LRA:"),
                           ("true_peak_dbfs", "Peak:")):
            if line.startswith(label) and key not in out:
                # ebur128 prints per-frame lines too; the summary block is
                # the only place these appear alone on a line.
                try:
                    out[key] = float(line.split()[1])
                except (IndexError, ValueError):
                    pass
        for key, label in (("peak_dbfs", "Peak level dB:"), ("rms_dbfs", "RMS level dB:"),
                           ("dc_offset", "DC offset:"), ("flat_factor", "Flat factor:"),
                           ("noise_floor_dbfs", "Noise floor dB:")):
            marker = f"] {label}"
            if marker in line:
                try:
                    out[key] = float(line.split(marker)[1].strip().split()[0])
                except (IndexError, ValueError):
                    pass
    return out


def measure(path, window_seconds=MEASURE_WINDOW_SECONDS, skip_head=0.0,
            skip_tail=0.0):
    """Measured facts about a finished track.

    Deliberately measurement, not judgement: every number here is something
    ffmpeg read out of the file. What any of it *means* for whether the
    track is worth publishing is ``assess``'s question, and whether it is
    pleasant to listen to is a human's (see ``provenance.audio`` in
    scripts/project.py). Returns ``{}`` when the file cannot be read at all.

    ``skip_head``/``skip_tail`` exclude the master fades. A fade is not
    programme material, and including it makes loudness range describe the
    fade rather than the music - on a short preview with long fades that is
    most of what the number would be measuring.
    """
    path = Path(path)
    duration = probe_seconds(path)
    if duration is None:
        return {}

    head = max(float(skip_head or 0.0), 0.0)
    tail = max(float(skip_tail or 0.0), 0.0)
    programme = duration - head - tail
    # Never analyse less than half the file to avoid a fade: a plan whose
    # fades really are most of the track is better measured whole, and the
    # result says which was done.
    if programme < duration / 2:
        head = tail = 0.0
        programme = duration

    window, start = programme, head
    if window_seconds and programme > window_seconds:
        window = float(window_seconds)
        start = head + (programme - window) / 2.0

    span = (["-ss", f"{start:.3f}", "-t", f"{window:.3f}"]
            if window < duration else [])
    stderr = _ffmpeg_stderr([
        "ffmpeg", "-hide_banner", "-nostats", *span, "-i", str(path),
        "-af", "ebur128=peak=true,astats=metadata=0", "-f", "null", "-"])
    result = _parse_loudness(stderr)

    band_stderr = _ffmpeg_stderr([
        "ffmpeg", "-hide_banner", "-nostats", *span, "-i", str(path),
        "-filter_complex",
        f"[0:a]asplit=3[a][b][c];"
        f"[a]lowpass=f={BAND_LOW_HZ},volumedetect[lo];"
        f"[b]highpass=f={BAND_LOW_HZ},lowpass=f={BAND_HIGH_HZ},volumedetect[mid];"
        f"[c]highpass=f={BAND_HIGH_HZ},volumedetect[hi]",
        "-map", "[lo]", "-f", "null", "-",
        "-map", "[mid]", "-f", "null", "-",
        "-map", "[hi]", "-f", "null", "-"])
    bands = []
    for line in band_stderr.splitlines():
        if "volumedetect" in line and "mean_volume:" in line:
            try:
                index = int(line.split("volumedetect_")[1].split(" ")[0])
                bands.append((index, float(line.split("mean_volume:")[1].strip().split()[0])))
            except (IndexError, ValueError):
                pass
    bands.sort()
    if len(bands) == 3:
        result["band_low_db"], result["band_mid_db"], result["band_high_db"] = (
            bands[0][1], bands[1][1], bands[2][1])
        result["brightness_db"] = round(bands[2][1] - bands[1][1], 2)

    result["duration_seconds"] = round(duration, 3)
    result["measured_seconds"] = round(window, 3)
    result["measured_from_seconds"] = round(start, 3)
    result["measured_window"] = (
        "full" if window >= duration
        else ("programme between the fades" if (head or tail) and window >= programme
              else "centred excerpt"))
    # ffmpeg reports an empty noise floor as -inf. json.dumps would happily
    # write `-Infinity`, which is not JSON and breaks every consumer that is
    # not Python - the web API and the browser both parse this.
    return {k: (None if isinstance(v, float) and (v != v or v in (float("inf"), float("-inf"))) else v)
            for k, v in result.items()}


def stage_gain(path, reference_lufs=LAYER_REFERENCE_LUFS):
    """The trim in dB that puts one rendered layer at the reference level.

    Returns ``(trim_db, measured_lufs)``; ``(0.0, None)`` when the layer is
    silent or unmeasurable, because there is no meaningful level to move.
    """
    measured = measure(path, window_seconds=120.0)
    lufs = measured.get("integrated_lufs")
    if lufs is None or lufs < MIN_STAGEABLE_LUFS:
        return 0.0, lufs
    trim = reference_lufs - lufs
    return round(max(-MAX_STAGING_TRIM_DB, min(MAX_STAGING_TRIM_DB, trim)), 2), lufs


# --------------------------------------------------------------------------
# quality criteria - what "good enough to ship unattended" means here
# --------------------------------------------------------------------------

# Named listening contexts, not niches. "A four-hour bed somebody falls
# asleep to" and "a narrated explainer" want genuinely different loudness,
# dynamics and brightness, and that distinction is about how the audio is
# listened to - which is why meditation/sleep is the first user of
# `background_sleep` rather than the thing it is named after.
LISTENING_CONTEXTS = {
    "background_sleep": {
        "integrated_lufs_target": -23.0, "integrated_lufs_tolerance": 2.0,
        "true_peak_ceiling_dbfs": -1.0, "max_brightness_db": -3.0,
        "max_loudness_range_lu": 8.0, "require_variation": True,
    },
    "background_focus": {
        "integrated_lufs_target": -20.0, "integrated_lufs_tolerance": 2.0,
        "true_peak_ceiling_dbfs": -1.0, "max_brightness_db": 0.0,
        "max_loudness_range_lu": 10.0, "require_variation": True,
    },
    "ambient_watch": {
        "integrated_lufs_target": -18.0, "integrated_lufs_tolerance": 2.0,
        "true_peak_ceiling_dbfs": -1.0, "max_brightness_db": 3.0,
        "max_loudness_range_lu": 12.0, "require_variation": True,
    },
    "narration": {
        "integrated_lufs_target": -16.0, "integrated_lufs_tolerance": 2.0,
        "true_peak_ceiling_dbfs": -1.0, "max_brightness_db": 6.0,
        "max_loudness_range_lu": 14.0, "require_variation": False,
    },
}
DEFAULT_LISTENING_CONTEXT = "ambient_watch"


def criteria_for(context=None, overrides=None):
    """The quality criteria for a listening context, with any overrides."""
    base = dict(LISTENING_CONTEXTS.get(
        context or DEFAULT_LISTENING_CONTEXT,
        LISTENING_CONTEXTS[DEFAULT_LISTENING_CONTEXT]))
    base["listening_context"] = context or DEFAULT_LISTENING_CONTEXT
    if overrides:
        base.update({k: v for k, v in overrides.items() if v is not None})
    return base


def _finding(code, severity, detail):
    return {"code": code, "severity": severity, "detail": detail}


def assess(measurement, criteria=None, structure=None):
    """Turn measurements into findings an unattended run can act on.

    Three severities, and the distinction is the whole point:

    - ``block`` - the artefact is objectively broken (clipped, silent,
      badly off its loudness target). No taste is involved, so it holds
      review by itself.
    - ``warn`` - a real defect a person should look at, but with a
      plausible legitimate reading, so it is surfaced, not fatal.
    - ``info`` - context worth recording.

    Nothing here ever concludes the audio is *good*. Passing every check
    means "nothing measurable is wrong with it", which is a different and
    much smaller claim, and the review gate keeps treating it that way.
    """
    criteria = criteria or criteria_for()
    findings = []
    if not measurement:
        return [_finding("unmeasurable", "block",
                         "the composed track could not be measured at all")]

    structure = structure or {}
    # Two cases where a loudness *target* is not a fair question, though
    # clipping and silence still are: a plan that explicitly opted out of
    # normalisation (it asked to be left alone), and a track too short for
    # EBU R128 to integrate over at all.
    too_short = float(measurement.get("measured_seconds") or 0) < MIN_INTEGRATION_SECONDS
    loudness_managed = structure.get("normalized", True) and not too_short

    lufs = measurement.get("integrated_lufs")
    target = criteria["integrated_lufs_target"]
    tolerance = criteria["integrated_lufs_tolerance"]
    if lufs is None:
        findings.append(_finding("loudness_unknown", "warn",
                                 "integrated loudness could not be measured"))
    elif lufs < -60:
        findings.append(_finding("silent", "block",
                                 f"the track is effectively silent ({lufs:.1f} LUFS)"))
    elif not loudness_managed:
        findings.append(_finding(
            "loudness_unmanaged", "info",
            f"integrated loudness {lufs:.1f} LUFS, not checked against the "
            f"{target:.1f} LUFS target: "
            + ("this plan opted out of normalisation"
               if not structure.get("normalized", True)
               else f"the track is under {MIN_INTEGRATION_SECONDS:.0f}s")))
    elif abs(lufs - target) > tolerance * 2:
        findings.append(_finding(
            "loudness_far_off", "block",
            f"integrated loudness {lufs:.1f} LUFS is more than "
            f"{tolerance * 2:.1f} LU from the {target:.1f} LUFS target for "
            f"{criteria['listening_context']} listening"))
    elif abs(lufs - target) > tolerance:
        findings.append(_finding(
            "loudness_off", "warn",
            f"integrated loudness {lufs:.1f} LUFS vs a {target:.1f} LUFS target"))

    peak = measurement.get("true_peak_dbfs")
    ceiling = criteria["true_peak_ceiling_dbfs"]
    if peak is not None and peak > 0.0:
        findings.append(_finding("clipping", "block",
                                 f"true peak {peak:.1f} dBFS is above 0 - the track clips"))
    elif peak is not None and peak > ceiling:
        findings.append(_finding(
            "headroom_low", "warn",
            f"true peak {peak:.1f} dBFS is above the {ceiling:.1f} dBFS ceiling"))

    flat = measurement.get("flat_factor")
    sample_peak = measurement.get("peak_dbfs")
    if flat is not None and flat > 0.5:
        # Runs of identical samples mean two very different things. Near
        # full scale they are flat-topped waveforms, i.e. clipping. Well
        # below it they are quantisation in a quiet passage - a fade tail,
        # a held silence - which is normal and not worth a warning that
        # says "clipping" to whoever reads it.
        if sample_peak is None or sample_peak > -1.0:
            findings.append(_finding(
                "flat_samples", "warn",
                f"flat factor {flat:.2f} at a sample peak of "
                f"{sample_peak if sample_peak is None else round(sample_peak, 1)} "
                "dBFS: flat-topped waveforms, which is clipping"))
        else:
            findings.append(_finding(
                "quantisation_floor", "info",
                f"flat factor {flat:.2f} well below full scale "
                f"({sample_peak:.1f} dBFS) - quantisation in the quiet "
                "passages, not clipping"))

    dc = measurement.get("dc_offset")
    if dc is not None and abs(dc) > 0.02:
        findings.append(_finding(
            "dc_offset", "warn",
            f"DC offset {dc:.3f} wastes headroom and can thump on playback"))

    brightness = measurement.get("brightness_db")
    if brightness is not None and brightness > criteria["max_brightness_db"]:
        findings.append(_finding(
            "too_bright", "warn",
            f"high band sits {brightness:.1f} dB above the midrange, past the "
            f"{criteria['max_brightness_db']:.1f} dB comfortable for "
            f"{criteria['listening_context']} listening - likely fatiguing"))

    lra = measurement.get("loudness_range_lu")
    if lra is not None and lra > criteria["max_loudness_range_lu"]:
        findings.append(_finding(
            "dynamics_wide", "warn",
            f"loudness range {lra:.1f} LU is wider than the "
            f"{criteria['max_loudness_range_lu']:.1f} LU this listening "
            "context wants - quiet parts will vanish and loud parts intrude"))

    if criteria.get("require_variation") and structure.get("has_variation") is False:
        findings.append(_finding(
            "static_single_layer", "warn",
            "single static layer with no fades - likely to read as a "
            "monotonous tone rather than a designed soundscape"))

    return findings


def verdict(findings):
    """``BLOCKED`` / ``REVIEW`` / ``OK`` for a set of findings."""
    severities = {f["severity"] for f in findings}
    if "block" in severities:
        return "BLOCKED"
    if "warn" in severities:
        return "REVIEW"
    return "OK"


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

def cmd_providers(args):
    print("\nAudio providers:\n")
    descriptions = {
        "noise": "Coloured noise (white/pink/brown/blue/violet/velvet). Rights-clean.",
        "tone": "Sine drone with optional lowpass. Rights-clean.",
        "pad": "Slowly evolving multi-oscillator harmonic pad (music layer). Rights-clean.",
        "rain": "Procedural rain-like bed from shaped noise. Rights-clean.",
        "silence": "Digital silence, for gaps and spacing.",
        "file": "Local audio asset. REQUIRES explicit licence fields.",
        "tts": "Local Piper narration. Offline, free, deterministic.",
    }
    for name in sorted(PROVIDERS):
        print(f"  {name:<10} {descriptions[name]}")
    print(f"\nOutput format: {SAMPLE_RATE} Hz, {CHANNELS}ch PCM, "
          f"normalised to {DEFAULT_LUFS} LUFS by default.\n")
    return 0


def cmd_voices(args):
    print("\nApproved voices (licence verified from each voice's MODEL_CARD):\n")
    for name, voice in VOICES.items():
        installed = (VOICES_DIR / voice["model"]).is_file()
        print(f"  {name}")
        print(f"    licence:     {voice['license']}  "
              f"(commercial: {voice['commercial_use']}, "
              f"attribution: {voice['attribution_required']})")
        print(f"    dataset:     {voice['dataset']}")
        print(f"    installed:   {'yes' if installed else 'NO - see README'}")
        print(f"    evidence:    {voice['evidence']}")
    print("\nRejected voices:\n")
    for name, reason in REJECTED_VOICES.items():
        print(f"  {name}\n    {reason}")
    print()
    return 0


def cmd_compose(args):
    plan_path = Path(args.plan)
    if not plan_path.is_file():
        log.error("Plan file not found: %s", plan_path)
        return 1
    try:
        plan = json.loads(plan_path.read_text())
    except json.JSONDecodeError as e:
        log.error("Plan file is not valid JSON: %s", e)
        return 1

    try:
        manifest = compose(plan, args.output)
    except AudioError as e:
        log.error("Audio composition failed with %d problem(s):", len(e.problems))
        for p in e.problems:
            log.error("  - %s", p)
        return 1

    log.info("Wrote %s (%.3fs, %s LUFS target, mean %.1f dB)",
             manifest["output"], manifest["actual_seconds"],
             manifest["target_lufs"], manifest["mean_volume_db"])
    if not manifest["commercial_use_cleared"]:
        log.warning("NOT cleared for commercial use - check layer licences.")
    for attribution in manifest["attributions_required"]:
        log.info("Attribution required: %s", attribution)
    for warning in manifest["quality"]["warnings"]:
        log.warning("Quality: %s", warning)
    if args.manifest:
        Path(args.manifest).write_text(json.dumps(manifest, indent=2) + "\n")
        log.info("Manifest: %s", args.manifest)
    return 0


def main():
    parser = argparse.ArgumentParser(description="Content Machine audio subsystem.")
    sub = parser.add_subparsers(dest="command", required=True)

    p_prov = sub.add_parser("providers", help="list available audio providers")
    p_prov.set_defaults(func=cmd_providers)

    p_voice = sub.add_parser("voices", help="list approved/rejected narration voices")
    p_voice.set_defaults(func=cmd_voices)

    p_comp = sub.add_parser("compose", help="build a track from an audio plan")
    p_comp.add_argument("--plan", required=True)
    p_comp.add_argument("--output", required=True)
    p_comp.add_argument("--manifest", default=None)
    p_comp.set_defaults(func=cmd_compose)

    args = parser.parse_args()
    for tool in ("ffmpeg", "ffprobe"):
        if not shutil.which(tool):
            log.error("%s must be installed and on PATH.", tool)
            return 1
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
