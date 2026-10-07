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
import fcntl
import hashlib
import json
import logging
import math
import os
import re
import shutil
import subprocess
import sys
import tarfile
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import audio as audio_mod  # noqa: E402
import creative as creative_mod  # noqa: E402
import envfile  # noqa: E402
import generation  # noqa: E402
import goal as goal_mod  # noqa: E402
import kdenlive as kdenlive_mod  # noqa: E402
import make_visuals  # noqa: E402
import media as media_mod  # noqa: E402
import motion as motion_mod  # noqa: E402
import qc  # noqa: E402
import render  # noqa: E402
import research  # noqa: E402
import sound_design  # noqa: E402
import storyboard as storyboard_mod  # noqa: E402
import subject_research  # noqa: E402
import visual_direction  # noqa: E402

logging.basicConfig(level=logging.INFO, format="[%(levelname)s] %(message)s")
log = logging.getLogger("project")

ROOT = Path(__file__).resolve().parent.parent
PROJECTS_DIR = ROOT / "projects"

SUPPORTED_AUDIO_EXTENSIONS = (".wav", ".mp3", ".m4a", ".aac", ".flac", ".ogg", ".opus")
SUPPORTED_IMAGE_EXTENSIONS = render.SUPPORTED_IMAGE_EXTENSIONS

SUBDIRS = ("images", "audio", "thumbnail", "output", "logs")

# Thumbnail candidates are pulled from these points through the video.
THUMBNAIL_POSITIONS = (0.25, 0.5, 0.75)
# Upper bound on candidates when they are taken per distinct picture, so a
# fifty-scene board does not launch fifty ffmpeg seeks for a choice a human
# makes from a handful.
MAX_THUMBNAIL_CANDIDATES = 6

PLACEHOLDER_TITLE = "UNTITLED — set a title before publishing"


class ProjectError(Exception):
    """Raised with a list of human-readable problems.

    A single string is accepted as one problem. Joining it character by
    character - which is what ``"; ".join`` does to a str - produced
    unreadable API details and defeated the callers that route on the
    message ("no such project" became "n; o;  ; s; ..." and a 404 turned
    into a 400).
    """

    def __init__(self, problems):
        if isinstance(problems, str):
            problems = [problems]
        problems = list(problems)
        super().__init__("; ".join(problems))
        self.problems = problems


class ProjectBusyError(ProjectError):
    """Another mutating operation already holds this project's lock."""

    def __init__(self, video_id):
        super().__init__(
            [f"another operation is already in progress for {video_id}"])
        self.video_id = video_id


class ReviewDecisionError(ProjectError):
    """record_review_decision refused: stale digest, an unmet gate, or bad input."""

    def __init__(self, problems):
        if isinstance(problems, str):
            problems = [problems]
        super().__init__(problems)


@contextmanager
def project_lock(video_id):
    """The one concurrency primitive every mutating domain function uses.

    An OS-level file lock (fcntl.flock), not a Django/Postgres-only
    mechanism, so a CLI subprocess, a web request, and a background task all
    get the same protection for free regardless of which one is calling.
    Fails fast (never blocks) - a caller finding the project busy gets a
    clear error immediately rather than a request hanging behind a
    multi-minute render.
    """
    pdir = project_dir(video_id)
    pdir.mkdir(parents=True, exist_ok=True)
    lock_path = pdir / ".lock"
    fd = os.open(lock_path, os.O_CREAT | os.O_RDWR)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ProjectBusyError(video_id)
        yield
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


@dataclass
class StageResult:
    """Uniform return value for every typed run_* domain function.

    `exit_code` preserves the CLI's existing 0/1/2 semantics byte-for-byte
    (a `cmd_X` shim just does `return run_X(...).exit_code`); `data` carries
    whatever a caller other than the CLI - a DRF view, a Celery task - needs
    without re-parsing metadata.json itself. This is the one type that
    crosses the CLI/Django/Celery boundary; `argparse.Namespace` never does.
    """
    ok: bool
    exit_code: int
    message: str
    data: dict = field(default_factory=dict)


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

def import_catalog_project(video_id, image_ids, audio_id, *, title, description,
                           catalog=media_mod.DEFAULT_CATALOG, duration=None,
                           width=1920, height=1080, fps=30, transition_seconds=0.75,
                           motion="zoom_in", allow_unverified_audio=False):
    """Bootstrap an existing-media project through the ordinary render/QC gate.

    This is an explicit still-image edit, not a clip editor or semantic planner.
    Inputs remain external references; the metadata snapshots their identities.
    """
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]*", video_id):
        raise ProjectError("video_id must contain only letters, digits, underscores and hyphens")
    if not image_ids or len(image_ids) > render.MAX_IMAGE_SLOTS:
        raise ProjectError(f"Select between 1 and {render.MAX_IMAGE_SLOTS} images")
    if not isinstance(fps, int) or isinstance(fps, bool) or fps <= 0:
        raise ProjectError("fps must be a positive integer")
    if not isinstance(transition_seconds, (int, float)) or not math.isfinite(transition_seconds) or transition_seconds < 0:
        raise ProjectError("transition_seconds must be finite and nonnegative")
    try:
        visuals = [media_mod.select(i, kind="image", catalog=catalog) for i in image_ids]
        audio_asset, audio_path = media_mod.select(
            audio_id, kind="audio", catalog=catalog,
            require_rights=not allow_unverified_audio)
    except media_mod.MediaError as exc:
        raise ProjectError(str(exc)) from exc
    seconds = audio_duration(audio_path) if duration is None else duration
    if not isinstance(seconds, (int, float)) or not math.isfinite(seconds) or seconds <= 0:
        raise ProjectError("duration must be a finite positive number")
    total_frames = round(seconds * fps)
    if total_frames / fps > (audio_duration(audio_path) or 0) + 1 / fps:
        raise ProjectError("Selected audio is shorter than the edit; compose an intentional loop with the audio stage first")
    overlap = round(transition_seconds * fps) if len(visuals) > 1 else 0
    base, extra = divmod(total_frames + overlap * (len(visuals) - 1), len(visuals))
    if base <= 2 * overlap or base < 1:
        raise ProjectError("Edit is too short for the selected images and transition overlaps")
    scenes = [{"scene_id": f"s{i + 1:02d}", "image": str(path),
               "duration_seconds": (base + (i < extra)) / fps,
               "motion": {"kind": motion, "fit": "cover"},
               "transition": {"kind": "crossfade" if overlap else "cut",
                              "duration_seconds": overlap / fps}}
              for i, (_, path) in enumerate(visuals)]
    raw = {"width": width, "height": height, "fps": fps,
           "duration_seconds": total_frames / fps, "scenes": scenes,
           "audio": {"file": str(audio_path)}}
    try:
        render.validate_and_normalize(raw, base_dir=project_dir(video_id))
    except render.SpecValidationError as exc:
        raise ProjectError(exc.errors) from exc
    metadata = build_metadata(video_id, title, "", "", total_frames / fps,
                              f"{width}x{height}", fps)
    metadata["description"] = description
    metadata["ingestion"] = {
        "catalog": str(Path(catalog).resolve()), "method": "reference",
        "allow_unverified_rights": bool(allow_unverified_audio),
        "inputs": [dict(asset, selected_path=str(path))
                   for asset, path in [*visuals, (audio_asset, audio_path)]],
    }
    metadata["provenance"]["images"].update(
        provider="catalog", notes="Explicit catalog selection; source origin recorded per asset")
    metadata["provenance"]["audio"].update(
        provider="catalog", notes="Existing audio; source and rights in ingestion.inputs")
    metadata["status"]["assets"] = "READY"
    try:
        rights = media_mod.rights_record(audio_asset)
    except media_mod.MediaError:
        if not allow_unverified_audio:
            raise
        source_rights = audio_asset.get("rights") or {}
        rights = {
            "source": source_rights.get("source") or audio_asset.get("source") or "unknown",
            "license": source_rights.get("license") or "unknown",
            "commercial_use": False,
            "attribution_required": source_rights.get("attribution_required"),
            "attribution_text": source_rights.get("attribution_text"),
            "evidence": source_rights.get("evidence") or "Track-level rights record pending",
            "status": "UNVERIFIED",
        }
    manifest = {"status": "OK", "commercial_use_cleared": rights.get("commercial_use") is True,
                "layers": [dict(rights, layer_id="existing-audio", provider="file",
                                asset_id=audio_id, parameters={"path": str(audio_path)})],
                "attributions_required": [rights["attribution_text"]]
                if rights.get("attribution_required") else []}
    with project_lock(video_id):
        pdir = project_dir(video_id)
        if (pdir / "metadata.json").exists():
            raise ProjectError(f"Project already exists: {video_id}; import uses a new project id")
        for relative, expected in (("video_spec.json", raw),
                                   ("audio/audio_manifest.json", manifest)):
            path = pdir / relative
            if path.exists():
                try:
                    matches = json.loads(path.read_text()) == expected
                except (ValueError, OSError):
                    matches = False
                if not matches:
                    raise ProjectError(f"Refusing to overwrite existing {path}; choose a new project id")
        for sub in SUBDIRS:
            (pdir / sub).mkdir(parents=True, exist_ok=True)
        media_mod.atomic_json(pdir / "video_spec.json", raw)
        media_mod.atomic_json(pdir / "audio" / "audio_manifest.json", manifest)
        # Metadata is the commit marker; retry an interrupted import with the same id.
        media_mod.atomic_json(pdir / "metadata.json", metadata)
    return StageResult(True, 0, "Catalog assets imported by reference", {
        "video_id": video_id, "duration_seconds": total_frames / fps,
        "scene_count": len(scenes), "editable_kdenlive": False,
        "allow_unverified_audio": bool(allow_unverified_audio),
    })


def _ingestion_problems(metadata):
    problems = []
    for asset in (metadata.get("ingestion") or {}).get("inputs", []):
        try:
            # Validate the actual selected path, not a different intact alias.
            media_mod.resolve(dict(asset, locations=[{
                "host_id": media_mod.host_id(), "path": asset["selected_path"]}]))
            try:
                media_mod.rights_record(asset)
            except media_mod.MediaError:
                # A private bootstrap edit may be rendered for inspection
                # while a YouTube Audio Library track is still being matched
                # to its track-level terms. The manifest remains uncleared,
                # so the publication gate still fails closed.
                allow = (metadata.get("ingestion") or {}).get("allow_unverified_rights")
                if not (allow and asset.get("technical", {}).get("kind") == "audio"
                        and (asset.get("rights") or {}).get("status") == "UNVERIFIED"):
                    raise
        except (media_mod.MediaError, KeyError, TypeError) as exc:
            problems.append(f"Imported source failed verification: {exc}")
    return problems


def cmd_import_media(args):
    try:
        result = import_catalog_project(
            args.video_id, args.image_id, args.audio_id, title=args.title,
            description=args.description, catalog=args.catalog, duration=args.duration,
            width=args.width, height=args.height, fps=args.fps,
            transition_seconds=args.transition_seconds, motion=args.motion,
            allow_unverified_audio=args.allow_unverified_audio)
        print(json.dumps(result.data, indent=2))
        return result.exit_code
    except (ProjectError, OSError) as exc:
        log.error("%s", exc)
        return 1


def storyboard_scene_spec(pdir, raw_spec, board):
    """``raw_spec`` with the storyboard's scenes folded in, normalised.

    The storyboard is the editorial plan and video_spec.json is the render
    contract; this is the one place the first becomes the second. Both the
    renderer and the Kdenlive export need it - the export used to look only
    at video_spec.json, so every storyboarded project (which is now almost
    all of them) refused to export an edit it had just rendered.
    """
    raw = dict(raw_spec)
    raw["scenes"] = [
        {
            "scene_id": scene["scene_id"],
            "duration_seconds": scene["duration_seconds"],
            "image": scene["image"],
            "motion": scene.get("motion") or {},
            "transition": scene.get("transition") or {},
        }
        for scene in board["scenes"]
    ]
    return render.validate_and_normalize(raw, base_dir=pdir)


def build_kdenlive_project(video_id, *, render_output=True):
    """Lower the resolved project edit to an editable Kdenlive project.

    The JSON spec remains the editorial contract. This adapter turns its
    frame-timed scenes into MLT clips.  It creates a project-local editable
    media directory with hardlinks where possible, so the project travels as
    a unit without changing the catalog objects or the owner's originals.
    Crossfade overlaps become alternating editable video tracks; the audio
    source stays on a separate editable track.
    """
    with project_lock(video_id):
        try:
            pdir, spec, raw_spec, metadata = load_project(video_id)
        except ProjectError:
            raise
        if not spec.get("scenes"):
            board = storyboard_mod.load(video_id)
            if board and board.get("scenes"):
                spec = storyboard_scene_spec(pdir, raw_spec, board)
        scenes = spec.get("scenes") or []
        if not scenes:
            raise ProjectError("Kdenlive export requires an explicit scene timeline")
        starts = motion_mod.scene_start_times(scenes)
        editable_media = pdir / "output" / "editable-media"
        editable_media.mkdir(parents=True, exist_ok=True)
        clips = []
        source_handoffs = []
        for scene, start in zip(scenes, starts):
            frames = max(1, round(float(scene["duration_seconds"]) * spec["fps"]))
            source = Path(scene["image_path"])
            local_source, method = ingest(source, editable_media)
            motion = scene.get("motion") or {}
            amount = float(motion.get("amount") or 0.0)
            zoom_in = motion.get("kind") == "zoom_in"
            clips.append(kdenlive_mod.Clip(
                path=local_source,
                start_frame=max(0, round(float(start) * spec["fps"])),
                duration_frames=frames,
                label=scene.get("scene_id") or Path(scene["image_path"]).name,
                track="video",
                zoom_start=1.0 if zoom_in else 1.0 + amount,
                zoom_end=1.0 + amount if zoom_in else 1.0,
            ))
            source_handoffs.append({"source": str(source), "editable_media": str(local_source),
                                    "method": method, "sha256": sha256(local_source)})
        total_frames = max(1, round(float(spec["duration_seconds"]) * spec["fps"]))
        audio_spec = spec.get("audio") or {}
        audio_source = Path(spec["audio_path"])
        local_audio, audio_method = ingest(audio_source, editable_media)
        audio_clip = kdenlive_mod.Clip(
            path=local_audio, start_frame=0,
            duration_frames=total_frames, track="audio", label="Dreamdrip music",
            gain_db=float(audio_spec.get("gain_db") or 0.0),
            fade_in_frames=round(float(audio_spec.get("fade_in_seconds") or 0) * spec["fps"]),
            fade_out_frames=round(float(audio_spec.get("fade_out_seconds") or 0) * spec["fps"]),
        )
        source_handoffs.append({"source": str(audio_source), "editable_media": str(local_audio),
                                "method": audio_method, "sha256": sha256(local_audio)})
        project_path = pdir / "output" / f"{video_id}.kdenlive"
        notes = (
            "Content Machine generated editable timeline. Source paths and "
            "sha256 hashes are recorded on each producer. Audio rights remain "
            "subject to the project's provenance gate."
        )
        result = kdenlive_mod.build_project(
            project_path, width=spec["width"], height=spec["height"], fps=spec["fps"],
            clips=clips, audio_clips=[audio_clip],
            title=metadata.get("selected_title") or video_id, notes=notes)
        verification = kdenlive_mod.verify_project(project_path)
        rendered = None
        render_qc = None
        if render_output:
            rendered = kdenlive_mod.render_project(
                project_path, pdir / "output" / f"{video_id}-kdenlive.mp4")
            render_qc = qc.qc_video(
                Path(rendered["output"]),
                expected={"width": spec["width"], "height": spec["height"],
                          "fps": spec["fps"], "duration_seconds": spec["duration_seconds"]},
                source_images=[clip.path for clip in clips],
            )
            (pdir / "output" / "kdenlive_qc_report.json").write_text(
                json.dumps(render_qc, indent=2) + "\n")
            if render_qc["status"] != "PASS":
                raise ProjectError("Kdenlive render did not pass QC: " +
                                   ", ".join(render_qc["failures"]))
            metadata.setdefault("status", {})["render"] = "OK"
            metadata["status"]["qc"] = "PASS"
        else:
            existing_render = pdir / "output" / f"{video_id}-kdenlive.mp4"
            if existing_render.is_file():
                render_qc = qc.qc_video(
                    existing_render,
                    expected={"width": spec["width"], "height": spec["height"],
                              "fps": spec["fps"], "duration_seconds": spec["duration_seconds"]},
                    source_images=[clip.path for clip in clips],
                )
                if render_qc["status"] != "PASS":
                    raise ProjectError("existing Kdenlive render did not pass QC: " +
                                       ", ".join(render_qc["failures"]))
                metadata.setdefault("status", {})["render"] = "OK"
                metadata["status"]["qc"] = "PASS"
        metadata.setdefault("editing", {})["kdenlive"] = {
            "project": "output/" + project_path.name,
            "render": "output/" + Path(rendered["output"]).name if rendered else None,
            "verified": verification,
            "timeline": result,
            "backend": "MLT/melt",
            "source_handoffs": source_handoffs,
            "source_hashes": {str(clip.path): kdenlive_mod._sha256(clip.path)
                              for clip in [*clips, audio_clip]},
            "qc": render_qc,
            "generated_utc": utc_now(),
        }
        save_metadata(pdir, metadata)
        package = package_kdenlive_project(video_id, project_path, source_handoffs)
        metadata["editing"]["kdenlive"]["package"] = package
        metadata.setdefault("status", {})["package"] = "OK"
        save_metadata(pdir, metadata)
        return StageResult(True, 0, "Kdenlive project built", {
            "project": str(project_path), "render": rendered,
            "verification": verification, "timeline": result, "package": package,
        })


