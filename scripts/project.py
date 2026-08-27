#!/usr/bin/env python3
"""Project lifecycle for the Content Machine.

A project is a self-contained directory of everything one video needs:

    projects/<video-id>/
    ├── video_spec.json   render contract, consumed by scripts/render.py
    ├── metadata.json     editorial/content spec + pipeline status
    ├── images/           still images (hardlinked from source, not copied)
    ├── audio/            audio track
    ├── thumbnail/        thumbnail candidates
    ├── output/           rendered MP4 + publication package
    └── logs/             per-run logs

Commands:

    python3 scripts/project.py init <video-id> --images DIR --audio FILE
    python3 scripts/project.py validate <video-id>
    python3 scripts/project.py run <video-id>

`run` is the single orchestration entry point: validate -> render ->
thumbnails -> QC -> package -> READY_FOR_REVIEW.
"""
import argparse
import hashlib
import json
import logging
import os
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import audio as audio_mod  # noqa: E402
import make_visuals  # noqa: E402
import qc  # noqa: E402
import render  # noqa: E402

logging.basicConfig(level=logging.INFO, format="[%(levelname)s] %(message)s")
log = logging.getLogger("project")

ROOT = Path(__file__).resolve().parent.parent
PROJECTS_DIR = ROOT / "projects"

SUPPORTED_AUDIO_EXTENSIONS = (".wav", ".mp3", ".m4a", ".aac", ".flac", ".ogg", ".opus")
SUPPORTED_IMAGE_EXTENSIONS = render.SUPPORTED_IMAGE_EXTENSIONS

SUBDIRS = ("images", "audio", "thumbnail", "output", "logs")

# Thumbnail candidates are pulled from these points through the video.
THUMBNAIL_POSITIONS = (0.25, 0.5, 0.75)

PLACEHOLDER_TITLE = "UNTITLED — set a title before publishing"


class ProjectError(Exception):
    """Raised with a list of human-readable problems."""

    def __init__(self, problems):
        super().__init__("; ".join(problems))
        self.problems = problems


def utc_now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def project_dir(video_id):
    return PROJECTS_DIR / video_id


def sha256(path, chunk=1 << 20):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while block := f.read(chunk):
            h.update(block)
    return h.hexdigest()


def audio_duration(path):
    """Duration in seconds of a file with a real audio stream, or None."""
    return qc.probe_audio_seconds(path)


def ingest(src, dest_dir, force_copy=False):
    """Place src inside dest_dir without duplicating bytes when possible.

    Hardlink (same filesystem, zero extra bytes) -> symlink -> copy.
    Returns (destination_path, method_used).
    """
    dest = dest_dir / src.name
    if dest.exists():
        return dest, "already-present"
    if not force_copy:
        try:
            os.link(src, dest)
            return dest, "hardlink"
        except OSError:
            pass
        try:
            os.symlink(src.resolve(), dest)
            return dest, "symlink"
        except OSError:
            pass
    shutil.copy2(src, dest)
    return dest, "copy"


def list_assets(directory, extensions):
    if not directory.is_dir():
        return []
    return sorted(
        p for p in directory.iterdir()
        if p.is_file() and p.suffix.lower() in extensions
    )


# --------------------------------------------------------------------------
# init
# --------------------------------------------------------------------------

def build_metadata(video_id, title, concept, audience, duration, resolution, fps,
                   production_grade_visuals=None):
    """The editorial/content spec. Kept separate from video_spec.json so the
    renderer's contract stays untouched while content fields evolve."""
    return {
        "video_id": video_id,
        "created_utc": utc_now(),
        "concept": concept or "",
        "audience": audience or "",
        "hypothesis": "",
        "visual_style": "",
        "title_candidates": [title] if title else [],
        "selected_title": title or PLACEHOLDER_TITLE,
        "description": "",
        "tags": [],
        "scenes": [],
        "thumbnail_concept": "",
        "target": {
            "duration_seconds": duration,
            "resolution": resolution,
            "fps": fps,
            "aspect_ratio": "16:9",
        },
        "audio_plan": {"mode": "single_track", "narration": None, "music": None},
        "provenance": {
            # production_grade is written explicitly, including as null.
            # An absent field once read as "no objection" and let
            # undeclared visuals reach review; the gate now requires a
            # deliberate true/false.
            "images": {
                "provider": "local", "model": None,
                "production_grade": production_grade_visuals,
                "notes": "manually supplied"
                         + ("" if production_grade_visuals is not None else
                            "; set production_grade to true or false before review"),
            },
            "audio": {"provider": "local", "model": None, "notes": "manually supplied"},
            "text": {"provider": None, "model": None},
        },
        "experiment": {
            "generation_cost_usd": 0.0,
            "generation_seconds": None,
            "variables": {},
        },
        "status": {
            "assets": "PENDING",
            "render": "PENDING",
            "qc": "PENDING",
            "package": "PENDING",
            "overall": "DRAFT",
        },
        "history": [],
    }


