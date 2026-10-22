"""Optional local CLIP analysis. No paid calls and no effect on rights/grades.

Only analysis uses numpy/ONNX/tokenizers; rendering remains stdlib + ffmpeg.
Models are installed explicitly, never downloaded by scanning or searching.
"""
from functools import lru_cache
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import urllib.request

try:
    from . import media
except ImportError:
    import media

MODEL = "Xenova/clip-vit-base-patch32"
REVISION = "d15189d"
MODEL_ID = f"{MODEL}@{REVISION}"
FILES = ("tokenizer.json", "onnx/text_model_quantized.onnx", "onnx/vision_model_quantized.onnx")
AUDIO_MODEL = "Xenova/clap-htsat-unfused"
AUDIO_REVISION = "9dcb1c1"
AUDIO_MODEL_ID = f"{AUDIO_MODEL}@{AUDIO_REVISION}"
AUDIO_FILES = ("tokenizer.json", "preprocessor_config.json", "onnx/text_model_quantized.onnx", "onnx/audio_model_quantized.onnx")


def model_dir():
    return Path(os.environ.get("MEDIA_MODEL_DIR", str(media.ROOT / "library/models/clip")))


def available():
    return all((model_dir() / name).is_file() for name in FILES) and all(
        importlib.util.find_spec(name) for name in ("numpy", "onnxruntime", "tokenizers"))


def install(audio=False):
    """Explicit free model download pinned to a repository revision."""
    for name in AUDIO_FILES if audio else FILES:
        destination = (model_dir().parent / "clap" if audio else model_dir()) / name
        if destination.is_file():
            continue
        destination.parent.mkdir(parents=True, exist_ok=True)
        pending = destination.with_suffix(".pending")
        urllib.request.urlretrieve(f"https://huggingface.co/{AUDIO_MODEL if audio else MODEL}/resolve/{AUDIO_REVISION if audio else REVISION}/{name}", pending)
        pending.replace(destination)
    return {"model": AUDIO_MODEL_ID if audio else MODEL_ID, "available": audio_available() if audio else available()}


def audio_available():
    return all((model_dir().parent / "clap" / name).is_file() for name in AUDIO_FILES) and all(
        importlib.util.find_spec(name) for name in ("numpy", "onnxruntime", "tokenizers", "transformers"))


@lru_cache(maxsize=1)
def _audio_models():
    if not audio_available():
        raise media.MediaError("Local audio analysis is not installed on this computer")
    import onnxruntime as ort
    from tokenizers import Tokenizer
    options = ort.SessionOptions()
    options.intra_op_num_threads = 2
    options.inter_op_num_threads = 1
    directory = model_dir().parent / "clap"
    models = [ort.InferenceSession(str(directory / file), sess_options=options, providers=["CPUExecutionProvider"]) for file in AUDIO_FILES[2:]]
    tokenizer = Tokenizer.from_file(str(directory / "tokenizer.json"))
    tokenizer.enable_truncation(max_length=77)
    return *models, tokenizer, _clap_features