def package_kdenlive_project(video_id, project_path, source_handoffs):
    """Create a portable editable-project handoff without changing sources.

    The project references ``editable-media`` relatively.  The archive keeps
    that directory next to the project and contains an integrity/provenance
    manifest; it deliberately carries the audio's unverified-rights state.
    """
    pdir = project_dir(video_id)
    output = pdir / "output"
    media_dir = output / "editable-media"
    if not project_path.is_file() or not media_dir.is_dir():
        raise ProjectError("Kdenlive package requires project-local editable media")
    manifest = {
        "video_id": video_id,
        "created_utc": utc_now(),
        "project": project_path.name,
        "media_directory": media_dir.name,
        "source_handoffs": source_handoffs,
        # Read from the audio manifest rather than asserted. Stamping every
        # archive "UNVERIFIED" was wrong in the direction that matters least
        # but is still wrong: it taught a reader to ignore the field, which
        # is exactly what makes a rights warning useless when it is true.
        "audio_rights": _audio_rights_statement(pdir),
    }
    manifest_path = output / f"{video_id}-editable-provenance.json"
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n")
    archive = output / f"{video_id}-editable-kdenlive.tar.gz"
    temporary = archive.with_suffix(archive.suffix + ".pending")
    with tarfile.open(temporary, "w:gz") as bundle:
        bundle.add(project_path, arcname=f"{video_id}/{project_path.name}")
        bundle.add(media_dir, arcname=f"{video_id}/{media_dir.name}")
        bundle.add(manifest_path, arcname=f"{video_id}/{manifest_path.name}")
        bundle.add(pdir / "metadata.json", arcname=f"{video_id}/metadata.json")
    os.replace(temporary, archive)
    return {"archive": "output/" + archive.name,
            "provenance": "output/" + manifest_path.name,
            "bytes": archive.stat().st_size}


def _audio_rights_statement(pdir):
    """What this project's audio manifest actually says about its rights.

    Fail-closed in the absence of a manifest: no manifest is "nobody
    established the rights", which is not "the rights are fine".
    """
    manifest_path = pdir / "audio" / "audio_manifest.json"
    if not manifest_path.is_file():
        return ("UNVERIFIED - no audio manifest; rights were never established, "
                "so publication remains blocked")
    try:
        manifest = json.loads(manifest_path.read_text())
    except (json.JSONDecodeError, OSError):
        return "UNVERIFIED - audio manifest could not be read"
    if manifest.get("commercial_use_cleared") is not True:
        return ("UNVERIFIED - the audio manifest does not clear commercial "
                "use; private review only")
    attributions = manifest.get("attributions_required") or []
    cleared = "CLEARED for commercial use by this project's audio manifest"
    if attributions:
        return cleared + "; attribution required: " + "; ".join(attributions)
    return cleared + "; no attribution required"


def run_editable(video_id, render=False):
    """Build the editable Kdenlive project (and its portable archive).

    A stage like any other so the web layer triggers it the same way, with
    the same run record. ``render`` re-renders the timeline through MLT,
    which is a second full encode - off by default, because the reviewable
    MP4 the pipeline already produced is the same edit.
    """
    try:
        return build_kdenlive_project(video_id, render_output=render)
    except (ProjectError, kdenlive_mod.KdenliveError, OSError) as exc:
        log.error("Kdenlive assembly failed: %s", exc)
        return StageResult(False, 1, f"editable export failed: {exc}")


# Deleting a production removes work that cannot be recovered, so the one
# implementation behind every adapter refuses the cases where "unwanted" is
# unlikely to be what was meant, and writes down what it removed.
DELETION_LEDGER = PROJECTS_DIR / "deleted-projects.log"


def delete_project(video_id, actor, reason=""):
    """Permanently remove a production and the artefacts that only it owns.

    Refuses a published project outright: an upload that exists in the world
    cannot be made not to have happened by deleting the folder that explains
    it. Everything else goes - the project directory, its research brief,
    findings and directives, and the concept derived for it - and a line
    naming what was removed, by whom and why is appended to the ledger.
    """
    pdir = project_dir(video_id)
    if not pdir.is_dir():
        raise ProjectError([f"no such project: {video_id}"])
    if not actor:
        raise ProjectError(["deletion needs a named actor"])
    metadata = {}
    metadata_path = pdir / "metadata.json"
    if metadata_path.is_file():
        try:
            metadata = json.loads(metadata_path.read_text())
        except (json.JSONDecodeError, OSError):
            metadata = {}
    if ((metadata.get("publish") or {}).get("published")
            or (metadata.get("status") or {}).get("overall") == "PUBLISHED"):
        raise ProjectError([
            f"{video_id} has been published; its record is not deletable from "
            f"here. Unpublish it first if that is really what you mean."])

    removed = [str(pdir.relative_to(ROOT))]
    concept_id = (metadata.get("experiment") or {}).get("concept_id")
    extras = [research.brief_path(video_id), research.findings_path(video_id),
              research.directives_path(video_id),
              ROOT / "research" / "subjects" / f"{video_id}.json"]
    if concept_id and goal_mod.load_derived_concept(concept_id) is not None:
        extras.append(goal_mod.derived_path(concept_id))
    shutil.rmtree(pdir)
    for path in extras:
        if path.is_file():
            path.unlink()
            removed.append(str(path.relative_to(ROOT)))

    DELETION_LEDGER.parent.mkdir(parents=True, exist_ok=True)
    with open(DELETION_LEDGER, "a") as ledger:
        ledger.write(json.dumps({
            "utc": utc_now(), "video_id": video_id, "actor": actor,
            "reason": reason, "removed": removed,
            "title": metadata.get("selected_title"),
        }) + "\n")
    log.info("Deleted %s (%d path(s)) on behalf of %s", video_id, len(removed), actor)
    return {"video_id": video_id, "removed": removed, "actor": actor,
            "reason": reason, "deleted_utc": utc_now()}


def cmd_kdenlive(args):
    try:
        result = build_kdenlive_project(args.video_id, render_output=not args.no_render)
        print(json.dumps(result.data, indent=2))
        return result.exit_code
    except (ProjectError, kdenlive_mod.KdenliveError, OSError) as exc:
        log.error("Kdenlive assembly failed: %s", exc)
        return 1


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

    problems.extend(_ingestion_problems(metadata))
    if problems:
        raise ProjectError(problems)

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

def run_research(video_id, concept_id=None, force=False):
    """Subject research and brief-driven competitor research for one video,
    each cached once.

    Two independent artifacts, with different failure contracts:

    - Subject research (facts a narration script may state) is required and
      fails closed for concepts flagged ``requires_subject_research`` - a
      no-op otherwise, so niches with no factual claims never pay for it.
    - Competitor/format research is optional and additive: driven by the
      project's own research brief (``research.load_brief``), which most
      projects will not have, and by a configured search provider, which
      this build may not have either. Neither absence is a stage failure -
      only a search that was actually attempted and came back too thin to
      say anything sourced is. ``creative`` picks up whatever exists here
      through ``research.load_brief``/``load_findings``; it never re-runs
      this stage itself.
    """
    with project_lock(video_id):
        pdir = project_dir(video_id)
        metadata_path = pdir / "metadata.json"
        if not metadata_path.is_file():
            log.error("Not a project (missing metadata.json): %s", pdir)
            return StageResult(False, 1, f"not a project: {video_id}")

        metadata = json.loads(metadata_path.read_text())
        concept_id = concept_id or (metadata.get("experiment") or {}).get("concept_id")
        concept = _load_concept(concept_id) if concept_id else None
        if concept is None:
            log.error(
                "No concept linked (pass --concept-id or set "
                "metadata.experiment.concept_id).")
            return StageResult(False, 1, "no concept linked")

        notes = []
        subject_artifact = None
        if concept.get("requires_subject_research"):
            try:
                subject_artifact = subject_research.research_subject(
                    video_id, concept, force=force)
            except subject_research.SubjectResearchError as e:
                log.error("Subject research failed closed: %s", e)
                return StageResult(False, 1, str(e))
            metadata.setdefault("status", {})["subject_research"] = "OK"
            log.info("Subject research: %d sourced fact(s) from %d source(s) via %s",
                     len(subject_artifact["facts"]), len(subject_artifact["sources"]),
                     subject_artifact["provider"])
            notes.append(f"{len(subject_artifact['facts'])} sourced fact(s)")
        else:
            log.info("%s does not require subject research; nothing to source", concept_id)

        findings_artifact = None
        research_brief = research.load_brief(video_id)
        if research_brief is not None:
            explicit = _explicitly_requested_research(research_brief)
            try:
                findings_artifact = research.research_project(
                    video_id, research_brief, force=force)
                metadata.setdefault("status", {})["competitor_research"] = "OK"
                log.info("Competitor research: %d finding(s) via %s; topics covered: %s",
                         len(findings_artifact["findings"]), findings_artifact["provider"],
                         ", ".join(findings_artifact.get("topics_covered") or []) or "none")
                for topic in findings_artifact.get("topics_uncovered") or []:
                    log.warning("No sourced findings for requested topic %r", topic)
                notes.append(f"{len(findings_artifact['findings'])} research finding(s)")
                # What the findings change about the build, derived now so
                # the storyboard and audio stages read a decision rather
                # than re-interpreting prose. Empty is a valid outcome.
                spec_raw = json.loads((pdir / "video_spec.json").read_text())
                directives = research.save_directives(
                    video_id, research.production_directives(
                        findings_artifact, brief=research_brief,
                        target_seconds=spec_raw.get("duration_seconds")))
                if directives["decisions"]:
                    log.info("Research changes %d production parameter(s): %s",
                             len(directives["decisions"]),
                             ", ".join(f"{d['parameter']}={d['value']}"
                                       for d in directives["decisions"]))
                    notes.append(f"{len(directives['decisions'])} production directive(s)")
                else:
                    log.info("Findings say nothing that changes a production "
                             "parameter; stage defaults stand.")
            except research.ResearchError as e:
                if explicit:
                    # The brief named something specific to study. Skipping
                    # that quietly produces a video that looks researched
                    # and is not, which is the failure this branch exists
                    # to make impossible.
                    metadata.setdefault("status", {})["competitor_research"] = "FAILED"
                    save_metadata(pdir, metadata)
                    log.error("Requested research could not be performed: %s", e)
                    log.error("Named in the brief: %s", "; ".join(explicit))
                    return StageResult(False, 1, f"requested research failed: {e}")
                metadata.setdefault("status", {})["competitor_research"] = "SKIPPED"
                log.warning("Competitor research skipped (not a stage failure): %s", e)

        save_metadata(pdir, metadata)
        if not notes:
            return StageResult(
                True, 0,
                f"{concept_id} does not require subject research; no research brief to work from")
        log.info("Now run: ./content-machine creative %s", video_id)
        return StageResult(True, 0, "; ".join(notes),
                           {"artifact": subject_artifact, "findings": findings_artifact})


def _explicitly_requested_research(brief):
    """What this brief asked for by name, if anything.

    A brief carrying only a niche is a hint: research it if a provider
    exists. A brief that names seed references or specific research topics
    is an instruction, and an instruction that cannot be carried out is a
    stage failure, not a warning.
    """
    named = []
    for ref in brief.get("seed_references") or []:
        value = (ref.get("value") or "").strip()
        if value:
            named.append(f"{ref.get('type') or 'seed'} {value}")
    for topic in brief.get("research_topics") or []:
        named.append(f"topic {topic}")
    return named


def cmd_research(args):
    return run_research(args.video_id, concept_id=args.concept_id, force=args.force).exit_code


def run_creative(video_id, force=False):
    """Fill in title, script, description, image direction and the
    executable audio plan from the project's linked concept.

    Deliberately mirrors run_audio/run_visuals: it edits metadata fields,
    not gate inputs. It never touches provenance.images.production_grade -
    that claim stays a human act, exactly as it does everywhere else in this
    module.
    """
    with project_lock(video_id):
        pdir = project_dir(video_id)
        spec_path = pdir / "video_spec.json"
        metadata_path = pdir / "metadata.json"
        if not spec_path.is_file() or not metadata_path.is_file():
            log.error("Not a project (missing video_spec.json/metadata.json): %s", pdir)
            return StageResult(False, 1, f"not a project: {video_id}")

        spec_raw = json.loads(spec_path.read_text())
        metadata = json.loads(metadata_path.read_text())

        concept_id = (metadata.get("experiment") or {}).get("concept_id")
        concept = _load_concept(concept_id) if concept_id else None
        if concept is None:
            log.error(
                "No concept linked (metadata.experiment.concept_id). The creative "
                "brief is generated from a concept's own fields, not invented - "
                "scaffold the project from one with `experiment.py scaffold`."
            )
            return StageResult(False, 1, "no concept linked")

        subject = None
        if concept.get("requires_subject_research"):
            subject = subject_research.load_subject_research(video_id)
            if subject is None:
                log.error(
                    "%s requires source-backed subject research, and none is "
                    "cached. Run `./content-machine research %s` first - a "
                    "creative brief must not invent facts from model memory.",
                    concept_id, video_id)
                return StageResult(False, 1, "subject research required first")

        research_brief = research.load_brief(video_id)
        findings = research.load_findings(video_id)

        target_seconds = spec_raw.get("duration_seconds")
        try:
            brief = creative_mod.generate_brief(
                concept, target_seconds, subject_research=subject,
                research_brief=research_brief, findings=findings)
        except creative_mod.CreativeError as e:
            log.error("Creative brief failed: %s", e)
            return StageResult(False, 1, str(e))

        placeholder_title = not metadata.get("selected_title") or metadata["selected_title"] == PLACEHOLDER_TITLE
        if placeholder_title or force:
            metadata["selected_title"] = brief["title"]
            metadata.setdefault("title_candidates", [])
            if brief["title"] not in metadata["title_candidates"]:
                metadata["title_candidates"].append(brief["title"])
        if not metadata.get("description") or force:
            metadata["description"] = brief["description"]
        if not metadata.get("script") or force:
            metadata["script"] = brief["narration_script"]

        visual_plan = metadata.setdefault("visual_plan", {})
        if not visual_plan.get("prompt") or force:
            visual_plan["prompt"] = brief["image_prompt"]
            visual_plan["negative_prompt"] = brief["negative_prompt"]
            visual_plan["style"] = creative_mod.pick_procedural_style(concept)

        audio_plan = metadata.setdefault("audio_plan", {})
        if not (audio_plan.get("composition") or {}).get("layers") or force:
            routed = creative_mod.route_audio(
                concept, target_seconds, brief["narration_script"],
                mood=brief.get("audio_mood"), findings=findings)
            composition = routed["composition"]
            # The decision record is kept whether or not a plan came out of
            # it: "no music source was available" is exactly the case
            # somebody later needs to be able to read.
            audio_plan["direction"] = routed["direction"]
            direction = routed["direction"]
            log.info("Audio direction: this concept needs %s - %s",
                     direction["kind"], direction["kind_reasoning"])
            if composition is None:
                log.warning(
                    "No audio source available for requirement '%s' "
                    "(concept '%s'): %s",
                    concept.get("audio_source_requirement"), concept_id,
                    direction.get("blocked_reason", "nothing applicable"))
            else:
                chosen = direction.get("chosen") or {}
                log.info("Audio source: %s via the %r provider (%s, %s)",
                         chosen.get("source"), chosen.get("provider"),
                         chosen.get("rights"), chosen.get("cost"))
                if chosen.get("production_grade_capable") is False:
                    log.warning(
                        "This source cannot be production-grade %s: %s. "
                        "Review stays blocked until a human listens and "
                        "records a judgement (`audio-grade`).",
                        direction["kind"], chosen.get("detail"))
                design, composition = _design_soundscape(
                    concept, composition, target_seconds, direction,
                    mood=brief.get("audio_mood"),
                    findings=findings, directives=research.load_directives(video_id))
                if design:
                    audio_plan["design"] = design
                audio_plan["composition"] = composition

        save_metadata(pdir, metadata)
        log.info("Creative brief written: title=%r", metadata["selected_title"])
        return StageResult(True, 0, "creative brief written",
                            {"selected_title": metadata["selected_title"]})