def cmd_init(args):
    video_id = args.video_id
    pdir = project_dir(video_id)
    if pdir.exists() and not args.force:
        log.error("Project already exists: %s (use --force to re-initialize)", pdir)
        return 1

    images_src = Path(args.images).expanduser().resolve()
    audio_src = Path(args.audio).expanduser().resolve()

    problems = []
    if not images_src.is_dir():
        problems.append(f"--images is not a directory: {images_src}")
    if not audio_src.is_file():
        problems.append(f"--audio is not a file: {audio_src}")
    if audio_src.is_file() and audio_src.suffix.lower() not in SUPPORTED_AUDIO_EXTENSIONS:
        problems.append(
            f"unsupported audio format '{audio_src.suffix}'; "
            f"supported: {', '.join(SUPPORTED_AUDIO_EXTENSIONS)}"
        )
    if problems:
        for p in problems:
            log.error("  - %s", p)
        return 1

    source_images = list_assets(images_src, SUPPORTED_IMAGE_EXTENSIONS)
    if not source_images:
        log.error("No supported images (%s) found in %s",
                  ", ".join(SUPPORTED_IMAGE_EXTENSIONS), images_src)
        return 1

    for sub in SUBDIRS:
        (pdir / sub).mkdir(parents=True, exist_ok=True)

    methods = set()
    for img in source_images:
        _, method = ingest(img, pdir / "images", force_copy=args.copy)
        methods.add(method)
    audio_dest, audio_method = ingest(audio_src, pdir / "audio", force_copy=args.copy)
    methods.add(audio_method)
    log.info("Ingested %d image(s) + 1 audio file (%s)",
             len(source_images), ", ".join(sorted(methods)))

    audio_seconds = audio_duration(audio_dest)
    if args.duration:
        duration = float(args.duration)
    elif audio_seconds:
        duration = round(audio_seconds, 2)
        log.info("Duration defaulted to audio length: %.2fs", duration)
    else:
        duration = 30.0
        log.warning("Could not read audio duration; defaulting to %.1fs", duration)

    spec = {
        "width": args.width,
        "height": args.height,
        "fps": args.fps,
        "duration_seconds": duration,
        "images": {"source_dir": "images", "seconds_per_image": args.seconds_per_image},
        "audio": {"file": f"audio/{audio_dest.name}"},
        "ken_burns": {"enabled": True, "zoom_start": 1.0, "zoom_end": 1.15},
        "crossfade": {"enabled": True, "duration_seconds": 0.75},
    }
    (pdir / "video_spec.json").write_text(json.dumps(spec, indent=2) + "\n")

    metadata = build_metadata(
        video_id, args.title, args.concept, args.audience,
        duration, f"{args.width}x{args.height}", args.fps,
        production_grade_visuals=args.production_grade_visuals,
    )
    metadata["provenance"]["images"]["notes"] = (
        f"ingested from {images_src}"
        + ("" if args.production_grade_visuals is not None else
           "; set production_grade to true or false before review"))
    metadata["provenance"]["audio"]["notes"] = f"ingested from {audio_src}"
    metadata["status"]["assets"] = "READY"
    (pdir / "metadata.json").write_text(json.dumps(metadata, indent=2) + "\n")

    log.info("Initialized project: %s", pdir)
    log.info("Render it with: python3 scripts/project.py run %s", video_id)
    return 0