def _clap_features(waveform, **_):
    """Pinned unfused CLAP uses Slaney log-mels, repeat-pad and 10s clips.

    Use Transformers' NumPy DSP primitives without loading a PyTorch model.
    Sampling intervals are deterministic instead of random crops.
    """
    import numpy as np
    from transformers.audio_utils import mel_filter_bank, spectrogram, window_function
    config = json.loads((model_dir().parent / "clap/preprocessor_config.json").read_text())
    length = config["nb_max_samples"]
    waveform = waveform[:length]
    if len(waveform) < length:
        waveform = np.tile(waveform, length // len(waveform))
        waveform = np.pad(waveform, (0, length - len(waveform)))
    filters = mel_filter_bank(num_frequency_bins=config["nb_frequency_bins"],
        num_mel_filters=config["feature_size"], min_frequency=config["frequency_min"],
        max_frequency=config["frequency_max"], sampling_rate=config["sampling_rate"], norm="slaney", mel_scale="slaney")
    mel = spectrogram(waveform, window_function(config["fft_window_size"], "hann"),
        frame_length=config["fft_window_size"], hop_length=config["hop_length"], power=2.0,
        mel_filters=filters, log_mel="dB").T
    return {"input_features": mel[None, None, ...].astype(np.float32), "is_longer": np.array([[False]])}


@lru_cache(maxsize=128)
def audio_text_vector(query):
    import numpy as np
    model, _, tokenizer, _ = _audio_models()
    encoded = tokenizer.encode(query)
    values = {"input_ids": np.array([encoded.ids], dtype=np.int64), "attention_mask": np.array([encoded.attention_mask], dtype=np.int64)}
    return _unit(model.run(None, {item.name: values[item.name] for item in model.get_inputs()})[0][0]).tolist()


def _analyze_audio(asset, path):
    import numpy as np
    _, model, _, extractor = _audio_models()
    duration = float(asset["technical"].get("duration_seconds") or 0)
    times = [0] if duration <= 10 else [max(0, min(duration - 10, duration * p)) for p in (0.1, 0.5, 0.9)]
    vectors = []
    for seconds in times:
        result = subprocess.run(["ffmpeg", "-v", "error", "-nostdin", "-ss", str(seconds), "-i", str(path),
                                 "-t", "10", "-ac", "1", "-ar", "48000", "-f", "f32le", "pipe:1"], capture_output=True, timeout=30)
        if result.returncode or not result.stdout:
            raise media.MediaError("Could not inspect the audio; check that it is readable")
        values = extractor(np.frombuffer(result.stdout, dtype="<f4"), sampling_rate=48000, return_tensors="np")
        vectors.append(_unit(model.run(None, {item.name: values[item.name] for item in model.get_inputs()})[0][0]))
    return {"model": AUDIO_MODEL_ID, "source_sha256": asset["id"], "confidence": "INFERRED",
            "frames_sampled": times, "embedding": _unit(np.mean(vectors, axis=0)).tolist(),
            "note": "Sound similarity is inferred from audio samples; listen before use."}


@lru_cache(maxsize=1)
def _models():
    if not available():
        raise media.MediaError("Local visual analysis is not installed on this computer")
    import onnxruntime as ort
    from tokenizers import Tokenizer
    options = ort.SessionOptions()
    options.intra_op_num_threads = 2
    options.inter_op_num_threads = 1
    text = ort.InferenceSession(str(model_dir() / FILES[1]), sess_options=options, providers=["CPUExecutionProvider"])
    vision = ort.InferenceSession(str(model_dir() / FILES[2]), sess_options=options, providers=["CPUExecutionProvider"])
    tokenizer = Tokenizer.from_file(str(model_dir() / FILES[0]))
    tokenizer.enable_truncation(max_length=77)
    return text, vision, tokenizer


def _unit(vector):
    import numpy as np
    return vector / max(float(np.linalg.norm(vector)), 1e-12)


@lru_cache(maxsize=128)
def text_vector(query):
    import numpy as np
    model, _, tokenizer = _models()
    encoded = tokenizer.encode(query)
    values = {"input_ids": np.array([encoded.ids], dtype=np.int64),
              "attention_mask": np.array([encoded.attention_mask], dtype=np.int64)}
    result = model.run(None, {item.name: values[item.name] for item in model.get_inputs()})
    return _unit(result[0][0]).tolist()


def analyze_record(asset, path):
    """Inspect pixels, sample video over time; store inferred evidence separately."""
    if asset["technical"]["kind"] == "audio":
        return _analyze_audio(asset, path)
    import numpy as np
    _, vision, _ = _models()
    duration = float(asset["technical"].get("duration_seconds") or 0)
    times = [0] if asset["technical"]["kind"] == "image" else [duration * p for p in (0.1, 0.5, 0.9)]
    vectors = []
    for seconds in times:
        command = ["ffmpeg", "-v", "error", "-nostdin", "-ss", str(seconds), "-i", str(path),
                   "-frames:v", "1", "-vf", "scale=224:224:force_original_aspect_ratio=increase:flags=bicubic,crop=224:224",
                   "-f", "rawvideo", "-pix_fmt", "rgb24", "pipe:1"]
        result = subprocess.run(command, capture_output=True, timeout=30)
        if result.returncode or len(result.stdout) != 224 * 224 * 3:
            raise media.MediaError("Could not inspect a frame; check that the media is readable")
        pixels = np.frombuffer(result.stdout, dtype=np.uint8).reshape(224, 224, 3).astype(np.float32) / 255
        pixels = (pixels - np.array([0.48145466, 0.4578275, 0.40821073], dtype=np.float32)) / np.array([0.26862954, 0.26130258, 0.27577711], dtype=np.float32)
        values = pixels.transpose(2, 0, 1)[None, ...]
        vectors.append(_unit(vision.run(None, {vision.get_inputs()[0].name: values})[0][0]))
    vector = _unit(np.mean(vectors, axis=0))
    return {"model": MODEL_ID, "source_sha256": asset["id"], "confidence": "INFERRED",
            "frames_sampled": times, "embedding": vector.tolist(),
            "note": "Visual similarity is inferred from pixels; inspect suitability before use."}


def analyze(identity, catalog=media.DEFAULT_CATALOG):
    asset = media.load(catalog)["assets"].get(identity)
    if not asset:
        raise media.MediaError("Unknown library asset")
    path = media.resolve(asset)
    result = analyze_record(asset, path)
    if media.digest(path) != identity:
        raise media.MediaError("Media changed during analysis; rescan it first")
    with media.catalog_lock(catalog):
        data = media.load(catalog)
        data["assets"][identity]["analysis"] = result
        data["assets"][identity].pop("analysis_error", None)
        media.atomic_json(catalog, data)
    return {"id": identity, "model": result["model"], "confidence": "INFERRED", "frames": len(result["frames_sampled"])}


def rank(assets, query):
    """Hybrid retrieval; never include filenames as content evidence."""
    import math
    words = query.casefold().split()
    vector = None
    try:
        if available() and any(a.get("analysis") for a in assets):
            vector = text_vector(query)
    except Exception:
        pass  # Optional inference must never take ordinary search offline.
    audio_vector = None
    try:
        if audio_available() and any((a.get("analysis") or {}).get("model") == AUDIO_MODEL_ID for a in assets if isinstance(a.get("analysis"), dict)):
            audio_vector = audio_text_vector(query)
    except Exception:
        pass
    ranked = []
    for asset in assets:
        text = " ".join([asset.get("description", ""), asset.get("source", ""), *asset.get("tags", []),
                         str((asset.get("provenance") or {}).get("prompt", ""))]).casefold()
        literal = sum(word in text for word in words) / max(1, len(words))
        analysis = asset.get("analysis") if isinstance(asset.get("analysis"), dict) else {}
        embedding = analysis.get("embedding")
        similarity = 0
        target = audio_vector if analysis.get("model") == AUDIO_MODEL_ID else vector
        if (target and analysis.get("model") in {MODEL_ID, AUDIO_MODEL_ID} and analysis.get("source_sha256") == asset["id"]
                and isinstance(embedding, list) and len(embedding) == len(target)
                and all(isinstance(v, (int, float)) and math.isfinite(v) for v in embedding)):
            similarity = sum(a * b for a, b in zip(target, embedding))
        if literal == 1 or similarity >= 0.18:
            ranked.append((max(literal * 0.45, similarity), asset))
    return [asset for _, asset in sorted(ranked, key=lambda pair: pair[0], reverse=True)]


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--install", action="store_true")
    parser.add_argument("--audio", action="store_true")
    args = parser.parse_args()
    print(json.dumps(install(audio=args.audio) if args.install else {"available": available(), "model": MODEL_ID, "audio_available": audio_available()}))