def _sound_research_block(findings, directives):
    """What sourced research says about this niche's sound, for the design
    pass. The emphasis line is a directive - layers sources actually named -
    and is stated as such so the model treats it as evidence, not taste."""
    lines = []
    emphasis = ((directives or {}).get("values") or {}).get("audio_emphasis")
    if emphasis:
        lines.append(
            "Sourced research about this niche names these sound layers: "
            + ", ".join(emphasis)
            + ". Build around them where the catalogue above allows it.")
    digest = research.findings_digest(
        {"findings": [f for f in ((findings or {}).get("findings") or [])
                      if f.get("topic") == "audio"]}, max_per_topic=6)
    if digest:
        lines.append("Sourced observations about audio in this niche:\n" + digest)
    if not lines:
        return ""
    return "\n".join(lines) + "\n"


def _design_soundscape(concept, composition, target_seconds, direction, mood=None,
                       findings=None, directives=None):
    """Shape a routed composition into a designed soundscape.

    Fail-soft on purpose, and only in one direction: when the design pass
    cannot run (no LLM configured, the call failed, a reply we could not
    read) the routed composition is used exactly as routed. That is the
    behaviour this pipeline had before sound design existed, so the failure
    mode is "less designed", never "no audio" and never "a layer nobody
    chose". The reason is recorded either way.
    """
    if not composition or not composition.get("layers"):
        return None, composition
    chosen = direction.get("chosen") or {}
    bed_description = (f"{chosen.get('source', 'unknown source')} via the "
                       f"{chosen.get('provider')!r} provider - "
                       f"{chosen.get('detail', '')}")
    try:
        design = sound_design.design_soundscape(
            concept, target_seconds, kind=direction.get("kind"), mood=mood,
            bed_description=bed_description,
            research_block=_sound_research_block(findings, directives))
    except Exception as exc:  # noqa: BLE001 - recorded, never fatal
        log.warning("Sound design pass unavailable (%s); using the routed "
                    "composition unshaped.", exc)
        return {"status": "unavailable", "reason": str(exc),
                "listening_context": sound_design.infer_listening_context(
                    concept, direction.get("kind"))}, composition

    designed = sound_design.compile_soundscape(design, composition, target_seconds)
    if designed is None:
        return design, composition
    design["status"] = "designed"
    log.info("Sound design (%s): %s", design.get("listening_context"),
             design.get("intent") or "no stated intent")
    for entry in design.get("ambience", []):
        log.info("  ambience %-16s %+.0f dB  %s", entry["element"],
                 entry["gain_db"], entry.get("reason", ""))
    for entry in design.get("detail", []):
        log.info("  event    %-16s %+.0f dB every %.0fs  %s", entry["element"],
                 entry["gain_db"], entry["every_seconds"], entry.get("reason", ""))
    for unmet in design.get("wanted_but_unavailable", []):
        log.warning("  sound design wanted something this build has not got: %s", unmet)
    return design, designed


def cmd_creative(args):
    return run_creative(args.video_id, force=args.force).exit_code


def run_audio(video_id, duration=None):
    """Compose this project's audio track from its declared audio plan.

    Deliberately does not use load_project(): the audio file is what we are
    about to create, so full project validation cannot pass yet.
    """
    with project_lock(video_id):
        pdir = project_dir(video_id)
        spec_path = pdir / "video_spec.json"
        metadata_path = pdir / "metadata.json"
        if not spec_path.is_file() or not metadata_path.is_file():
            log.error("Not a project (missing video_spec.json/metadata.json): %s", pdir)
            return StageResult(False, 1, f"not a project: {video_id}")

        spec_raw = json.loads(spec_path.read_text())
        metadata = json.loads(metadata_path.read_text())
        plan = dict((metadata.get("audio_plan") or {}).get("composition") or {})
        if not plan.get("layers"):
            log.error("No audio plan to build. Set metadata.audio_plan.composition.layers")
            log.error("Providers available: %s", ", ".join(sorted(audio_mod.PROVIDERS)))
            return StageResult(False, 1, "no audio plan to build")

        # Audio length follows the video unless the plan overrides it.
        plan.setdefault("target_seconds", spec_raw.get("duration_seconds"))
        if duration:
            plan["target_seconds"] = float(duration)

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
            return StageResult(False, 1, "audio composition failed")

        (pdir / "audio" / "audio_manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")

        # Point the render contract at the track we just built.
        spec_raw.setdefault("audio", {})["file"] = "audio/track.wav"
        spec_path.write_text(json.dumps(spec_raw, indent=2) + "\n")

        providers = sorted({layer["provider"] for layer in manifest["layers"]})
        direction = (metadata.get("audio_plan") or {}).get("direction") or {}
        chosen = direction.get("chosen") or {}
        previous = (metadata.get("provenance") or {}).get("audio") or {}
        metadata["status"]["audio"] = "OK"
        audio_prov = {
            "provider": "+".join(providers),
            "model": next((l.get("voice") for l in manifest["layers"] if l.get("voice")), None),
            "notes": f"composed locally: {len(manifest['layers'])} layer(s)",
            "commercial_use_cleared": manifest["commercial_use_cleared"],
            "attributions_required": manifest["attributions_required"],
            "quality": manifest.get("quality"),
            # What was measured in the finished file and what that means
            # against this listening context's criteria. Kept in provenance
            # so the review surface can show a reviewer the numbers behind
            # a finding instead of only the finding's wording.
            "measurement": manifest.get("measurement"),
            "assessment": manifest.get("assessment"),
            "listening_context": ((manifest.get("assessment") or {})
                                  .get("criteria", {}).get("listening_context")),
            # Carried from the routing decision so the gate can ask "could
            # this source be production-grade at all" without re-deriving
            # the answer from provider names.
            "kind": direction.get("kind"),
            "source": chosen.get("source"),
            "production_grade_capable": chosen.get("production_grade_capable"),
        }
        # A human's judgement survives a re-render of the same plan and is
        # discarded by a different one - the same rule images follow.
        if previous.get("production_grade") is not None:
            same_plan = previous.get("plan_digest") == _audio_plan_digest(plan)
            audio_prov["production_grade"] = (
                previous["production_grade"] if same_plan else None)
            if same_plan:
                for key in ("graded_by", "graded_utc", "grade_notes"):
                    if previous.get(key) is not None:
                        audio_prov[key] = previous[key]
            else:
                log.info("Audio plan changed since it was graded; the "
                         "previous production-grade judgement no longer applies.")
        audio_prov["plan_digest"] = _audio_plan_digest(plan)
        metadata["provenance"]["audio"] = audio_prov
        save_metadata(pdir, metadata)

        log.info("Audio: %s (%.3fs, mean %.1f dB)",
                 output_path, manifest["actual_seconds"], manifest["mean_volume_db"])
        if not manifest["commercial_use_cleared"]:
            log.warning("Audio is NOT cleared for commercial use.")
        for attribution in manifest["attributions_required"]:
            log.info("Attribution required: %s", attribution)
        quality = manifest.get("quality") or {}
        for warning in quality.get("warnings", []):
            log.warning("Audio quality: %s", warning)
        for advisory in quality.get("advisories", []):
            log.info("Audio advisory: %s", advisory)
        measured = manifest.get("measurement") or {}
        if measured.get("integrated_lufs") is not None:
            log.info("Audio measured: %.1f LUFS, true peak %.1f dBFS, "
                     "range %.1f LU (%s) - assessment %s",
                     measured["integrated_lufs"],
                     measured.get("true_peak_dbfs", float("nan")),
                     measured.get("loudness_range_lu", float("nan")),
                     measured.get("measured_window", "full"),
                     (manifest.get("assessment") or {}).get("verdict", "UNKNOWN"))
        log.info("Now run: ./content-machine run %s", video_id)
        return StageResult(True, 0, "audio composed", {"output_path": str(output_path)})