# --------------------------------------------------------------------------
# validate
# --------------------------------------------------------------------------

def load_project(video_id):
    """Load and validate a project. Returns (pdir, spec, raw_spec, metadata)."""
    pdir = project_dir(video_id)
    problems = []

    if not pdir.is_dir():
        raise ProjectError([f"project directory not found: {pdir}"])

    for sub in SUBDIRS:
        if not (pdir / sub).is_dir():
            (pdir / sub).mkdir(parents=True, exist_ok=True)

    spec_path = pdir / "video_spec.json"
    metadata_path = pdir / "metadata.json"

    if not spec_path.is_file():
        problems.append(f"missing video_spec.json in {pdir}")
    if not metadata_path.is_file():
        problems.append(f"missing metadata.json in {pdir}")
    if problems:
        raise ProjectError(problems)

    try:
        raw_spec = render.load_spec(spec_path)
    except render.SpecValidationError as e:
        raise ProjectError(e.errors)

    try:
        metadata = json.loads(metadata_path.read_text())
    except json.JSONDecodeError as e:
        raise ProjectError([f"metadata.json is not valid JSON: {e}"])

    # Reuse the renderer's own validation so the rules live in exactly one
    # place; paths resolve relative to the project directory.
    try:
        spec = render.validate_and_normalize(raw_spec, base_dir=pdir)
    except render.SpecValidationError as e:
        raise ProjectError(e.errors)

    # Project-level checks the renderer does not make: assets must not just
    # exist, they must actually decode.
    unreadable_images = [str(p) for p in spec["image_paths"] if qc.probe_image(p) is None]
    if unreadable_images:
        problems.append(f"unreadable/corrupt image(s): {unreadable_images}")

    # Catch frames QC would reject as black BEFORE paying for a long render.
    too_dark = []
    for image in spec["image_paths"]:
        if str(image) in unreadable_images:
            continue
        luma = qc.image_mean_luma(image)
        if luma is not None and luma < qc.BLACK_FLOOR_MEAN_LUMA:
            too_dark.append(f"{image.name} (mean luma {luma:.1f})")
    if too_dark:
        problems.append(
            f"image(s) too dark; QC would reject them as black frames "
            f"(floor {qc.BLACK_FLOOR_MEAN_LUMA:.0f}): {too_dark}"
        )

    if qc.probe_audio_seconds(spec["audio_path"]) is None:
        problems.append(
            f"unreadable audio, or no usable audio stream, in: {spec['audio_path']}")

    if spec["audio_path"].suffix.lower() not in SUPPORTED_AUDIO_EXTENSIONS:
        problems.append(
            f"unsupported audio format '{spec['audio_path'].suffix}'; "
            f"supported: {', '.join(SUPPORTED_AUDIO_EXTENSIONS)}"
        )

    if not isinstance(metadata.get("video_id"), str) or not metadata["video_id"]:
        problems.append("metadata.json is missing a non-empty 'video_id'")

    if problems:
        raise ProjectError(problems)

    return pdir, spec, raw_spec, metadata


def cmd_validate(args):
    try:
        pdir, spec, _, metadata = load_project(args.video_id)
    except ProjectError as e:
        log.error("Project validation failed with %d problem(s):", len(e.problems))
        for p in e.problems:
            log.error("  - %s", p)
        return 1

    audio_seconds = audio_duration(spec["audio_path"])
    log.info("Project:      %s", pdir)
    log.info("Images:       %d supported file(s)", len(spec["image_paths"]))
    log.info("Audio:        %s (%.2fs)", spec["audio_path"].name, audio_seconds or 0.0)
    log.info("Output:       %dx%d @ %sfps, %.2fs",
             spec["width"], spec["height"], spec["fps"], spec["duration_seconds"])
    if audio_seconds and audio_seconds < spec["duration_seconds"]:
        log.info("Audio is shorter than the video; it will be looped by the renderer.")
    log.info("Title:        %s", metadata.get("selected_title"))
    log.info("Validation passed.")
    return 0


# --------------------------------------------------------------------------
# run
# --------------------------------------------------------------------------

