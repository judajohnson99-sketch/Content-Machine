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
    voice_name = params.get("voice", "en_US-libritts_r-medium")
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


PROVIDERS = {
    "noise": provider_noise,
    "tone": provider_tone,
    "rain": provider_rain,
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
        for key in ("gain_db", "fade_in_seconds", "fade_out_seconds", "start_seconds"):
            if key in layer and not isinstance(layer[key], (int, float)):
                problems.append(f"{label}.{key} must be a number")
        if layer.get("start_seconds", 0) < 0:
            problems.append(f"{label}.start_seconds must be >= 0")

    if problems:
        raise AudioError(problems)
    return float(target), layers


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
        manifest = {
            "output": str(final),
            "generated_utc": utc_now(),
            "target_seconds": target,
            "actual_seconds": round(actual, 3),
            "mean_volume_db": level,
            "sample_rate": SAMPLE_RATE,
            "channels": CHANNELS,
            "normalized": bool(plan.get("normalize", True)),
            "target_lufs": plan.get("target_lufs", DEFAULT_LUFS) if plan.get("normalize", True) else None,
            "layers": provenance,
            "commercial_use_cleared": all(p.get("commercial_use") for p in provenance),
            "attributions_required": attributions,
            "status": "OK",
        }
        return manifest
    finally:
        if owns_workdir:
            shutil.rmtree(workdir, ignore_errors=True)


def _mix(rendered, target, workdir):
    """Apply per-layer gain/fades/offset, then mix down to one stream."""
    mixed = workdir / "mixed.wav"
    inputs, filters, labels = [], [], []

    for index, (path, layer) in enumerate(rendered):
        inputs += ["-i", str(path)]
        chain = []
        start = float(layer.get("start_seconds", 0) or 0)
        if start > 0:
            chain.append(f"adelay={int(start * 1000)}|{int(start * 1000)}")
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
        labels.append(f"[{label}]")

    if len(labels) == 1:
        filters.append(f"{labels[0]}anull[mix]")
    else:
        # normalize=0 keeps each layer at its authored gain instead of
        # silently attenuating everything by the layer count.
        filters.append(f"{''.join(labels)}amix=inputs={len(labels)}:duration=longest:normalize=0[mix]")

    run_ffmpeg([
        "ffmpeg", "-y", "-v", "error", *inputs,
        "-filter_complex", ";".join(filters), "-map", "[mix]",
        "-t", str(target), "-c:a", "pcm_s16le",
        "-ar", str(SAMPLE_RATE), "-ac", str(CHANNELS), str(mixed),
    ], "mixing layers")
    return mixed


def _finalise(mixed, output_path, plan, target):
    """Optional loudness normalisation, master fades, exact-length output."""
    chain = []
    if plan.get("normalize", True):
        lufs = float(plan.get("target_lufs", DEFAULT_LUFS))
        chain.append(f"loudnorm=I={lufs}:TP=-1.5:LRA=11")
    fade_in = float(plan.get("fade_in_seconds", 0) or 0)
    if fade_in > 0:
        chain.append(f"afade=t=in:st=0:d={fade_in}")
    fade_out = float(plan.get("fade_out_seconds", 0) or 0)
    if fade_out > 0:
        chain.append(f"afade=t=out:st={max(target - fade_out, 0)}:d={fade_out}")
    chain.append(f"aformat=sample_rates={SAMPLE_RATE}:channel_layouts=stereo")

    run_ffmpeg([
        "ffmpeg", "-y", "-v", "error", "-i", str(mixed),
        "-af", ",".join(chain), "-t", str(target),
        "-c:a", "pcm_s16le", "-ar", str(SAMPLE_RATE), "-ac", str(CHANNELS),
        str(output_path),
    ], "finalising track")
    return output_path


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

def cmd_providers(args):
    print("\nAudio providers:\n")
    descriptions = {
        "noise": "Coloured noise (white/pink/brown/blue/violet/velvet). Rights-clean.",
        "tone": "Sine drone with optional lowpass. Rights-clean.",
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