def _audio_plan_digest(plan):
    """Fingerprint of the audio plan a judgement was made about.

    A human listened to a specific track. Re-rendering the same plan
    reproduces it; changing the plan does not, and silently keeping the old
    verdict would be claiming somebody approved audio they never heard.
    """
    blob = json.dumps(plan, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(blob).hexdigest()


def cmd_audio(args):
    return run_audio(args.video_id, duration=args.duration).exit_code


def run_visuals(video_id, prompt=None, negative=None, count=None, width=None,
                 height=None, seed=None, model=None, style=None, depicted=False):
    """Generate this project's images through the provider router.

    Deliberately mirrors run_audio: the assets are what we are about to
    create, so full project validation cannot pass yet.

    The provider actually used is recorded in provenance, and
    ``production_grade`` is never set to true here. A machine can establish
    that an image is procedural (and therefore not production-grade); it
    cannot establish the opposite. Affirming a real asset stays a human act,
    which is what keeps READY_FOR_REVIEW a meaningful boundary.
    """
    with project_lock(video_id):
        pdir = project_dir(video_id)
        spec_path = pdir / "video_spec.json"
        metadata_path = pdir / "metadata.json"
        if not spec_path.is_file() or not metadata_path.is_file():
            log.error("Not a project (missing video_spec.json/metadata.json): %s", pdir)
            return StageResult(False, 1, f"not a project: {video_id}")

        spec_raw = json.loads(spec_path.read_text())
        metadata = json.loads(metadata_path.read_text())
        plan = (metadata.get("visual_plan") or {})

        prompt = prompt or plan.get("prompt")
        if not prompt:
            log.error("No prompt. Pass --prompt or set metadata.visual_plan.prompt")
            return StageResult(False, 1, "no prompt")

        # Whether depicted imagery is mandatory is a property of the concept, not
        # a flag the caller guesses at. If the concept says procedural plates are
        # unacceptable, abstract-only providers are not candidates - otherwise we
        # would generate assets the publication gate is guaranteed to reject.
        concept_id = (metadata.get("experiment") or {}).get("concept_id")
        concept = _load_concept(concept_id) if concept_id else None
        if depicted:
            require_depicted = True
        elif concept is not None:
            require_depicted = not concept.get("procedural_visuals_acceptable", False)
        else:
            require_depicted = False

        request = generation.GenerationRequest(
            prompt=prompt,
            negative_prompt=negative or plan.get("negative_prompt"),
            width=width or spec_raw.get("width", 1920),
            height=height or spec_raw.get("height", 1080),
            count=count or plan.get("count", 1),
            seed=seed if seed is not None else plan.get("seed", 20260827),
            model=model or plan.get("model"),
            style=style or plan.get("style", "deep-night"),
            require_depicted=require_depicted,
        )

        if require_depicted:
            log.info("concept requires depicted imagery; abstract-only providers excluded")
        router = generation.Router()
        try:
            job = router.generate(request, pdir / "images")
        except generation.GenerationError as e:
            for attempt in e.attempts:
                log.info("  - %s: %s - %s", attempt["provider"], attempt["outcome"],
                         attempt.get("detail", ""))
            if require_depicted:
                queued = _defer_to_gpu_worker(request, pdir / "images", video_id,
                                              label=prompt[:80])
                blocked = _remote_job_blocked(queued)
                if queued and not blocked:
                    metadata.setdefault("status", {})["visuals"] = VISUALS_WAITING_FOR_GPU
                    save_metadata(pdir, metadata)
                    return _waiting_for_gpu_result([queued], "visuals")
                if blocked:
                    log.error(blocked)
            metadata.setdefault("status", {})["visuals"] = "FAILED"
            save_metadata(pdir, metadata)
            log.error("Visual generation failed: %s", e)
            if require_depicted:
                log.error("Bring ComfyUI online (COMFYUI_URL), enroll a GPU worker "
                          "(./content-machine worker enroll), or configure an image API.")
            return StageResult(False, 1, "visual generation failed")

        provider = job["provider"]
        depicted = job.get("produces_depicted", False)
        metadata.setdefault("status", {})["visuals"] = "OK"
        images_prov = metadata.setdefault("provenance", {}).setdefault("images", {})
        # A production-grade claim is about specific assets. If this run produced
        # different ones, any earlier claim no longer describes what is on disk,
        # so it is cleared rather than inherited. A reused job means the assets
        # are unchanged, and an existing claim still stands.
        new_assets = images_prov.get("job_id") != job["job_id"]
        images_prov.update({
            "provider": provider,
            "model": job.get("model"),
            "job_id": job["job_id"],
            "provider_job_id": job.get("provider_job_id"),
            "generated_at": job.get("completed_at"),
            "cost_usd": job.get("cost_usd"),
            "notes": job.get("notes") or f"generated via {provider}",
        })
        if not depicted:
            # An abstract-only provider is decisive evidence AGAINST production
            # grade, so record it. The reverse is not inferable: a machine cannot
            # certify that an image is a good asset, so a depicted provider leaves
            # the claim for a human and review stays blocked until they make it.
            images_prov["production_grade"] = False
        elif new_assets:
            images_prov["production_grade"] = None
        save_metadata(pdir, metadata)

        log.info("Visuals: %d asset(s) via %s (job %s)",
                 len(job["assets"]), provider, job["job_id"])
        for asset in job["assets"]:
            log.info("  %s", asset)
        if not depicted:
            log.warning("%s produces abstract plates, not depicted imagery; "
                        "production_grade recorded as false.", provider)
        elif images_prov.get("production_grade") is not True:
            log.info("Set provenance.images.production_grade once you have "
                     "reviewed these assets; review is blocked until you do.")
        log.info("Now run: ./content-machine run %s", video_id)
        return StageResult(True, 0, "visuals generated", {"job_id": job["job_id"]})


def cmd_visuals(args):
    return run_visuals(
        args.video_id, prompt=args.prompt, negative=args.negative, count=args.count,
        width=args.width, height=args.height, seed=args.seed, model=args.model,
        style=args.style, depicted=args.depicted).exit_code


def run_storyboard(video_id, niche=None, scene_count=None, source_width=None,
                    source_height=None, force=False):
    """Derive this project's scene plan from its script and format profile.

    Editorial planning only, exactly like run_creative: it writes the plan
    and a summary into metadata, and touches no gate input. In particular it
    never sets provenance.images.production_grade.
    """
    with project_lock(video_id):
        pdir = project_dir(video_id)
        spec_path = pdir / "video_spec.json"
        metadata_path = pdir / "metadata.json"
        if not spec_path.is_file() or not metadata_path.is_file():
            log.error("Not a project (missing video_spec.json/metadata.json): %s", pdir)
            return StageResult(False, 1, f"not a project: {video_id}")

        spec_raw = json.loads(spec_path.read_text())
        metadata = json.loads(metadata_path.read_text())
        concept_id = (metadata.get("experiment") or {}).get("concept_id")
        concept = _load_concept(concept_id) if concept_id else None

        profile = None
        if niche:
            profile = research.load_profile(niche)
            if profile is None:
                log.warning("No format profile for niche %r; using defaults. "
                            "Build one with: ./content-machine research profile "
                            "--niche %s", niche, niche)

        directives = research.load_directives(video_id) or {}
        try:
            board = storyboard_mod.build_storyboard(
                video_id, metadata, spec_raw, profile=profile,
                scene_count=scene_count,
                source_width=source_width, source_height=source_height,
                directives=directives.get("values"))
        except storyboard_mod.StoryboardError as e:
            log.error("Storyboard could not be built:")
            for problem in e.problems:
                log.error("  - %s", problem)
            return StageResult(False, 1, "storyboard could not be built")

        # Carry forward images already generated for an identical request. The
        # request digest is the same key the generation job store uses, so a
        # rebuild that does not change a scene keeps its GPU work.
        existing = storyboard_mod.load(video_id)

        # Which scenes depict what, and the identity every scene shares.
        # The gating is unchanged: a concept whose product *is* an abstract
        # plate (procedural_visuals_acceptable) deliberately holds one
        # picture, and nothing here second-guesses that. What changed is
        # what the other two branches produce - a compiled prompt carrying
        # the video's palette, light, materials, camera and framing, rather
        # than a free-text base prompt with a fragment stapled on.
        environments = None
        motifs = None
        if concept and concept.get("requires_subject_research"):
            subject = subject_research.load_subject_research(video_id)
            if subject is None:
                log.error(
                    "%s requires source-backed subject research, and none is "
                    "cached. Run `./content-machine research %s` first - a "
                    "storyboard must not invent scene visuals from model memory.",
                    concept_id, video_id)
                return StageResult(False, 1, "subject research required first")
            if existing and existing.get("scene_motifs") and not force:
                motifs = existing["scene_motifs"]
                log.info("Reusing %d cached scene motif(s)", len(motifs))
            else:
                try:
                    motifs = creative_mod.generate_scene_motifs(
                        concept, subject, board["scenes"],
                        base_prompt=board.get("visual_plan_prompt", ""))
                except creative_mod.CreativeError as e:
                    log.error("Scene motif generation failed: %s", e)
                    return StageResult(False, 1, "scene motif generation failed")
            # Facts-grounded: what each scene depicts was decided by sourced
            # research, so those motifs *are* the environments, one per
            # scene. The direction contributes only the identity around them.
            environments = [{"slug": scene["scene_id"],
                             "description": motifs[scene["scene_id"]]}
                            for scene in board["scenes"]
                            if motifs.get(scene["scene_id"])]
        elif concept and not concept.get("procedural_visuals_acceptable", False):
            # No sourced facts to depict, but the concept still needs real
            # imagery: reusing one fixed base prompt across every scene is
            # exactly the generic/repetitive-visuals failure this branch
            # exists to avoid.
            pass

        # A direction is compiled whenever this video has more than one shot,
        # including for procedural plates: the plate generator varies what it
        # draws per distinct prompt, so one prompt repeated across two hundred
        # scenes is two hundred copies of the same picture. The single-shot
        # case - the deliberately held frame - still gets no direction.
        if concept and (environments is not None
                        or not concept.get("procedural_visuals_acceptable", False)
                        or (len(board["scenes"]) > 1 and not holds_one_frame(concept))):
            direction = _visual_direction(
                video_id, metadata, concept, board, spec_raw,
                environments=environments, force=force)
            if direction and visual_direction.is_usable(direction):
                style = generation.Router().prompt_style(
                    generation.GenerationRequest(prompt="", require_depicted=True))
                prompts, negative, plan = visual_direction.compile_scene_prompts(
                    direction, board["scenes"], style=style,
                    base_negative=(metadata.get("visual_plan") or {}).get("negative_prompt"),
                    max_distinct=distinct_image_budget())
                by_slug = {env["slug"]: env for env in direction["environments"]}
                intents = {entry["scene_id"]: by_slug[entry["environment"]]["description"]
                           for entry in plan}
                storyboard_mod.apply_scene_prompts(board, prompts, negative, intents)
                board["visual_direction"] = direction
                board["shot_plan"] = plan
                board["prompt_style"] = style
                board["scene_motifs"] = intents
                log.info("Visual direction: %d environment(s), %d distinct "
                         "picture(s) across %d scene(s), %r prompt dialect",
                         len(direction["environments"]),
                         visual_direction.distinct_prompt_count(prompts),
                         len(board["scenes"]), style)
                if direction.get("slop_removed"):
                    log.info("  stripped generic prompt vocabulary: %s",
                             ", ".join(direction["slop_removed"]))
            elif motifs:
                # No usable direction, but facts-grounded motifs still beat
                # one prompt repeated for every scene.
                storyboard_mod.apply_scene_motifs(board, motifs)
                board["scene_motifs"] = motifs
            else:
                log.warning("No visual direction and no motifs: every scene "
                            "will reuse this project's base prompt.")

        if existing and not force:
            by_digest = {
                (scene.get("generation") or {}).get("request_digest"): scene
                for scene in existing.get("scenes", []) if scene.get("image")
            }
            reused = 0
            for scene in board["scenes"]:
                prior = by_digest.get(scene["generation"]["request_digest"])
                if prior:
                    scene["image"] = prior["image"]
                    scene["generation"]["job_id"] = (prior.get("generation") or {}).get("job_id")
                    scene["generation"]["provider"] = (prior.get("generation") or {}).get("provider")
                    reused += 1
            if reused:
                log.info("Reused %d already-generated scene image(s)", reused)

        path = storyboard_mod.save(video_id, board)
        metadata["scenes"] = storyboard_mod.scene_summary(board)
        metadata.setdefault("status", {})["storyboard"] = "OK"
        save_metadata(pdir, metadata)
        write_research_influence(video_id, pdir, directives, board)

        log.info("Storyboard: %d scene(s), %.2fs timeline (target %.2fs)",
                 len(board["scenes"]), board["timeline_seconds"],
                 board["target"]["duration_seconds"])
        log.info("Sources generated at %dx%d, output %dx%d (scaled at render time, "
                 "not generated at output size)",
                 board["source_generation"]["width"], board["source_generation"]["height"],
                 board["target"]["width"], board["target"]["height"])
        log.info("Written: %s", path)
        log.info("Now run: ./content-machine scenes %s", video_id)
        return StageResult(True, 0, "storyboard written", {"path": str(path)})


# How many genuinely different pictures one video pays for, however many
# shots it is cut into. Tunable because the right number depends on what is
# generating them: a GPU making 200 distinct plates is an afternoon, the
# procedural generator making 40 is a minute.
DEFAULT_DISTINCT_IMAGE_BUDGET = 40


def distinct_image_budget():
    raw = os.environ.get("CM_DISTINCT_IMAGE_BUDGET", "").strip()
    try:
        value = int(raw)
    except ValueError:
        return DEFAULT_DISTINCT_IMAGE_BUDGET
    return max(value, 1)


def write_research_influence(video_id, pdir, directives, board=None):
    """Record what sourced research actually changed about this production.

    Three separate things, deliberately not collapsed: what research
    *suggested* (every directive, with its evidence), what the build
    *applied* (which is smaller - a directive can be bounded or overridden),
    and what it could not reach. The dashboard shows this so "research-driven"
    is a claim a person can check rather than one the pipeline makes about
    itself.
    """
    applied = dict((board or {}).get("research_applied") or {})
    record = {
        "video_id": video_id,
        "recorded_utc": utc_now(),
        # Whether research ran at all. "Research found nothing that changes a
        # parameter" and "research never ran" are different states, and a
        # reader who cannot tell them apart will believe the first when the
        # second is true.
        "researched": research.load_findings(video_id) is not None,
        "findings_researched_utc": (directives or {}).get("findings_researched_utc"),
        "decisions": (directives or {}).get("decisions", []),
        "applied": applied,
        "suggested_not_applied": sorted(
            set((directives or {}).get("values", {})) - set(applied)
            - {"scene_count", "typical_duration_seconds"}),
        "scene_count": len((board or {}).get("scenes") or []) or None,
        "timeline_seconds": (board or {}).get("timeline_seconds"),
    }
    (pdir / "research_influence.json").write_text(json.dumps(record, indent=2) + "\n")
    return record

def _visual_direction(video_id, metadata, concept, board, spec_raw,
                      direction=None, environments=None, force=False):
    """This project's visual direction document, cached in its metadata.

    One LLM call per video, reused on every later storyboard rebuild unless
    forced. Fail-soft in one direction only: with no direction the caller
    still has the old motif path, so the failure mode is "less directed",
    never "no storyboard" and never an invented environment.

    ``environments`` overrides the document's own settings when the concept
    is fact-grounded: what those scenes depict was decided by sourced
    research, and the direction supplies only the identity around them.
    """
    visual_plan = metadata.setdefault("visual_plan", {})
    if direction is None and not force:
        direction = visual_plan.get("direction")
    if direction is None:
        try:
            direction = creative_mod.generate_visual_direction(
                concept, len(board["scenes"]),
                brief={"image_prompt": visual_plan.get("prompt")},
                research_brief=research.load_brief(video_id),
                findings=research.load_findings(video_id),
                target_seconds=spec_raw.get("duration_seconds"))
        except Exception as e:  # noqa: BLE001 - recorded, never fatal
            log.warning("Visual direction pass unavailable (%s); falling back "
                        "to the project's base prompt.", e)
            return None
        visual_plan["direction"] = direction
    if environments:
        direction = dict(direction, environments=[
            visual_direction.sanitize_direction({"environments": environments})
            ["environments"]][0])
    return direction


def cmd_storyboard(args):
    return run_storyboard(
        args.video_id, niche=args.niche, scene_count=args.scenes,
        source_width=args.source_width, source_height=args.source_height,
        force=args.force).exit_code


def run_scenes(video_id, force=False, depicted=False):
    """Generate (or reuse) one image per storyboard scene.

    Every image goes through the same generation.Router as `visuals`, at the
    scene's own source resolution, so the digest, the job store, the reuse
    seam and the provenance rules are the ones already in place. This adds a
    plan for *which* images to make; it does not add a second way to make one.
    """
    with project_lock(video_id):
        pdir = project_dir(video_id)
        board = storyboard_mod.load(video_id)
        if board is None:
            log.error("No storyboard for %s. Build one first: "
                      "./content-machine storyboard %s", video_id, video_id)
            return StageResult(False, 1, "no storyboard")

        metadata_path = pdir / "metadata.json"
        metadata = json.loads(metadata_path.read_text())
        concept_id = (metadata.get("experiment") or {}).get("concept_id")
        concept = _load_concept(concept_id) if concept_id else None
        if depicted:
            require_depicted = True
        elif concept is not None:
            require_depicted = not concept.get("procedural_visuals_acceptable", False)
        else:
            require_depicted = False
        if require_depicted:
            log.info("concept requires depicted imagery; abstract-only providers excluded")

        router = generation.Router()
        scene_dir = pdir / "images"
        scene_dir.mkdir(parents=True, exist_ok=True)
        quality_attempts = _scene_quality_attempts()

        generated, reused, failed, queued = 0, 0, [], []
        providers_used = set()
        any_abstract = False
        # Two scenes deliberately assigned the same environment share a
        # request digest, so the first render satisfies both. Serving the
        # second from this map (rather than from the job store a moment
        # later) keeps the "reused" count honest: it was one generation.
        generated_by_digest = {}
        for scene in board["scenes"]:
            if scene.get("image") and not force:
                reused += 1
                continue
            request = storyboard_mod.scene_request(scene, require_depicted=require_depicted)
            shared = generated_by_digest.get(scene["generation"]["request_digest"])
            if shared:
                scene["image"] = shared["image"]
                scene["generation"].update(shared["generation"])
                reused += 1
                continue
            try:
                job = router.generate(request, scene_dir)
            except generation.GenerationError as e:
                remote = None
                if require_depicted:
                    remote = _defer_to_gpu_worker(
                        request, scene_dir, video_id,
                        label=f"{scene['scene_id']}: {request.prompt[:60]}")
                if remote:
                    scene.setdefault("generation", {}).update({
                        "job_id": remote["job_id"], "remote_state": remote["state"]})
                    blocked = _remote_job_blocked(remote)
                    if blocked:
                        failed.append((scene["scene_id"], blocked))
                    else:
                        queued.append(remote)
                else:
                    failed.append((scene["scene_id"], str(e)))
                continue
            assets = job.get("assets") or []
            if not assets:
                failed.append((scene["scene_id"], "provider returned no assets"))
                continue
            asset = Path(assets[0])

            # Look at what came back before accepting it. A flat fill or a
            # black frame is not a picture, and an unattended run that keeps
            # the first asset regardless is the reason "the pipeline
            # succeeded" and "the video is watchable" drifted apart. A
            # re-roll is a new seed, so it is a genuinely different image
            # rather than the same request repeated.
            assessment = qc.assess_image(asset)
            attempt = 1
            while assessment["verdict"] == "BLOCKED" and attempt < quality_attempts:
                log.warning("%s attempt %d rejected: %s", scene["scene_id"], attempt,
                            "; ".join(f["detail"] for f in assessment["findings"]
                                      if f["severity"] == "block"))
                request.seed = request.seed + _SCENE_RESEED_STEP * attempt
                try:
                    job = router.generate(request, scene_dir)
                except generation.GenerationError as e:
                    log.warning("%s re-roll could not be generated: %s",
                                scene["scene_id"], e)
                    break
                retry_assets = job.get("assets") or []
                if not retry_assets:
                    break
                asset = Path(retry_assets[0])
                assessment = qc.assess_image(asset)
                attempt += 1
            assessment["attempts"] = attempt
            if assessment["verdict"] == "BLOCKED":
                failed.append((scene["scene_id"],
                               "; ".join(f["detail"] for f in assessment["findings"]
                                         if f["severity"] == "block")
                               + f" (after {attempt} attempt(s))"))
                continue
            if attempt > 1:
                log.info("%s accepted on attempt %d", scene["scene_id"], attempt)

            try:
                relative = asset.relative_to(pdir)
            except ValueError:
                relative = asset
            scene["image"] = str(relative)
            scene["generation"].update({
                "job_id": job["job_id"],
                "provider": job["provider"],
                "provider_job_id": job.get("provider_job_id"),
                "generated_utc": job.get("completed_at"),
                "produces_depicted": job.get("produces_depicted", False),
                "seed": request.seed,
                "quality": assessment,
            })
            generated_by_digest[scene["generation"]["request_digest"]] = {
                "image": scene["image"], "generation": dict(scene["generation"])}
            providers_used.add(job["provider"])
            if not job.get("produces_depicted", False):
                any_abstract = True
            generated += 1

        # Two scenes that asked for different pictures and got the same
        # one is a variation failure the reviewer would otherwise only find
        # by looking. Advisory, not fatal: it is a matter of degree, and
        # the deliberate-reuse case is excluded by request digest.
        duplicates = qc.duplicate_scene_findings(board["scenes"], pdir)
        board["image_quality"] = {
            "checked_utc": utc_now(),
            "attempts_allowed": quality_attempts,
            "duplicate_findings": duplicates,
        }
        for finding in duplicates:
            log.warning("Scene variation: %s", finding["detail"])

        storyboard_mod.save(video_id, board)
        metadata["scenes"] = storyboard_mod.scene_summary(board)

        if providers_used:
            images_prov = metadata.setdefault("provenance", {}).setdefault("images", {})
            previous_scene_jobs = images_prov.get("scene_job_ids")
            scene_jobs = sorted(
                (s.get("generation") or {}).get("job_id")
                for s in board["scenes"] if (s.get("generation") or {}).get("job_id"))
            images_prov.update({
                "provider": ", ".join(sorted(providers_used)),
                "scene_job_ids": scene_jobs,
                # The assets the deliverable is built from, named
                # explicitly so a retry or an abandoned earlier render
                # sitting in images/ is never mistaken for one of them.
                "scene_images": sorted(
                    {s["image"] for s in board["scenes"] if s.get("image")}),
                "notes": f"{len(scene_jobs)} scene image(s) via "
                         f"{', '.join(sorted(providers_used))}",
                "source_generation": {
                    "width": board["source_generation"]["width"],
                    "height": board["source_generation"]["height"],
                },
            })
            if any_abstract:
                # Decisive evidence against production grade, same rule as
                # run_visuals. The positive is never inferable by a machine.
                images_prov["production_grade"] = False
            elif previous_scene_jobs != scene_jobs:
                images_prov["production_grade"] = None
        save_metadata(pdir, metadata)

        log.info("Scenes: %d generated, %d reused, %d queued for the GPU worker, %d failed",
                 generated, reused, len(queued), len(failed))
        for scene_id, detail in failed:
            log.error("  %s: %s", scene_id, detail)
        if failed:
            log.error("Unresolved scenes block the render. A queued GPU job is "
                      "not a failure - it simply has not landed yet.")
            return StageResult(False, 1, "unresolved scenes",
                                {"generated": generated, "reused": reused,
                                 "failed": len(failed), "queued": len(queued)})
        if queued:
            metadata.setdefault("status", {})["scenes"] = VISUALS_WAITING_FOR_GPU
            save_metadata(pdir, metadata)
            return _waiting_for_gpu_result(queued, "scenes",
                                           {"generated": generated, "reused": reused})
        if any_abstract:
            log.warning("Some scenes came from an abstract-only provider; "
                        "production_grade recorded as false.")
        log.info("Now run: ./content-machine run %s", video_id)
        return StageResult(True, 0, "scenes generated",
                            {"generated": generated, "reused": reused})


VISUALS_WAITING_FOR_GPU = "WAITING_FOR_GPU_WORKER"
WAITING_FOR_GPU_EXIT_CODE = 2   # NEEDS_ATTENTION in the web layer, never FAILED


def _defer_to_gpu_worker(request, out_dir, video_id, label=None):
    """Queue a depicted-imagery request for the remote GPU, or None.

    Only reached once every synchronous provider has declined the request.
    The remote worker is a queue, not a provider (knowledge: Remote GPU Work
    Is a Queue Not a Provider): the PC being off is normal, so the job waits
    there at no cost and the next run of this stage finds the completed job
    through the ordinary digest seam and reuses it. Nothing is queued when
    no worker was ever enrolled - then the failure stays a failure.
    """
    import worker  # local import: worker imports this module's siblings, not vice versa

    try:
        if not worker.remote_capable(("comfyui",)):
            return None
        job = worker.enqueue(request, out_dir, capabilities=("comfyui",),
                             project_id=video_id, prompt_label=label)
    except worker.WorkerError as e:
        log.warning("could not queue for the GPU worker: %s", e)
        return None
    view = worker.job_view(job)
    if view["state"] in worker.REQUEUABLE:
        log.warning("GPU job %s already exists and is %s - not re-queued: %s",
                    view["job_id"], view["state"], _remote_job_blocked(view))
    else:
        log.info("queued for the GPU worker: job %s (%s)", view["job_id"],
                 view.get("wait_reason") or view["state"])
    return view


def _remote_job_blocked(view):
    """Why an existing remote job cannot deliver, or None while it still can.

    ``worker.enqueue`` is idempotent on the digest, so a stage that asks
    again for a render whose job already FAILED (or was CANCELLED) gets that
    record back. Treating it as "queued" would report a scene as on its way
    when nothing will ever move it; treating it as a stage failure that
    names the requeue action keeps the decision to try again explicit - an
    operator's, in the CLI or the Control Center - rather than a silent
    retry every time Produce runs.
    """
    import worker

    if not view or view["state"] not in worker.REQUEUABLE:
        return None
    category = view.get("failure_category")
    why = view.get("error") or view["state"].lower()
    tag = f" [{category}]" if category else ""
    return (f"GPU job {view['job_id']} is {view['state']}{tag}: {why} - "
            f"requeue it (./content-machine worker requeue {view['job_id']}, "
            "or Retry in the Control Center) and re-run")


def _waiting_for_gpu_result(jobs, stage, extra=None):
    """The StageResult for 'the images are on their way, not here yet'.

    Exit code 2 so the web layer records NEEDS_ATTENTION rather than FAILED:
    nothing went wrong, and re-running the stage (or Produce) once the jobs
    land resumes from the completed jobs without regenerating anything.
    """
    import worker

    readiness = worker.depicted_readiness()
    reasons = sorted({j.get("wait_reason") or j["state"] for j in jobs})
    message = (f"waiting for GPU worker: {len(jobs)} {stage} job(s) queued "
               f"({', '.join(reasons)}); {readiness['detail']}. Re-run Produce "
               "once the images land.")
    log.warning(message)
    data = {"waiting_for_gpu": True, "remote_jobs": [j["job_id"] for j in jobs],
            "gpu_state": readiness["state"], "gpu_detail": readiness["detail"]}
    data.update(extra or {})
    return StageResult(False, WAITING_FOR_GPU_EXIT_CODE, message, data)


def record_visual_grade(video_id, reviewer, production_grade, notes=""):
    """A human's explicit claim about the project's visuals.

    The ONLY writer of ``provenance.images.production_grade`` outside the
    machine-established negative in run_visuals/run_scenes and the CLI's
    explicit flags. ``reviewer`` must be a human identity (the web layer
    sources it from the session, the CLI from a required flag); an
    automated caller cannot grant it. ``True`` is a claim, not a proof: the
    gate still re-inspects the artefacts (a procedural plate stays a
    blocker whatever this says), so the strongest thing a wrong claim can
    do is nothing.
    """
    if production_grade not in (True, False):
        raise ReviewDecisionError("production_grade must be true or false")
    if not reviewer or not reviewer.strip():
        raise ReviewDecisionError("reviewer is required and must be a human identity")
    with project_lock(video_id):
        pdir = project_dir(video_id)
        meta_path = pdir / "metadata.json"
        if not meta_path.is_file():
            raise ReviewDecisionError(f"not a project: {video_id}")
        metadata = json.loads(meta_path.read_text())
        images = list_assets(pdir / "images", SUPPORTED_IMAGE_EXTENSIONS)
        if production_grade and not images:
            raise ReviewDecisionError(
                "cannot claim production-grade visuals: the project has no images")
        prov = metadata.setdefault("provenance", {}).setdefault("images", {})
        prov["production_grade"] = production_grade
        prov["production_grade_claim"] = {
            "utc": utc_now(), "reviewer": reviewer, "notes": notes or "",
            "asset_count": len(images),
        }
        save_metadata(pdir, metadata)
        return dict(prov["production_grade_claim"], production_grade=production_grade)


def record_audio_grade(video_id, reviewer, production_grade, notes=""):
    """A human's explicit claim about the project's audio.

    The counterpart to ``record_visual_grade``, and the only way a track
    whose source cannot be production-grade (see ``route_audio``) reaches
    review. It is deliberately not required for every project: where a
    synthesised texture *is* the product, the artefact is exactly what was
    specified and measurable, so demanding a ceremonial claim would teach
    people to click through one. It is required precisely where a machine
    cannot answer the question - whether synthesised music is music anyone
    would want to listen to.

    The claim is bound to the audio plan that was rendered
    (``plan_digest``), so re-rendering the same plan keeps it and changing
    the plan drops it: nobody is recorded as having approved audio they
    never heard.
    """
    if production_grade not in (True, False):
        raise ReviewDecisionError("production_grade must be true or false")
    if not reviewer or not reviewer.strip():
        raise ReviewDecisionError("reviewer is required and must be a human identity")
    with project_lock(video_id):
        pdir = project_dir(video_id)
        meta_path = pdir / "metadata.json"
        if not meta_path.is_file():
            raise ReviewDecisionError(f"not a project: {video_id}")
        metadata = json.loads(meta_path.read_text())
        tracks = list_assets(pdir / "audio", SUPPORTED_AUDIO_EXTENSIONS)
        if production_grade and not tracks:
            raise ReviewDecisionError(
                "cannot claim production-grade audio: the project has no audio track")
        prov = metadata.setdefault("provenance", {}).setdefault("audio", {})
        prov["production_grade"] = production_grade
        prov["graded_by"] = reviewer
        prov["graded_utc"] = utc_now()
        prov["grade_notes"] = notes or ""
        save_metadata(pdir, metadata)
        return {"production_grade": production_grade, "reviewer": reviewer,
                "utc": prov["graded_utc"], "notes": prov["grade_notes"]}


def cmd_audio_grade(args):
    try:
        entry = record_audio_grade(args.video_id, args.reviewer,
                                   args.grade == "true", notes=args.notes or "")
    except ReviewDecisionError as e:
        log.error("%s", e)
        return 1
    log.info("provenance.audio.production_grade=%s recorded by %s",
             entry["production_grade"], entry["reviewer"])
    return 0


def cmd_visual_grade(args):
    try:
        entry = record_visual_grade(args.video_id, args.reviewer,
                                    args.grade == "true", notes=args.notes or "")
    except ReviewDecisionError as e:
        log.error("%s", e)
        return 1
    log.info("provenance.images.production_grade=%s recorded by %s",
             entry["production_grade"], entry["reviewer"])
    return 0


# A re-roll must land somewhere genuinely different in the model's latent
# space, not one seed over, and must stay deterministic so a rerun of the
# same project reproduces the same images. A large fixed prime step does
# both.
_SCENE_RESEED_STEP = 7919


def _scene_quality_attempts():
    """How many times a scene may be generated before giving up on it.

    One by default plus one re-roll: enough to shake off a genuinely bad
    draw, few enough that a systematically broken prompt fails fast instead
    of burning GPU time proving the same point ten times.
    """
    try:
        return max(1, int(os.environ.get("SCENE_QUALITY_ATTEMPTS", "2")))
    except ValueError:
        return 2


def cmd_scenes(args):
    return run_scenes(args.video_id, force=args.force, depicted=args.depicted).exit_code


def cmd_providers(args):
    """Report which generation providers are reachable right now."""
    router = generation.Router()
    request = generation.GenerationRequest(prompt="", require_depicted=args.depicted)
    log.info("routing order: %s", " -> ".join(router.order))
    for entry in router.status(request):
        flags = []
        if not entry["configured"]:
            flags.append("unconfigured")
        if entry["costs_money"]:
            flags.append("COSTS MONEY")
        if not entry["produces_depicted"]:
            flags.append("abstract only")
        if entry["in_cooldown"]:
            flags.append(f"cooldown {entry['cooldown_remaining_seconds']:.0f}s")
        suffix = f"  [{', '.join(flags)}]" if flags else ""
        print(f"{'OK  ' if entry['healthy'] else 'DOWN'}  "
              f"{entry['provider']:<12} {entry['detail']}{suffix}")
    search = subject_research.provider_status()
    if search["available"]:
        print(f"OK    {'search':<12} SEARCH_PROVIDER={search['configured']}  [subject research]")
    else:
        detail = (f"SEARCH_PROVIDER={search['configured']!r} is not a known provider"
                  if search["configured"] else "SEARCH_PROVIDER is not set")
        print(f"DOWN  {'search':<12} {detail}  [subject research fails closed; "
              f"known: {', '.join(search['known'])}]")
    return 0


def attach_log_file(pdir):
    log_path = pdir / "logs" / f"run_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"
    handler = logging.FileHandler(log_path)
    handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s"))
    logging.getLogger().addHandler(handler)
    return log_path, handler


def thumbnail_timestamps(board, duration):
    """Where to grab thumbnail candidates from.

    Fixed fractions of the runtime are only a proxy for "a different
    picture". Now that a deliberately repeated environment is one render
    reused across several scenes, 0.25/0.5/0.75 can land on the same image
    three times and hand the reviewer three copies of one thumbnail. Where a
    storyboard exists, sample the middle of the first scene showing each
    distinct image instead, so every candidate is a genuinely different
    frame; fall back to the fractions when there is no storyboard to read.
    """
    scenes = (board or {}).get("scenes") or []
    stamps = []
    seen = set()
    for scene, start in zip(scenes, motion_mod.scene_start_times(scenes)):
        image = scene.get("image")
        if not image or image in seen:
            continue
        seen.add(image)
        middle = start + float(scene.get("duration_seconds") or 0.0) / 2.0
        if 0.0 <= middle < duration:
            stamps.append(round(middle, 3))
        if len(stamps) >= MAX_THUMBNAIL_CANDIDATES:
            break
    return stamps or [max(duration * p, 0.0) for p in THUMBNAIL_POSITIONS]


def extract_thumbnails(video_path, thumb_dir, duration, board=None):
    """Pull candidate thumbnails from the finished video (local, no AI)."""
    candidates = []
    for index, timestamp in enumerate(thumbnail_timestamps(board, duration), start=1):
        out = thumb_dir / f"candidate_{index}.jpg"
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
    Concepts derived from an operator goal live in experiments/derived/ and
    are looked up there first - they are per-production, so they would only
    pollute the curated catalogue.
    """
    if not concept_id:
        return None
    derived = goal_mod.load_derived_concept(concept_id)
    if derived is not None:
        return derived
    if not CONCEPTS_PATH.is_file():
        return None
    try:
        data = json.loads(CONCEPTS_PATH.read_text())
    except (json.JSONDecodeError, OSError):
        return None
    return next((c for c in data.get("concepts", []) if c.get("id") == concept_id), None)


def scene_referenced_images(pdir):
    """The image files this project's storyboard actually uses, or ``None``
    when there is no storyboard to say.

    ``images/`` accumulates more than the deliverable: a failed attempt that
    still wrote a file, an image from an earlier `visuals` run, a scene
    regenerated under a new prompt. Those are working residue, not assets
    anyone chose to publish, and treating them as part of the deliverable
    both misreports what was made and lets an abandoned placeholder block a
    project whose real scenes are fine.
    """
    board = storyboard_mod.load(pdir.name)
    if not board or not board.get("scenes"):
        # A catalog import already carries an explicit edit in the render spec.
        # The gate must inspect those sources even without a generated storyboard.
        spec_path = pdir / "video_spec.json"
        board = json.loads(spec_path.read_text()) if spec_path.is_file() else {}
        if not board.get("scenes"):
            return None
    referenced = []
    for scene in board["scenes"]:
        rel = scene.get("image")
        if not rel:
            continue
        path = (pdir / rel) if not Path(rel).is_absolute() else Path(rel)
        if path.is_file() and path not in referenced:
            referenced.append(path)
    return referenced


def unreferenced_images(pdir):
    """Image files on disk that no storyboard scene points at."""
    referenced = scene_referenced_images(pdir)
    if referenced is None:
        return []
    chosen = {p.resolve() for p in referenced}
    return sorted(p for p in list_assets(pdir / "images", SUPPORTED_IMAGE_EXTENSIONS)
                  if p.resolve() not in chosen)


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

    # The concept demands depicted imagery. Inspect the assets themselves -
    # the ones the deliverable is actually built from, not every file that
    # has ever landed in images/.
    offenders = []
    candidates = scene_referenced_images(pdir)
    if candidates is None:
        candidates = list_assets(pdir / "images", SUPPORTED_IMAGE_EXTENSIONS)
    for image in sorted(candidates):
        kind, detail = make_visuals.classify(image)
        if kind in ("procedural", "flat"):
            offenders.append(f"{image.name} ({kind}: {detail})")
    if offenders:
        blocking.append(
            f"concept '{concept_id}' requires depicted imagery "
            f"(procedural_visuals_acceptable=false), but these assets are "
            f"not depicted imagery: {'; '.join(offenders)}")
    return blocking


def _creative_quality_blockers(pdir, storyboard, audio_manifest):
    """Detected creative-quality defects that must gate review, kept
    distinguishable from technical validity (``qc_status`` above).

    A technically valid render (correct codec, duration, decodable assets)
    can still be an obviously unfinished product - one fixed image prompt
    reused for every scene, or a single flat unfaded audio layer. Neither is
    a QC/codec failure, so neither belongs in ``qc_status``, but both must
    still block READY_FOR_REVIEW rather than sit unread in a nested report
    while an unrelated, easily-missed administrative blocker (no
    production-grade claim yet) is the only thing standing in the way.
    Only signals with no plausible legitimate reading are used here (see
    ``qc.visual_diversity_warnings``'s docstring on trusting a deliberate,
    reasoned low environment count) - this must not veto a genuinely
    excellent minimal design, only catch the case nothing designed it at all.
    """
    blocking = []
    if storyboard is None:
        storyboard_path = pdir / "storyboard.json"
        if storyboard_path.is_file():
            storyboard = json.loads(storyboard_path.read_text())
    if storyboard:
        scenes = storyboard.get("scenes") or []
        motifs_recorded = bool(storyboard.get("scene_motifs"))
        blocking.extend(qc.visual_diversity_warnings(scenes, motifs_recorded))

    if audio_manifest:
        blocking.extend(audio_manifest.get("quality", {}).get("warnings", []))
    return blocking


def _audio_blockers(pdir, metadata, audio_manifest):
    """Gate audio that a machine cannot vouch for out of READY_FOR_REVIEW.

    Two questions, and only the second one needs a person:

    1. **Rights** - answerable from the manifest, and already fatal
       elsewhere in this gate.
    2. **Is it actually good?** - not answerable here at all. Where the
       routing chose a source that cannot be production-grade (synthesised
       music standing in for music), an explicit human judgement is
       required and its absence blocks, exactly as an absent
       production-grade claim blocks the visuals. Where the synthesised
       signal *is* the product - the brown-noise bed a sleep video ships -
       no such stand-in exists, so nothing is demanded.
    """
    audio_prov = (metadata.get("provenance") or {}).get("audio") or {}
    claimed = audio_prov.get("production_grade")
    if claimed is False:
        return [f"audio is not production-grade: "
                f"{audio_prov.get('grade_notes') or 'a human listened and said so'}"]
    if claimed is True:
        return []
    if audio_prov.get("production_grade_capable") is False:
        direction = (metadata.get("audio_plan") or {}).get("direction") or {}
        chosen = direction.get("chosen") or {}
        return [
            f"audio is {chosen.get('source', 'a synthesised stand-in')} standing in "
            f"for {direction.get('kind', 'music')}, which no check here can judge: "
            "a human has to listen and record the verdict "
            "(./content-machine audio-grade <id> --reviewer <you> --grade true|false)"]
    return []


def gate_blockers(pdir, metadata, qc_status, qc_failures, has_thumbnail,
                  audio_manifest, storyboard=None, storyboard_report=None):
    """Every rule standing between a rendered project and READY_FOR_REVIEW.

    Single source of truth, shared by ``run`` and ``status``. Keeping one
    implementation is the point: a second copy would drift from this one,
    which is precisely the failure this module now guards against.
    """
    blocking = _ingestion_problems(metadata)
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
    blocking.extend(_audio_blockers(pdir, metadata, audio_manifest))
    blocking.extend(_creative_quality_blockers(pdir, storyboard, audio_manifest))

    blocking.extend(_research_blockers(pdir))

    # Audio rights gate: an unclearable track must never reach review.
    if audio_manifest and not audio_manifest.get("commercial_use_cleared", False):
        unclear = [l.get("layer_id") for l in audio_manifest.get("layers", [])
                   if not l.get("commercial_use")]
        blocking.append(f"audio not cleared for commercial use: {unclear}")
    return blocking


def _research_blockers(pdir):
    """A production that was meant to be researched and was not.

    Scoped to projects that carry a research brief, which is every
    production started from a goal. "Nobody could research it" is not "it
    needed no research": the video can be produced and previewed, but it
    cannot pass a gate that says this is a researched production until the
    research actually ran. Running it is always available from the
    dashboard; this is what makes not running it visible.
    """
    video_id = pdir.name
    if research.load_brief(video_id) is None:
        return []
    if research.load_findings(video_id) is not None:
        return []
    return ["research has not run for this production (it carries a research "
            "brief but no findings). Run research, or remove the brief if "
            "this video is genuinely not researched."]


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
        "provenance_audio": {k: v for k, v in
                             (metadata.get("provenance", {}).get("audio", {}) or {}).items()
                             if k in ("production_grade", "production_grade_capable",
                                      "source", "plan_digest")},
        "spec": json.loads(spec_path.read_text()) if spec_path.is_file() else None,
        "images": sorted(
            (p.name, sha256(p))
            for p in list_assets(pdir / "images", SUPPORTED_IMAGE_EXTENSIONS)),
        "audio": sorted(
            (p.name, sha256(p))
            for p in list_assets(pdir / "audio", SUPPORTED_AUDIO_EXTENSIONS)),
        "video": sha256(video_path) if video_path.is_file() else None,
    }
    if metadata.get("ingestion"):
        inputs["ingestion"] = metadata["ingestion"]
        inputs["imported_bytes"] = [
            (asset["selected_path"], sha256(Path(asset["selected_path"]))
             if Path(asset["selected_path"]).is_file() else None)
            for asset in metadata["ingestion"].get("inputs", [])]
    blob = json.dumps(inputs, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(blob).hexdigest()


def status_report(video_id):
    """The pure verdict computation behind `status` - JSON-serializable.

    Answers the questions an autonomous system (or a web API) has to be able
    to answer: what is the state, was it computed from what is on disk right
    now, and what would block it if it were re-evaluated. Returns None if
    there is no such project. This is the one implementation; `cmd_status`
    and any future caller (e.g. a web API) both read it rather than
    recomputing the verdict themselves.
    """
    pdir = project_dir(video_id)
    meta_path = pdir / "metadata.json"
    if not meta_path.is_file():
        return None
    metadata = json.loads(meta_path.read_text())
    status = metadata.get("status", {})
    recorded = status.get("overall", "UNKNOWN")
    video_path = pdir / "output" / f"{video_id}.mp4"

    if not video_path.is_file():
        return {
            "video_id": video_id,
            "recorded": recorded,
            "verdict": "NOT_RENDERED",
            "blocking": [],
            "stale": None,
            "digest_state": None,
        }

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

    # Staleness answers a different question than the verdict: the verdict
    # is what holds now, staleness is whether the stored artefacts still
    # describe it.
    stored = status.get("gate_digest")
    if stored is None:
        digest_state = "ABSENT"
    elif stored != gate_digest(pdir, metadata, video_path):
        digest_state = "CHANGED"
    else:
        digest_state = "MATCHES"

    return {
        "video_id": video_id,
        "recorded": recorded,
        "verdict": verdict,
        "blocking": blocking,
        "stale": recorded != verdict,
        "digest_state": digest_state,
    }


def list_projects():
    """Summaries of every project on disk, for a dashboard/listing view.

    A pure filesystem read. One unreadable/corrupt project must not break
    the ability to list the rest of them, so a bad metadata.json is skipped
    rather than raised.
    """
    if not PROJECTS_DIR.is_dir():
        return []
    summaries = []
    for pdir in sorted(PROJECTS_DIR.iterdir()):
        summary = project_summary(pdir.name)
        if summary is not None:
            summaries.append(summary)
    return summaries


def project_summary(video_id):
    """One project's dashboard summary, or None if it is not a readable
    project. The single shape list_projects() and a freshly created
    project both report in."""
    pdir = project_dir(video_id)
    meta_path = pdir / "metadata.json"
    if not pdir.is_dir() or not meta_path.is_file():
        return None
    try:
        metadata = json.loads(meta_path.read_text())
    except (OSError, ValueError):
        return None
    status = metadata.get("status", {})
    experiment = metadata.get("experiment") or {}
    return {
        "video_id": metadata.get("video_id", pdir.name),
        "selected_title": metadata.get("selected_title"),
        "concept_id": experiment.get("concept_id"),
        "niche": experiment.get("niche"),
        "overall_status": status.get("overall", "UNKNOWN"),
        "created_utc": metadata.get("created_utc"),
        # Archived projects stay on disk, in every read model and in the
        # Review Center's history; they only leave the active views. Nothing
        # here deletes - permanent deletion is the operator's own act.
        "archived": bool(metadata.get("archived")),
        # One servable image so a library of productions can be browsed by
        # eye rather than by id. A thumbnail if the render produced one,
        # else the first scene image, else nothing - no placeholder is
        # invented, because "no picture yet" is a true and useful answer.
        "preview_image": _preview_image(pdir),
    }


def _preview_image(pdir):
    """A project-relative path to the best single still, or None.

    Deliberately prefers the extracted thumbnail: it comes from the finished
    render, so it shows what the video actually looks like rather than one
    source asset.
    """
    # output/editable-media is where an owner-media assembly stages its
    # project-local copies, so a catalog-imported production has a picture
    # too rather than looking empty.
    for directory in ("thumbnail", "images", "output/editable-media"):
        found = list_assets(pdir / directory, SUPPORTED_IMAGE_EXTENSIONS)
        if found:
            return f"{directory}/{found[0].name}"
    return None


def set_archived(video_id, archived, actor, reason=""):
    """Mark a project archived (or restore it). Never deletes anything.

    Archiving is how development residue - test projects, abandoned
    experiments, demos - leaves the active product experience without any
    file being removed: ``metadata.archived`` records who did it, when and
    why, and every list that serves an operator's active view filters on
    it. Restoring is the same act in reverse. Like a review decision it
    needs a human-attributable actor; a system default is refused.
    """
    actor = (actor or "").strip()
    if not actor:
        raise ProjectError(["archiving needs a human-attributable actor"])
    with project_lock(video_id):
        pdir = project_dir(video_id)
        meta_path = pdir / "metadata.json"
        if not meta_path.is_file():
            raise ProjectError([f"no such project: {video_id}"])
        metadata = json.loads(meta_path.read_text())
        if archived:
            metadata["archived"] = {"at": utc_now(), "by": actor,
                                    "reason": (reason or "").strip()}
        else:
            metadata.pop("archived", None)
        metadata.setdefault("history", []).append({
            "utc": utc_now(), "event": "archived" if archived else "restored",
            "by": actor, "reason": (reason or "").strip()})
        save_metadata(pdir, metadata)
    return project_summary(video_id)


# The only project subdirectories any adapter may hand a file out of. Root
# files (metadata.json, video_spec.json, storyboard.json) are deliberately
# excluded: those are read models with their own endpoints, never raw
# downloads, and nothing outside these five is a deliverable or evidence.
ASSET_DIRS = ("output", "thumbnail", "images", "audio", "logs")


def project_file_path(video_id, relative):
    """Resolve a project-relative asset path that is safe to serve.

    The one place the "which files may leave the project directory" rule
    lives, so the web API, a future MCP adapter and the CLI all refuse the
    same things: absolute paths, `..` traversal, anything outside ASSET_DIRS,
    and symlinks that resolve out of the project. Raises ProjectError with a
    single problem string; returns the resolved Path of an existing file.
    """
    raw = str(relative or "")
    if not raw or raw.startswith(("/", "\\")) or "\\" in raw:
        raise ProjectError([f"not a servable asset path: {raw!r}"])
    parts = raw.split("/")
    if any(part in ("", ".", "..") for part in parts):
        raise ProjectError([f"not a servable asset path: {raw!r}"])
    if parts[0] not in ASSET_DIRS:
        raise ProjectError([f"{parts[0]!r} is not a servable asset directory"])
    if len(parts) < 2:
        raise ProjectError([f"no such asset: {raw}"])

    pdir = project_dir(video_id)
    if not (pdir / "metadata.json").is_file():
        raise ProjectError([f"not a project: {video_id}"])
    # resolve() follows symlinks, so a link planted inside images/ that points
    # at /etc/passwd lands outside the allowed directory and is refused here.
    allowed_root = pdir.resolve() / parts[0]
    candidate = (pdir / raw).resolve()
    if allowed_root not in candidate.parents:
        raise ProjectError([f"{raw!r} resolves outside the project"])
    if not candidate.is_file():
        raise ProjectError([f"no such asset: {raw}"])
    return candidate


def _file_entry(pdir, path):
    stat = path.stat()
    return {
        "path": str(path.relative_to(pdir)),
        "bytes": stat.st_size,
        "modified_utc": datetime.fromtimestamp(stat.st_mtime, timezone.utc)
        .replace(microsecond=0).isoformat().replace("+00:00", "Z"),
    }


def _read_json(path):
    try:
        return json.loads(path.read_text()) if path.is_file() else None
    except (OSError, ValueError):
        return None


def project_assets(video_id):
    """Everything on disk a reviewer can look at, as one read model.

    A pure filesystem read, like status_report(): it lists what exists and
    summarises the artefacts' own JSON (QC report, audio manifest,
    storyboard, publication package). It computes no verdict - status_report
    stays the only place that does - and every path it returns is relative
    and serveable through project_file_path(). None if there is no project.
    """
    pdir = project_dir(video_id)
    metadata = _read_json(pdir / "metadata.json")
    if metadata is None:
        return None

    video_path = pdir / "output" / f"{video_id}.mp4"
    video = _file_entry(pdir, video_path) if video_path.is_file() else None

    thumbnails = [_file_entry(pdir, p)
                  for p in list_assets(pdir / "thumbnail", (".jpg", ".jpeg", ".png"))]

    board = storyboard_mod.load(video_id)
    scene_by_image = {}
    job_ids = set()
    images_prov = (metadata.get("provenance") or {}).get("images") or {}
    if images_prov.get("job_id"):
        job_ids.add(images_prov["job_id"])
    job_ids.update(images_prov.get("scene_job_ids") or [])
    if board:
        for scene in board.get("scenes", []):
            if scene.get("image"):
                scene_by_image[scene["image"]] = scene["scene_id"]
            if (scene.get("generation") or {}).get("job_id"):
                job_ids.add(scene["generation"]["job_id"])
    lineage = _image_lineage(pdir, job_ids)
    images = []
    for p in list_assets(pdir / "images", SUPPORTED_IMAGE_EXTENSIONS):
        entry = _file_entry(pdir, p)
        entry["scene_id"] = scene_by_image.get(entry["path"])
        entry["generation"] = lineage.get(entry["path"])
        images.append(entry)

    audio = None
    audio_files = list_assets(pdir / "audio", SUPPORTED_AUDIO_EXTENSIONS)
    if audio_files:
        audio = _file_entry(pdir, audio_files[0])
        manifest = _read_json(pdir / "audio" / "audio_manifest.json") or {}
        audio.update({
            "seconds": manifest.get("actual_seconds"),
            "mean_volume_db": manifest.get("mean_volume_db"),
            "layers": [
                {"id": layer.get("layer_id"), "provider": layer.get("provider"),
                 "license": layer.get("license"), "voice": layer.get("voice")}
                for layer in manifest.get("layers", [])
            ],
            "commercial_use_cleared": manifest.get("commercial_use_cleared"),
            "attributions_required": manifest.get("attributions_required", []),
        })

    qc_report = _read_json(pdir / "output" / "qc_report.json")
    qc = None
    if qc_report:
        qc = {
            "status": qc_report.get("status"),
            "checks_run": qc_report.get("checks_run"),
            "checks_failed": qc_report.get("checks_failed"),
            "failures": qc_report.get("failures", []),
            "checks": [
                {"check": c.get("check"), "passed": c.get("passed"), "detail": c.get("detail")}
                for c in qc_report.get("checks", [])
            ],
        }

    storyboard = None
    if board:
        storyboard = {
            "scene_count": len(board.get("scenes", [])),
            "timeline_seconds": board.get("timeline_seconds"),
            "scenes": storyboard_mod.scene_summary(board),
        }

    package_json = _read_json(pdir / "output" / "publication_package.json")
    package = None
    if package_json:
        package = {
            "status": package_json.get("status"),
            "blocking_issues": package_json.get("blocking_issues", []),
            "generated_utc": package_json.get("generated_utc"),
            "path": "output/publication_package.json",
        }

    logs = sorted(
        (_file_entry(pdir, p) for p in (pdir / "logs").glob("*.log")
         if p.is_file()),
        key=lambda e: e["modified_utc"], reverse=True)[:10]

    visual_plan = metadata.get("visual_plan") or {}
    audio_prov = (metadata.get("provenance") or {}).get("audio") or {}
    audio_direction = (metadata.get("audio_plan") or {}).get("direction") or {}
    editing = None
    kdenlive_record = (metadata.get("editing") or {}).get("kdenlive")
    if kdenlive_record:
        project_file = pdir / kdenlive_record.get("project", "")
        render_file = pdir / kdenlive_record.get("render", "") if kdenlive_record.get("render") else None
        package_record = kdenlive_record.get("package") or {}
        archive_file = pdir / package_record["archive"] if package_record.get("archive") else None
        editing = {
            "kdenlive": dict(kdenlive_record),
            "project": _file_entry(pdir, project_file) if project_file.is_file() else None,
            "render": _file_entry(pdir, render_file) if render_file and render_file.is_file() else None,
            # The portable handoff: the project, its project-local media and
            # the provenance manifest in one file. Listed as an asset so the
            # person reviewing the video can actually take the edit away.
            "archive": (_file_entry(pdir, archive_file)
                        if archive_file and archive_file.is_file() else None),
        }
    return {
        "video_id": video_id,
        "video": video,
        "thumbnails": thumbnails,
        "images": images,
        "images_provenance": {
            "provider": images_prov.get("provider"),
            "model": images_prov.get("model"),
            "production_grade": images_prov.get("production_grade"),
            "production_grade_claim": images_prov.get("production_grade_claim"),
            "notes": images_prov.get("notes"),
            # The deliverable set, not everything in images/: a retry that
            # wrote a file is residue, and a reviewer should see which
            # pictures the video actually uses.
            "scene_images": images_prov.get("scene_images") or [],
            "unreferenced_count": len(unreferenced_images(pdir)),
        },
        "visual_plan": {
            "prompt": visual_plan.get("prompt"),
            "negative_prompt": visual_plan.get("negative_prompt"),
            "style": visual_plan.get("style"),
        },
        "audio": audio,
        "audio_provenance": {
            "kind": audio_prov.get("kind"),
            "source": audio_prov.get("source"),
            "production_grade_capable": audio_prov.get("production_grade_capable"),
            "production_grade": audio_prov.get("production_grade"),
            "graded_by": audio_prov.get("graded_by"),
            "graded_utc": audio_prov.get("graded_utc"),
            "grade_notes": audio_prov.get("grade_notes"),
            "kind_reasoning": audio_direction.get("kind_reasoning"),
            "chosen_detail": (audio_direction.get("chosen") or {}).get("detail"),
            "considered": [
                {"source": c.get("source"), "available": c.get("available"),
                 "production_grade_capable": c.get("production_grade_capable"),
                 "rights": c.get("rights"), "cost": c.get("cost"),
                 "detail": c.get("detail")}
                for c in audio_direction.get("considered") or []
            ],
        },
        "qc": qc,
        "storyboard": storyboard,
        "package": package,
        "editing": editing,
        "logs": logs,
    }


def _image_lineage(pdir, job_ids):
    """path -> how that image was made, from the generation job store.

    Read-only over jobs/<id>.json: provider, model, prompt, seed, the
    request's size, and the worker that rendered it when it was remote.
    A job whose record is gone simply has no lineage; nothing is invented.
    """
    lineage = {}
    for job_id in sorted(job_ids):
        job = generation.load_job(job_id)
        if not job:
            continue
        request = job.get("request") or {}
        summary = {
            "job_id": job_id,
            "provider": job.get("provider"),
            "model": job.get("model"),
            "worker_id": job.get("worker_id"),
            "produces_depicted": job.get("produces_depicted"),
            "prompt": request.get("prompt"),
            "negative_prompt": request.get("negative_prompt"),
            "seed": request.get("seed"),
            "width": request.get("width"),
            "height": request.get("height"),
            "completed_at": job.get("completed_at"),
            "notes": job.get("notes"),
        }
        for asset in job.get("assets") or []:
            try:
                relative = str(Path(asset).resolve().relative_to(pdir.resolve()))
            except ValueError:
                continue
            lineage[relative] = summary
    return lineage


def cmd_status(args):
    """Report a project's verdict and whether it still applies.

    Thin formatter over `status_report` - see that function for the actual
    computation.
    """
    report = status_report(args.video_id)
    if report is None:
        log.error("No such project: %s", args.video_id)
        return 1

    print(f"project:  {report['video_id']}")
    print(f"recorded: {report['recorded']}")

    if report["verdict"] == "NOT_RENDERED":
        print("verdict:  NOT_RENDERED - no output video; run './content-machine "
              f"run {args.video_id}'")
        return 2

    print(f"verdict:  {report['verdict']}")
    if report["blocking"]:
        print("blocking:")
        for issue in report["blocking"]:
            print(f"  - {issue}")

    if report["stale"]:
        print(f"WARNING:  recorded status '{report['recorded']}' is STALE - a re-run "
              f"would produce '{report['verdict']}'")

    if report["digest_state"] == "ABSENT":
        print("digest:   absent (verdict predates digest tracking); "
              "re-run to establish it")
    elif report["digest_state"] == "CHANGED":
        print("digest:   CHANGED - project inputs differ from those the "
              "recorded verdict was computed from")
    else:
        print("digest:   matches the recorded verdict's inputs")

    return 0 if report["verdict"] == "READY_FOR_REVIEW" else 2


def run_pipeline(video_id):
    with project_lock(video_id):
        started = datetime.now(timezone.utc)
        try:
            pdir, spec, raw_spec, metadata = load_project(video_id)
        except ProjectError as e:
            log.error("Project validation failed with %d problem(s):", len(e.problems))
            for p in e.problems:
                log.error("  - %s", p)
            return StageResult(False, 1, "project validation failed", {"problems": e.problems})

        log_path, handler = attach_log_file(pdir)
        try:
            log.info("=== Stage 1/5: validate === OK")

            # A project with a storyboard renders its explicit scene plan; one
            # without keeps the original image-cycling contract untouched.
            board = storyboard_mod.load(video_id)
            storyboard_report = None
            if board is not None:
                problems = storyboard_mod.validate(board, pdir)
                if problems:
                    metadata["status"]["render"] = "FAILED"
                    metadata["status"]["overall"] = "FAILED"
                    save_metadata(pdir, metadata)
                    log.error("Storyboard is not renderable (%d problem(s)):",
                              len(problems))
                    for problem in problems:
                        log.error("  - %s", problem)
                    return StageResult(False, 1, "storyboard is not renderable", {"problems": problems})
                # The track the spec declares (already validated by
                # load_project) - not "the first file in audio/", which is
                # the wrong one whenever a hand-supplied source sits next
                # to the composed track.wav.
                audio_seconds = audio_duration(spec["audio_path"])
                storyboard_report = qc.qc_storyboard(board, pdir, audio_seconds=audio_seconds)
                qc.log_report(storyboard_report)
                (pdir / "output").mkdir(parents=True, exist_ok=True)
                (pdir / "output" / "storyboard_qc.json").write_text(
                    json.dumps(storyboard_report, indent=2) + "\n")
                if storyboard_report["status"] != "PASS":
                    metadata["status"]["render"] = "FAILED"
                    metadata["status"]["overall"] = "FAILED"
                    metadata["status"]["storyboard_qc"] = storyboard_report["status"]
                    save_metadata(pdir, metadata)
                    log.error("Storyboard QC failed: %s",
                              ", ".join(storyboard_report["failures"]))
                    return StageResult(False, 1, "storyboard QC failed", {"failures": storyboard_report["failures"]})
                metadata["status"]["storyboard_qc"] = storyboard_report["status"]
                try:
                    spec = storyboard_scene_spec(
                        pdir, json.loads((pdir / "video_spec.json").read_text()), board)
                except render.SpecValidationError as e:
                    metadata["status"]["render"] = "FAILED"
                    metadata["status"]["overall"] = "FAILED"
                    save_metadata(pdir, metadata)
                    log.error("Scene spec invalid (%d error(s)):", len(e.errors))
                    for err in e.errors:
                        log.error("  - %s", err)
                    return StageResult(False, 1, "scene spec invalid", {"errors": e.errors})
                log.info("Rendering %d storyboard scene(s)", len(board["scenes"]))

            # --- Stage 2: render -------------------------------------------------
            output_path = pdir / "output" / f"{video_id}.mp4"
            output_path.parent.mkdir(parents=True, exist_ok=True)
            log.info("=== Stage 2/5: render ===")
            try:
                render.render(spec, output_path)
            except SystemExit:
                metadata["status"]["render"] = "FAILED"
                metadata["status"]["overall"] = "FAILED"
                save_metadata(pdir, metadata)
                log.error("Render failed. Stage: render. Log: %s", log_path)
                log.error("Retry is safe: fix the reported ffmpeg error and re-run.")
                return StageResult(False, 1, "render failed")
            if not output_path.is_file():
                metadata["status"]["render"] = "FAILED"
                metadata["status"]["overall"] = "FAILED"
                save_metadata(pdir, metadata)
                log.error("ffmpeg reported success but no output file at %s", output_path)
                return StageResult(False, 1, "ffmpeg reported success but produced no output file")
            metadata["status"]["render"] = "OK"
            log.info("Rendered: %s (%.2f MB)", output_path,
                     output_path.stat().st_size / (1024 * 1024))

            # --- Stage 3: thumbnails ---------------------------------------------
            log.info("=== Stage 3/5: thumbnails ===")
            # The finished runtime, not the requested one: a crossfade overlaps
            # two scenes, so a scene render is shorter than the sum of its parts.
            finished_seconds = spec.get("timeline_seconds") or spec["duration_seconds"]
            candidates = extract_thumbnails(
                output_path, pdir / "thumbnail", finished_seconds, board=board)
            log.info("Extracted %d thumbnail candidate(s)", len(candidates))

            # --- Stage 4: QC ------------------------------------------------------
            log.info("=== Stage 4/5: quality control ===")
            report = qc.qc_video(
                output_path,
                expected={
                    "width": spec["width"], "height": spec["height"],
                    "fps": spec["fps"], "duration_seconds": finished_seconds,
                },
                source_images=(
                    [scene["image_path"] for scene in spec["scenes"]]
                    if spec.get("scenes") else spec["image_paths"]),
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
                bool(candidates), audio_manifest, storyboard=board,
                storyboard_report=storyboard_report)
            digest = gate_digest(pdir, metadata, output_path)
            title = metadata.get("selected_title") or ""

            elapsed = (datetime.now(timezone.utc) - started).total_seconds()
            metadata["experiment"]["generation_seconds"] = round(elapsed, 2)

            package = {
                "video_id": video_id,
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
                    "duration_seconds": finished_seconds,
                    "requested_duration_seconds": spec["duration_seconds"],
                    "video_codec": "h264",
                    "audio_codec": "aac",
                    "pixel_format": "yuv420p",
                    "container": "mp4",
                    "faststart": True,
                },
                "thumbnail": {
                    "primary": str(candidates[0].relative_to(ROOT)) if candidates else None,
                    "candidates": [str(c.relative_to(ROOT)) for c in candidates],
                    # The concept's own thumbnail direction, carried through so
                    # the person choosing a candidate can see what it was meant
                    # to say. Nothing here picks for them.
                    "concept": metadata.get("thumbnail_concept", ""),
                },
                "title": title,
                "description": metadata.get("description", ""),
                "tags": metadata.get("tags", []),
                "concept": metadata.get("concept", ""),
                "audience": metadata.get("audience", ""),
                "source_assets": (metadata.get("ingestion") or {}).get("inputs", []),
                "source_attributions_required": sorted({
                    asset["rights"]["attribution_text"]
                    for asset in (metadata.get("ingestion") or {}).get("inputs", [])
                    if (asset.get("rights") or {}).get("attribution_required")
                    and (asset.get("rights") or {}).get("attribution_text")}),
                "storyboard": {
                    "scenes": len(board["scenes"]),
                    "timeline_seconds": board["timeline_seconds"],
                    # Stated so a reviewer can see that a 1080p master was
                    # assembled from smaller sources, rather than inferring it.
                    "source_generation": board["source_generation"],
                    "motions": sorted({(s.get("motion") or {}).get("kind")
                                       for s in board["scenes"]}),
                    "qc": {"status": storyboard_report["status"],
                           "failures": storyboard_report["failures"]}
                    if storyboard_report else None,
                } if board else None,
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
            return StageResult(
                not blocking, 0 if not blocking else 2, package["status"],
                {"blocking_issues": blocking, "gate_digest": digest,
                 "package_path": str(package_path)})
        finally:
            logging.getLogger().removeHandler(handler)
            handler.close()


def cmd_run(args):
    return run_pipeline(args.video_id).exit_code


def set_project_duration(video_id, seconds):
    """Re-target an existing project at a new length. Returns whether it moved.

    The storyboard is dropped when the length changes: it is a plan for a
    particular runtime, and keeping it would either stretch every shot or
    leave the video short. The generated images stay - they are reused by
    request digest, so re-planning costs nothing already paid for.
    """
    pdir = project_dir(video_id)
    spec_path = pdir / "video_spec.json"
    if not spec_path.is_file():
        raise ProjectError([f"not a project: {video_id}"])
    with project_lock(video_id):
        spec = json.loads(spec_path.read_text())
        if abs(float(spec.get("duration_seconds") or 0) - float(seconds)) < 0.01:
            return False
        spec["duration_seconds"] = float(seconds)
        spec_path.write_text(json.dumps(spec, indent=2) + "\n")
        metadata = json.loads((pdir / "metadata.json").read_text())
        metadata["duration_seconds"] = float(seconds)
        metadata.setdefault("status", {}).pop("storyboard", None)
        save_metadata(pdir, metadata)
        board_path = storyboard_mod.storyboard_path(video_id)
        if board_path.is_file():
            board_path.unlink()
    return True


def produce_uses_scenes(video_id, metadata):
    """Whether one-click produce should build a storyboard for this project.

    The auto rule, kept deliberately small and inspectable: a storyboard,
    unless the project is the one shape that genuinely wants a held frame.

    This used to be the other way round - scenes only for narrated projects,
    image-cycling for everything else - because a scene was a concurrent
    ffmpeg input and a long video therefore could not have many of them. The
    piecewise renderer removed that limit, and with it the reason: cycling a
    handful of plates on a global Ken Burns move is a slideshow, and a
    half-hour slideshow is not what anyone meant by an ambient video. A
    storyboard gives every shot its own image, its own move and its own
    dissolve, which is the difference between an edit and a loop.

    The exception is a concept whose product *is* one held frame - a dark
    screen behind brown noise. It says so in its own visual direction and in
    the single-image template it is built from, and nothing here overrides
    that.
    """
    if storyboard_mod.load(video_id) is not None:
        return True
    if (metadata.get("script") or "").strip():
        return True
    return not holds_one_frame(
        _load_concept((metadata.get("experiment") or {}).get("concept_id")))


# What a deliberately-held frame looks like from the outside: a concept that
# says in words that nothing moves, or one built from the single-image
# template. Narrow and inspectable on purpose - inferring it from the niche
# instead would quietly turn real ambient videos back into slideshows.
_HELD_FRAME_PHRASES = ("no motion", "dark screen", "static frame",
                       "unchanging", "single static")
_HELD_FRAME_TEMPLATE = "long_static_ambient"


def holds_one_frame(concept):
    """Whether this concept's product is one unchanging picture."""
    if not concept:
        return False
    if _HELD_FRAME_TEMPLATE in (concept.get("spec_template") or ""):
        return True
    text = " ".join(str(concept.get(k) or "") for k in
                    ("visual_concept", "content_format")).lower()
    return any(phrase in text for phrase in _HELD_FRAME_PHRASES)