def cmd_audio(args):
    """Compose this project's audio track from its declared audio plan.

    Deliberately does not use load_project(): the audio file is what we are
    about to create, so full project validation cannot pass yet.
    """
    pdir = project_dir(args.video_id)
    spec_path = pdir / "video_spec.json"
    metadata_path = pdir / "metadata.json"
    if not spec_path.is_file() or not metadata_path.is_file():
        log.error("Not a project (missing video_spec.json/metadata.json): %s", pdir)
        return 1

    spec_raw = json.loads(spec_path.read_text())
    metadata = json.loads(metadata_path.read_text())
    plan = dict((metadata.get("audio_plan") or {}).get("composition") or {})
    if not plan.get("layers"):
        log.error("No audio plan to build. Set metadata.audio_plan.composition.layers")
        log.error("Providers available: %s", ", ".join(sorted(audio_mod.PROVIDERS)))
        return 1

    # Audio length follows the video unless the plan overrides it.
    plan.setdefault("target_seconds", spec_raw.get("duration_seconds"))
    if args.duration:
        plan["target_seconds"] = float(args.duration)

    (pdir / "audio").mkdir(parents=True, exist_ok=True)
    output_path = pdir / "audio" / "track.wav"
    try:
        manifest = audio_mod.compose(plan, output_path)
    except audio_mod.AudioError as e:
        metadata.setdefault("status", {})["audio"] = "FAILED"
        save_metadata(pdir, metadata)
        log.error("Audio composition failed with %d problem(s):", len(e.problems))
        for p in e.problems:
            log.error("  - %s", p)
        return 1

    (pdir / "audio" / "audio_manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")

    # Point the render contract at the track we just built.
    spec_raw.setdefault("audio", {})["file"] = "audio/track.wav"
    spec_path.write_text(json.dumps(spec_raw, indent=2) + "\n")

    providers = sorted({layer["provider"] for layer in manifest["layers"]})
    metadata["status"]["audio"] = "OK"
    metadata["provenance"]["audio"] = {
        "provider": "+".join(providers),
        "model": next((l.get("voice") for l in manifest["layers"] if l.get("voice")), None),
        "notes": f"composed locally: {len(manifest['layers'])} layer(s)",
        "commercial_use_cleared": manifest["commercial_use_cleared"],
        "attributions_required": manifest["attributions_required"],
    }
    save_metadata(pdir, metadata)

    log.info("Audio: %s (%.3fs, mean %.1f dB)",
             output_path, manifest["actual_seconds"], manifest["mean_volume_db"])
    if not manifest["commercial_use_cleared"]:
        log.warning("Audio is NOT cleared for commercial use.")
    for attribution in manifest["attributions_required"]:
        log.info("Attribution required: %s", attribution)
    log.info("Now run: ./content-machine run %s", args.video_id)
    return 0


def attach_log_file(pdir):
    log_path = pdir / "logs" / f"run_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"
    handler = logging.FileHandler(log_path)
    handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s"))
    logging.getLogger().addHandler(handler)
    return log_path, handler


def extract_thumbnails(video_path, thumb_dir, duration):
    """Pull candidate thumbnails from the finished video (local, no AI)."""
    candidates = []
    for index, position in enumerate(THUMBNAIL_POSITIONS, start=1):
        out = thumb_dir / f"candidate_{index}.jpg"
        timestamp = max(duration * position, 0.0)
        result = subprocess.run([
            "ffmpeg", "-y", "-v", "error", "-ss", f"{timestamp:.3f}",
            "-i", str(video_path), "-frames:v", "1",
            "-vf", "scale=1280:-2", "-q:v", "2", str(out),
        ], capture_output=True, text=True)
        if result.returncode == 0 and out.is_file():
            candidates.append(out)
        else:
            log.warning("Thumbnail extraction failed at %.2fs: %s",
                        timestamp, result.stderr.strip()[-200:])
    return candidates


CONCEPTS_PATH = ROOT / "experiments" / "concepts.json"


def _load_concept(concept_id):
    """Return the concept dict from concepts.json, or None if absent.

    Intentionally forgiving: concepts.json is optional context, and a
    project without a linked concept skips the drift check entirely.
    """
    if not CONCEPTS_PATH.is_file() or not concept_id:
        return None
    try:
        data = json.loads(CONCEPTS_PATH.read_text())
    except (json.JSONDecodeError, OSError):
        return None
    return next((c for c in data.get("concepts", []) if c.get("id") == concept_id), None)