def run_produce(video_id, concept_id=None, duration=None, production_grade_visuals=None,
                scenes=None):
    """CONCEPT -> research -> creative -> images -> audio -> render -> QC -> package.

    Orchestration only: every stage below is the existing, independently
    tested typed domain function, called the same way its own cmd_* shim
    calls it. Scaffolding a new project shells out to experiment.py rather
    than importing it, since experiment.py already imports this module -
    importing it back would be circular for no benefit over the same CLI
    boundary every other stage already crosses.

    ``scenes`` picks the image stage: True forces storyboard -> scenes,
    False forces the single-plate ``visuals`` path, None (the default) lets
    produce_uses_scenes() decide from the project itself. Research is
    always attempted first; it is a no-op for concepts that don't require
    it and fails closed for those that do when no source is configured.

    Deliberately does not hold project_lock itself: each stage below
    acquires and releases its own lock, exactly as it does when the CLI
    calls them one at a time. An outer lock here would deadlock against
    them - fcntl locks are not reentrant within one process.
    """
    pdir = project_dir(video_id)
    if not pdir.exists():
        if not concept_id:
            log.error(
                "Project %s does not exist and no --concept-id was given "
                "to scaffold one from.", video_id)
            return StageResult(False, 1, "no project and no concept_id to scaffold from")
        log.info("=== Stage 1/6: concept -> scaffold ===")
        scaffold_cmd = [sys.executable, str(ROOT / "scripts" / "experiment.py"), "scaffold",
                        concept_id, video_id]
        if duration:
            scaffold_cmd += ["--duration", str(duration)]
        rc = subprocess.run(scaffold_cmd, cwd=str(ROOT)).returncode
        if rc != 0:
            log.error("Scaffold failed (exit %d)", rc)
            return StageResult(False, rc, "scaffold failed")
    else:
        log.info("=== Stage 1/6: concept === reusing existing project %s", video_id)
        if duration:
            # Explicitly asked for at a length: this is how an excerpt
            # becomes the full video. Changing the spec invalidates the
            # storyboard built for the old length, so it is rebuilt rather
            # than stretched - a 90-second scene plan is not a 30-minute one
            # with longer shots.
            changed = set_project_duration(video_id, float(duration))
            if changed:
                log.info("Length set to %.0fs; the scene plan will be rebuilt "
                         "for it.", float(duration))

    log.info("=== Stage 2/6: research (subject facts if required; brief-driven "
             "competitor research if a brief exists) ===")
    result = run_research(video_id)
    if not result.ok:
        return result

    log.info("=== Stage 3/6: creative (title, script, description, image direction, audio plan) ===")
    result = run_creative(video_id, force=False)
    if not result.ok:
        return result

    metadata = json.loads((pdir / "metadata.json").read_text())
    use_scenes = scenes if scenes is not None else produce_uses_scenes(video_id, metadata)
    if use_scenes:
        log.info("=== Stage 4/6: images (storyboard -> one image per scene) ===")
        result = run_storyboard(video_id)
        if not result.ok:
            return result
        result = run_scenes(video_id)
    else:
        log.info("=== Stage 4/6: images (single plate set) ===")
        result = run_visuals(video_id)
    if not result.ok:
        return result

    if production_grade_visuals is not None:
        # A human passed this explicitly on the command line - recorded
        # exactly as `init` records it, and applied AFTER run_visuals:
        # run_visuals unconditionally records what the provider actually
        # produced (False for procedural), so setting this claim any
        # earlier would just be overwritten by that.
        with project_lock(video_id):
            metadata = json.loads((pdir / "metadata.json").read_text())
            metadata.setdefault("provenance", {}).setdefault("images", {})[
                "production_grade"] = production_grade_visuals
            save_metadata(pdir, metadata)

    log.info("=== Stage 5/6: audio ===")
    result = run_audio(video_id)
    if not result.ok:
        return result

    log.info("=== Stage 6/6: render -> QC -> package ===")
    return run_pipeline(video_id)


def cmd_produce(args):
    return run_produce(
        args.video_id, concept_id=args.concept_id, duration=args.duration,
        production_grade_visuals=args.production_grade_visuals,
        scenes=args.scenes).exit_code


REVIEW_DECISIONS = ("approved", "rejected")


def record_review_decision(video_id, reviewer, decision, notes="", expected_digest=None):
    """A human's review verdict on a project, recorded once, atomically.

    This is the *only* place ``metadata.json.publish.approved_by_human`` is
    written - it does not "publish" anything (``publish.published`` /
    ``publication_id`` in ``output/publication_package.json`` stay
    untouched; publishing is a separate, unbuilt capability).

    ``decision`` is ``"approved"`` or ``"rejected"``. ``reviewer`` must be a
    non-empty human-attributable identity - the CLI sources it from a
    required ``--reviewer`` flag (never a default) and the DRF view sources
    it from ``request.user``; this function itself refuses an empty one so
    no automated caller can slip a system identity through either surface.

    The gate is re-checked from scratch here, not trusted from the caller:
    ``expected_digest`` must match a freshly computed ``gate_digest()`` (a
    stale snapshot is refused rather than silently accepted), and approval
    is refused outright whenever a fresh ``gate_blockers()`` call is
    non-empty - rejection carries no such requirement, since flagging a
    broken project is always allowed.
    """
    if decision not in REVIEW_DECISIONS:
        raise ReviewDecisionError(
            f"invalid decision: {decision!r} (expected 'approved' or 'rejected')")
    if not reviewer or not reviewer.strip():
        raise ReviewDecisionError("reviewer is required and must be a human identity")

    with project_lock(video_id):
        pdir = project_dir(video_id)
        meta_path = pdir / "metadata.json"
        if not meta_path.is_file():
            raise ReviewDecisionError(f"not a project: {video_id}")
        metadata = json.loads(meta_path.read_text())

        video_path = pdir / "output" / f"{video_id}.mp4"
        if not video_path.is_file():
            raise ReviewDecisionError(f"{video_id} has not been rendered yet")

        qc_path = pdir / "output" / "qc_report.json"
        qc_report = json.loads(qc_path.read_text()) if qc_path.is_file() else {}
        manifest_path = pdir / "audio" / "audio_manifest.json"
        audio_manifest = (json.loads(manifest_path.read_text())
                          if manifest_path.is_file() else {})

        current_digest = gate_digest(pdir, metadata, video_path)
        if expected_digest != current_digest:
            raise ReviewDecisionError(
                "expected_digest is stale: the project's current gate_digest "
                "does not match what this review decision was based on - "
                "re-fetch the project's status and retry")

        blocking = gate_blockers(
            pdir, metadata,
            qc_report.get("status", "MISSING"), qc_report.get("failures", []),
            bool(list_assets(pdir / "thumbnail", (".jpg", ".jpeg"))),
            audio_manifest)
        if decision == "approved" and blocking:
            raise ReviewDecisionError([f"cannot approve: {b}" for b in blocking])

        entry = {
            "utc": utc_now(),
            "reviewer": reviewer,
            "decision": decision,
            "notes": notes or "",
            "gate_digest": current_digest,
        }
        metadata.setdefault("review_history", []).append(entry)
        # A rejection revokes any prior approval - "approved" only ever
        # means "the most recent human decision was approval".
        metadata.setdefault("publish", {})["approved_by_human"] = decision == "approved"
        save_metadata(pdir, metadata)
        return entry