def _visual_blockers(pdir, metadata, images_provenance):
    """Gate placeholder imagery out of READY_FOR_REVIEW.

    Two independent rules, because self-report alone proved insufficient:

    1. **Fail closed on the claim.** ``production_grade`` must be explicitly
       ``True``. An absent claim is not a passing claim - previously
       ``is False`` let a project that never made the claim sail through.
    2. **Trust the artefact over the metadata.** When the linked concept
       demands depicted imagery, the images themselves are inspected. A
       procedural plate is stamped at generation and a flat fill is
       measurable, so neither can be relabelled into a production asset by
       editing ``provenance``.
    """
    blocking = []
    claimed = images_provenance.get("production_grade")
    if claimed is not True:
        if claimed is False:
            blocking.append(
                "visuals are not production-grade: "
                f"{images_provenance.get('notes', 'placeholder assets in use')}")
        else:
            blocking.append(
                "provenance.images.production_grade is not set: a project "
                "cannot reach review without an explicit production-grade "
                "claim for its visuals (set it to true or false)")

    concept_id = (metadata.get("experiment") or {}).get("concept_id")
    if not concept_id:
        return blocking
    concept = _load_concept(concept_id)
    if concept is None:
        # Linked concept has disappeared from concepts.json - fail loudly
        # so it cannot silently be treated as "no concept linked".
        blocking.append(
            f"concept_id '{concept_id}' referenced by metadata.experiment "
            f"is not present in experiments/concepts.json")
        return blocking
    if concept.get("procedural_visuals_acceptable", False):
        return blocking

    # The concept demands depicted imagery. Inspect the assets themselves.
    offenders = []
    for image in sorted(list_assets(pdir / "images", SUPPORTED_IMAGE_EXTENSIONS)):
        kind, detail = make_visuals.classify(image)
        if kind in ("procedural", "flat"):
            offenders.append(f"{image.name} ({kind}: {detail})")
    if offenders:
        blocking.append(
            f"concept '{concept_id}' requires depicted imagery "
            f"(procedural_visuals_acceptable=false), but these assets are "
            f"not depicted imagery: {'; '.join(offenders)}")
    return blocking


def gate_blockers(pdir, metadata, qc_status, qc_failures, has_thumbnail,
                  audio_manifest):
    """Every rule standing between a rendered project and READY_FOR_REVIEW.

    Single source of truth, shared by ``run`` and ``status``. Keeping one
    implementation is the point: a second copy would drift from this one,
    which is precisely the failure this module now guards against.
    """
    blocking = []
    if qc_status != "PASS":
        blocking.append(f"QC failed: {', '.join(qc_failures)}")
    title = metadata.get("selected_title") or ""
    if not title or title == PLACEHOLDER_TITLE:
        blocking.append("no title set (metadata.selected_title)")
    if not metadata.get("description"):
        blocking.append("no description set (metadata.description)")
    if not has_thumbnail:
        blocking.append("no thumbnail available")

    images_provenance = metadata.get("provenance", {}).get("images", {})
    blocking.extend(_visual_blockers(pdir, metadata, images_provenance))

    # Audio rights gate: an unclearable track must never reach review.
    if audio_manifest and not audio_manifest.get("commercial_use_cleared", False):
        unclear = [l.get("layer_id") for l in audio_manifest.get("layers", [])
                   if not l.get("commercial_use")]
        blocking.append(f"audio not cleared for commercial use: {unclear}")
    return blocking