def cmd_archive(args):
    try:
        summary = set_archived(args.video_id, not args.restore, args.by, reason=args.reason)
    except ProjectError as e:
        log.error(str(e))
        return 1
    log.info("%s is now %s (nothing was deleted)", summary["video_id"],
             "archived" if summary["archived"] else "active")
    return 0


def cmd_approve(args):
    try:
        entry = record_review_decision(
            args.video_id, args.reviewer, "approved",
            notes=args.notes, expected_digest=args.expected_digest)
    except ProjectError as e:
        log.error(str(e))
        return 1
    log.info("Approved by %s at %s", entry["reviewer"], entry["utc"])
    return 0


def cmd_reject(args):
    try:
        entry = record_review_decision(
            args.video_id, args.reviewer, "rejected",
            notes=args.notes, expected_digest=args.expected_digest)
    except ProjectError as e:
        log.error(str(e))
        return 1
    log.info("Rejected by %s at %s", entry["reviewer"], entry["utc"])
    return 0


def save_metadata(pdir, metadata):
    (pdir / "metadata.json").write_text(json.dumps(metadata, indent=2) + "\n")


def main():
    # The same .env the web/Celery processes load (cmweb/settings/base.py),
    # so a stage behaves identically whichever entry point started it.
    # Values already in the shell environment win over the file.
    envfile.load_env_file()
    parser = argparse.ArgumentParser(
        description="Content Machine project lifecycle.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_import = sub.add_parser("import-media", help="new project from cataloged still images and audio")
    p_import.add_argument("video_id")
    p_import.add_argument("--catalog", type=Path, default=media_mod.DEFAULT_CATALOG)
    p_import.add_argument("--image-id", action="append", required=True, help="repeat in desired sequence order")
    p_import.add_argument("--audio-id", required=True)
    p_import.add_argument("--title", required=True)
    p_import.add_argument("--description", required=True)
    p_import.add_argument("--duration", type=float)
    p_import.add_argument("--width", type=int, default=1920)
    p_import.add_argument("--height", type=int, default=1080)
    p_import.add_argument("--fps", type=int, default=30)
    p_import.add_argument("--transition-seconds", type=float, default=0.75)
    p_import.add_argument("--motion", choices=motion_mod.MOTIONS, default="zoom_in")
    p_import.add_argument(
        "--allow-unverified-audio", action="store_true",
        help="private review bootstrap only; keep the rights gate blocked until the track is verified")
    p_import.set_defaults(func=cmd_import_media)

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

    p_research = sub.add_parser(
        "research", help="source-backed subject research, cached once (no-op if the concept doesn't need it)")
    p_research.add_argument("video_id")
    p_research.add_argument("--concept-id", dest="concept_id", default=None,
                            help="defaults to metadata.experiment.concept_id")
    p_research.add_argument("--force", action="store_true",
                            help="re-research even if a cached artifact exists")
    p_research.set_defaults(func=cmd_research)

    p_creative = sub.add_parser(
        "creative", help="generate title, script, description, image direction and audio plan")
    p_creative.add_argument("video_id")
    p_creative.add_argument("--force", action="store_true",
                            help="overwrite fields already set, instead of filling in only what is missing")
    p_creative.set_defaults(func=cmd_creative)

    p_audio = sub.add_parser("audio", help="compose the project's audio track from its audio plan")
    p_audio.add_argument("video_id")
    p_audio.add_argument("--duration", type=float, default=None,
                         help="override target seconds (defaults to the video duration)")
    p_audio.set_defaults(func=cmd_audio)

    p_vis = sub.add_parser("visuals", help="generate the project's images via the provider router")
    p_vis.add_argument("video_id")
    p_vis.add_argument("--prompt", default=None, help="defaults to metadata.visual_plan.prompt")
    p_vis.add_argument("--negative", default=None)
    p_vis.add_argument("--count", type=int, default=None)
    p_vis.add_argument("--width", type=int, default=None)
    p_vis.add_argument("--height", type=int, default=None)
    p_vis.add_argument("--seed", type=int, default=None)
    p_vis.add_argument("--model", default=None)
    p_vis.add_argument("--style", default=None, help="procedural fallback style")
    p_vis.add_argument("--depicted", action="store_true",
                       help="require depicted imagery even if the concept allows plates")
    p_vis.set_defaults(func=cmd_visuals)

    p_grade = sub.add_parser("visual-grade",
                             help="record a human's production-grade claim for the visuals")
    p_grade.add_argument("video_id")
    p_grade.add_argument("grade", choices=("true", "false"))
    p_grade.add_argument("--reviewer", required=True, help="a human identity, never a default")
    p_grade.add_argument("--notes", default="")
    p_grade.set_defaults(func=cmd_visual_grade)

    p_agrade = sub.add_parser(
        "audio-grade",
        help="record a human's production-grade claim for the audio (listen first)")
    p_agrade.add_argument("video_id")
    p_agrade.add_argument("grade", choices=("true", "false"))
    p_agrade.add_argument("--reviewer", required=True, help="a human identity, never a default")
    p_agrade.add_argument("--notes", default="")
    p_agrade.set_defaults(func=cmd_audio_grade)

    p_story = sub.add_parser(
        "storyboard", help="derive this project's scene plan from its script and format profile")
    p_story.add_argument("video_id")
    p_story.add_argument("--niche", default=None, help="format profile to shape it")
    p_story.add_argument("--scenes", type=int, default=None)
    p_story.add_argument("--source-width", type=int, default=None)
    p_story.add_argument("--source-height", type=int, default=None)
    p_story.add_argument("--force", action="store_true",
                         help="discard already-assigned scene images/motifs")
    p_story.set_defaults(func=cmd_storyboard)

    p_scenes = sub.add_parser("scenes", help="generate (or reuse) one image per storyboard scene")
    p_scenes.add_argument("video_id")
    p_scenes.add_argument("--force", action="store_true",
                          help="regenerate every scene, even ones with an image already")
    p_scenes.add_argument("--depicted", action="store_true",
                          help="require depicted imagery even if the concept allows plates")
    p_scenes.set_defaults(func=cmd_scenes)

    p_prov = sub.add_parser("providers", help="show generation provider health")
    p_prov.add_argument("--depicted", action="store_true",
                        help="only providers that can produce depicted imagery")
    p_prov.set_defaults(func=cmd_providers)

    p_run = sub.add_parser("run", help="validate -> render -> thumbnails -> QC -> package")
    p_run.add_argument("video_id")
    p_run.set_defaults(func=cmd_run)

    p_kdenlive = sub.add_parser(
        "kdenlive", help="build, verify and optionally render an editable Kdenlive project")
    p_kdenlive.add_argument("video_id")
    p_kdenlive.add_argument(
        "--no-render", action="store_true",
        help="write and verify the editable project without producing a second MP4")
    p_kdenlive.set_defaults(func=cmd_kdenlive)

    p_status = sub.add_parser(
        "status", help="report a project's verdict and whether it still applies")
    p_status.add_argument("video_id")

    p_produce = sub.add_parser(
        "produce", help="concept -> creative -> images -> audio -> render -> QC -> package")
    p_produce.add_argument("video_id")
    p_produce.add_argument("--concept-id", dest="concept_id", default=None,
                           help="scaffold a new project from this concept if video_id doesn't exist yet")
    p_produce.add_argument("--duration", type=float, default=None,
                           help="override the concept template's duration, in seconds")
    p_produce.add_argument(
        "--production-grade-visuals", dest="production_grade_visuals",
        action="store_const", const=True, default=None,
        help="declare the generated visuals as production-grade (a human decision - see init)")
    p_produce.add_argument(
        "--scenes", dest="scenes", action="store_const", const=True, default=None,
        help="build a storyboard and one image per scene (default: automatic - "
             "projects with narration or an existing storyboard use scenes)")
    p_produce.add_argument(
        "--no-scenes", dest="scenes", action="store_const", const=False,
        help="force the single-plate image path even if the project has narration")
    p_produce.set_defaults(func=cmd_produce)
    p_status.set_defaults(func=cmd_status)

    p_approve = sub.add_parser(
        "approve", help="record a human's approval (requires a fresh, matching gate digest)")
    p_approve.add_argument("video_id")
    p_approve.add_argument("--reviewer", required=True,
                           help="a human-attributable identity - never a service/system default")
    p_approve.add_argument("--notes", default="")
    p_approve.add_argument(
        "--expected-digest", dest="expected_digest", required=True,
        help="gate_digest this approval was based on (see 'status'); a mismatch is refused")
    p_approve.set_defaults(func=cmd_approve)

    p_reject = sub.add_parser("reject", help="record a human's rejection")
    p_reject.add_argument("video_id")
    p_reject.add_argument("--reviewer", required=True,
                          help="a human-attributable identity - never a service/system default")
    p_reject.add_argument("--notes", default="")
    p_reject.add_argument(
        "--expected-digest", dest="expected_digest", required=True,
        help="gate_digest this rejection was based on (see 'status'); a mismatch is refused")
    p_reject.set_defaults(func=cmd_reject)

    p_archive = sub.add_parser(
        "archive", help="hide a project from active views without deleting anything")
    p_archive.add_argument("video_id")
    p_archive.add_argument("--by", required=True,
                           help="a human-attributable identity - never a service/system default")
    p_archive.add_argument("--reason", default="")
    p_archive.add_argument("--restore", action="store_true",
                           help="put an archived project back in the active views")
    p_archive.set_defaults(func=cmd_archive)

    args = parser.parse_args()

    if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        log.error("ffmpeg and ffprobe must be installed and on PATH.")
        return 1
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