def gate_digest(pdir, metadata, video_path):
    """Fingerprint every input a gate verdict depends on.

    A verdict is only meaningful for the state it was computed from. Storing
    this alongside the verdict makes a later edit to the spec, the assets or
    the provenance detectable as staleness instead of silently leaving a
    READY_FOR_REVIEW that no longer holds.
    """
    concept_id = (metadata.get("experiment") or {}).get("concept_id")
    concept = _load_concept(concept_id) if concept_id else None
    manifest_path = pdir / "audio" / "audio_manifest.json"
    audio_manifest = (json.loads(manifest_path.read_text())
                      if manifest_path.is_file() else {})
    spec_path = pdir / "video_spec.json"
    inputs = {
        "provenance_images": metadata.get("provenance", {}).get("images", {}),
        "selected_title": metadata.get("selected_title"),
        "description": metadata.get("description"),
        "concept_id": concept_id,
        "concept_procedural_ok": (concept or {}).get("procedural_visuals_acceptable"),
        "audio_cleared": audio_manifest.get("commercial_use_cleared"),
        "spec": json.loads(spec_path.read_text()) if spec_path.is_file() else None,
        "images": sorted(
            (p.name, sha256(p))
            for p in list_assets(pdir / "images", SUPPORTED_IMAGE_EXTENSIONS)),
        "audio": sorted(
            (p.name, sha256(p))
            for p in list_assets(pdir / "audio", SUPPORTED_AUDIO_EXTENSIONS)),
        "video": sha256(video_path) if video_path.is_file() else None,
    }
    blob = json.dumps(inputs, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(blob).hexdigest()


def cmd_status(args):
    """Report a project's verdict and whether it still applies.

    Answers the questions an autonomous system has to be able to answer:
    what is the state, was it computed from what is on disk right now, and
    what would block it if it were re-evaluated.
    """
    pdir = project_dir(args.video_id)
    meta_path = pdir / "metadata.json"
    if not meta_path.is_file():
        log.error("No such project: %s", args.video_id)
        return 1
    metadata = json.loads(meta_path.read_text())
    status = metadata.get("status", {})
    recorded = status.get("overall", "UNKNOWN")
    video_path = pdir / "output" / f"{args.video_id}.mp4"

    print(f"project:  {args.video_id}")
    print(f"recorded: {recorded}")

    if not video_path.is_file():
        print("verdict:  NOT_RENDERED - no output video; run './content-machine "
              f"run {args.video_id}'")
        return 2

    qc_path = pdir / "output" / "qc_report.json"
    qc_report = json.loads(qc_path.read_text()) if qc_path.is_file() else {}
    manifest_path = pdir / "audio" / "audio_manifest.json"
    audio_manifest = (json.loads(manifest_path.read_text())
                      if manifest_path.is_file() else {})

    # The verdict is recomputed from what is on disk right now, so it is
    # always current regardless of what metadata records.
    blocking = gate_blockers(
        pdir, metadata,
        qc_report.get("status", "MISSING"), qc_report.get("failures", []),
        bool(list_assets(pdir / "thumbnail", (".jpg", ".jpeg"))),
        audio_manifest)
    verdict = "READY_FOR_REVIEW" if not blocking else "NEEDS_ATTENTION"
    print(f"verdict:  {verdict}")

    if blocking:
        print("blocking:")
        for issue in blocking:
            print(f"  - {issue}")

    # Staleness is reported separately from the verdict, because they answer
    # different questions: the verdict is what holds now, staleness is
    # whether the stored artefacts still describe it.
    if recorded != verdict:
        print(f"WARNING:  recorded status '{recorded}' is STALE - a re-run "
              f"would produce '{verdict}'")
    stored = status.get("gate_digest")
    if stored is None:
        print("digest:   absent (verdict predates digest tracking); "
              "re-run to establish it")
    elif stored != gate_digest(pdir, metadata, video_path):
        print("digest:   CHANGED - project inputs differ from those the "
              "recorded verdict was computed from")
    else:
        print("digest:   matches the recorded verdict's inputs")

    return 0 if verdict == "READY_FOR_REVIEW" else 2


def cmd_run(args):
    started = datetime.now(timezone.utc)
    try:
        pdir, spec, raw_spec, metadata = load_project(args.video_id)
    except ProjectError as e:
        log.error("Project validation failed with %d problem(s):", len(e.problems))
        for p in e.problems:
            log.error("  - %s", p)
        return 1

    log_path, handler = attach_log_file(pdir)
    try:
        log.info("=== Stage 1/5: validate === OK")

        # --- Stage 2: render -------------------------------------------------
        output_path = pdir / "output" / f"{args.video_id}.mp4"
        output_path.parent.mkdir(parents=True, exist_ok=True)
        log.info("=== Stage 2/5: render ===")
        cmd = render.build_ffmpeg_command(spec, output_path)
        try:
            render.run_ffmpeg(cmd)
        except SystemExit:
            metadata["status"]["render"] = "FAILED"
            metadata["status"]["overall"] = "FAILED"
            save_metadata(pdir, metadata)
            log.error("Render failed. Stage: render. Log: %s", log_path)
            log.error("Retry is safe: fix the reported ffmpeg error and re-run.")
            return 1
        if not output_path.is_file():
            metadata["status"]["render"] = "FAILED"
            metadata["status"]["overall"] = "FAILED"
            save_metadata(pdir, metadata)
            log.error("ffmpeg reported success but no output file at %s", output_path)
            return 1
        metadata["status"]["render"] = "OK"
        log.info("Rendered: %s (%.2f MB)", output_path,
                 output_path.stat().st_size / (1024 * 1024))

        # --- Stage 3: thumbnails ---------------------------------------------
        log.info("=== Stage 3/5: thumbnails ===")
        candidates = extract_thumbnails(
            output_path, pdir / "thumbnail", spec["duration_seconds"])
        log.info("Extracted %d thumbnail candidate(s)", len(candidates))

        # --- Stage 4: QC ------------------------------------------------------
        log.info("=== Stage 4/5: quality control ===")
        report = qc.qc_video(
            output_path,
            expected={
                "width": spec["width"], "height": spec["height"],
                "fps": spec["fps"], "duration_seconds": spec["duration_seconds"],
            },
            source_images=spec["image_paths"],
        )
        qc.log_report(report)
        (pdir / "output" / "qc_report.json").write_text(json.dumps(report, indent=2) + "\n")
        metadata["status"]["qc"] = report["status"]

        # --- Stage 5: package --------------------------------------------------
        log.info("=== Stage 5/5: publication package ===")
        audio_manifest = {}
        manifest_path = pdir / "audio" / "audio_manifest.json"
        if manifest_path.is_file():
            audio_manifest = json.loads(manifest_path.read_text())

        blocking = gate_blockers(
            pdir, metadata, report["status"], report["failures"],
            bool(candidates), audio_manifest)
        digest = gate_digest(pdir, metadata, output_path)
        title = metadata.get("selected_title") or ""

        elapsed = (datetime.now(timezone.utc) - started).total_seconds()
        metadata["experiment"]["generation_seconds"] = round(elapsed, 2)

        package = {
            "video_id": args.video_id,
            "generated_utc": utc_now(),
            "status": "READY_FOR_REVIEW" if not blocking else "NEEDS_ATTENTION",
            "blocking_issues": blocking,
            # Fingerprint of the inputs this verdict was computed from.
            # "content-machine status" compares it against the project on
            # disk, so an edit after the fact shows up as STALE.
            "gate_digest": digest,
            "video": {
                "path": str(output_path.relative_to(ROOT)),
                "sha256": sha256(output_path),
                "size_bytes": output_path.stat().st_size,
                "width": spec["width"],
                "height": spec["height"],
                "fps": spec["fps"],
                "duration_seconds": spec["duration_seconds"],
                "video_codec": "h264",
                "audio_codec": "aac",
            },
            "thumbnail": {
                "primary": str(candidates[0].relative_to(ROOT)) if candidates else None,
                "candidates": [str(c.relative_to(ROOT)) for c in candidates],
            },
            "title": title,
            "description": metadata.get("description", ""),
            "tags": metadata.get("tags", []),
            "concept": metadata.get("concept", ""),
            "audience": metadata.get("audience", ""),
            "generation": {
                "renderer": "scripts/render.py (ffmpeg)",
                "image_provider": metadata["provenance"]["images"]["provider"],
                "image_production_grade": metadata["provenance"]["images"].get("production_grade"),
                "image_notes": metadata["provenance"]["images"].get("notes"),
                "audio_provider": metadata["provenance"]["audio"]["provider"],
                "text_provider": metadata["provenance"]["text"]["provider"],
                "generation_seconds": round(elapsed, 2),
                "generation_cost_usd": metadata["experiment"]["generation_cost_usd"],
            },
            "audio": {
                "commercial_use_cleared": audio_manifest.get("commercial_use_cleared"),
                "attributions_required": audio_manifest.get("attributions_required", []),
                "layers": [
                    {k: l.get(k) for k in ("layer_id", "provider", "license", "commercial_use")}
                    for l in audio_manifest.get("layers", [])
                ],
            } if audio_manifest else None,
            "qc": {"status": report["status"], "failures": report["failures"]},
            # Things a human must confirm during review. Not blockers -
            # READY_FOR_REVIEW means assembled and ready to be checked, not
            # cleared to publish.
            "review_checklist": metadata.get("review_checklist", []),
            "publish": {
                "approved_by_human": False,
                "published": False,
                "publication_id": None,
                "published_utc": None,
            },
        }
        package_path = pdir / "output" / "publication_package.json"
        package_path.write_text(json.dumps(package, indent=2) + "\n")

        metadata["status"]["package"] = "OK"
        metadata["status"]["overall"] = package["status"]
        metadata["status"]["gate_digest"] = digest
        metadata["history"].append({
            "utc": utc_now(), "action": "run",
            "result": package["status"], "log": str(log_path.relative_to(ROOT)),
        })
        save_metadata(pdir, metadata)

        if blocking:
            log.warning("Status: NEEDS_ATTENTION")
            for issue in blocking:
                log.warning("  - %s", issue)
        else:
            log.info("Status: READY_FOR_REVIEW")
        log.info("Package: %s", package_path)
        log.info("Log:     %s", log_path)
        return 0 if not blocking else 2
    finally:
        logging.getLogger().removeHandler(handler)
        handler.close()


def save_metadata(pdir, metadata):
    (pdir / "metadata.json").write_text(json.dumps(metadata, indent=2) + "\n")


def main():
    parser = argparse.ArgumentParser(
        description="Content Machine project lifecycle.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_init = sub.add_parser("init", help="create a project from a folder of images + an audio file")
    p_init.add_argument("video_id")
    p_init.add_argument("--images", required=True, help="directory of source images")
    p_init.add_argument("--audio", required=True, help="audio file")
    p_init.add_argument("--title", default=None)
    p_init.add_argument("--concept", default=None)
    p_init.add_argument("--audience", default=None)
    p_init.add_argument("--width", type=int, default=1920)
    p_init.add_argument("--height", type=int, default=1080)
    p_init.add_argument("--fps", type=int, default=30)
    p_init.add_argument("--duration", type=float, default=None,
                        help="seconds; defaults to the audio track's length")
    p_init.add_argument("--seconds-per-image", type=float, default=4.0)
    p_init.add_argument("--copy", action="store_true",
                        help="copy assets instead of hardlinking them")
    p_init.add_argument("--force", action="store_true", help="re-initialize an existing project")
    p_init.add_argument(
        "--production-grade-visuals", dest="production_grade_visuals",
        action="store_const", const=True, default=None,
        help="declare the supplied images as production-grade. Without this "
             "the claim is undeclared and review is blocked until it is set.")
    p_init.set_defaults(func=cmd_init)

    p_val = sub.add_parser("validate", help="check a project's assets and spec")
    p_val.add_argument("video_id")
    p_val.set_defaults(func=cmd_validate)

    p_audio = sub.add_parser("audio", help="compose the project's audio track from its audio plan")
    p_audio.add_argument("video_id")
    p_audio.add_argument("--duration", type=float, default=None,
                         help="override target seconds (defaults to the video duration)")
    p_audio.set_defaults(func=cmd_audio)

    p_run = sub.add_parser("run", help="validate -> render -> thumbnails -> QC -> package")
    p_run.add_argument("video_id")
    p_run.set_defaults(func=cmd_run)

    p_status = sub.add_parser(
        "status", help="report a project's verdict and whether it still applies")
    p_status.add_argument("video_id")
    p_status.set_defaults(func=cmd_status)

    args = parser.parse_args()

    if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        log.error("ffmpeg and ffprobe must be installed and on PATH.")
        return 1
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
