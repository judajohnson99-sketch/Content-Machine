#!/usr/bin/env python3
"""Content experiment framework.

Holds content hypotheses (experiments/concepts.json), ranks them by
expected information value, and scaffolds a real project directory from
a concept so that production can start the moment assets exist.

    python3 scripts/experiment.py list [--niche N] [--top N]
    python3 scripts/experiment.py show <concept-id>
    python3 scripts/experiment.py blockers
    python3 scripts/experiment.py validate
    python3 scripts/experiment.py scaffold <concept-id> <video-id> [--images DIR --audio FILE]

Ranking is computed, not hand-assigned: see score() below. The weights
are visible and adjustable so a ranking can be argued with rather than
taken on faith.
"""
import argparse
import json
import logging
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import audio  # noqa: E402
import generation  # noqa: E402
import goal as goal_mod  # noqa: E402
import worker  # noqa: E402
import project as project_mod  # noqa: E402
import subject_research  # noqa: E402

logging.basicConfig(level=logging.INFO, format="[%(levelname)s] %(message)s")
log = logging.getLogger("experiment")

ROOT = Path(__file__).resolve().parent.parent
EXPERIMENTS_DIR = ROOT / "experiments"
CONCEPTS_PATH = EXPERIMENTS_DIR / "concepts.json"

BENEFIT_KEYS = ("uncertainty_reduction", "generalizability", "repeatability", "monetization_clarity")
COST_KEYS = ("production_complexity", "generation_dependence")
REQUIRED_FIELDS = (
    "id", "niche", "target_audience", "content_format", "video_length_minutes",
    "visual_concept", "audio_concept", "publishing_format", "monetization_hypothesis",
    "production_complexity", "generation_dependence", "risks", "continue_evidence",
    "kill_evidence", "scores", "assumptions_requiring_research",
    "audio_source_requirement", "spec_template",
    # Whether procedural/generated plates can legitimately be the deliverable.
    # If false, a project scaffolded from this concept cannot claim
    # production-grade visuals while its image provider is procedural.
    "procedural_visuals_acceptable",
)

# Each step of policy risk above 1 removes 15% of a concept's value: a
# maximally risky idea keeps 40%, it is not zeroed out, because a cheap
# test can still be worth running to establish where the line actually is.
POLICY_RISK_DISCOUNT_PER_STEP = 0.15

# What each audio requirement needs before a concept can actually be produced.
AUDIO_CAPABILITY = {
    "synthesisable_now": "AVAILABLE - noise/tone/rain providers synthesise this today",
    "tts_required": "AVAILABLE - local Piper narration (CC BY 4.0 voice, attribution required)",
    "licensed_or_recorded": "PARTIAL - procedural 'rain' bed can test the format; a real "
                            "recording is still needed for final quality",
    "licensed_or_recorded_plus_mixing": "PARTIAL - mixing is implemented; source audio still needed",
    "music_generation_or_licensed": "BLOCKED - needs music generation or a music licence",
    "music_generation_plus_mixing": "BLOCKED - needs music generation AND mixing",
}


def load_concepts():
    if not CONCEPTS_PATH.is_file():
        raise SystemExit(f"concepts file not found: {CONCEPTS_PATH}")
    data = json.loads(CONCEPTS_PATH.read_text())
    return data, data["concepts"]


def score(concept):
    """Expected information value.

    benefit  = how much running this teaches us, and how far that transfers
    cost     = how expensive it is to find out
    discount = how likely the answer is made moot by policy/legal exposure

    Deliberately NOT a preference ranking: a concept we expect to fail can
    still score highly if failing would be informative and cheap to learn.
    """
    s = concept["scores"]
    benefit = 1
    for key in BENEFIT_KEYS:
        benefit *= s[key]
    cost = sum(concept[key] for key in COST_KEYS)
    discount = 1.0 - (s["policy_risk"] - 1) * POLICY_RISK_DISCOUNT_PER_STEP
    return (benefit / cost) * discount


def ranked(concepts):
    scored = [(score(c), c) for c in concepts]
    top = max((s for s, _ in scored), default=1.0) or 1.0
    # Normalise to 0-100 so scores are comparable at a glance; ties break
    # by id to keep the ordering deterministic across runs.
    return sorted(
        ({"eiv": round(s / top * 100, 1), "raw": round(s, 2), **c} for s, c in scored),
        key=lambda c: (-c["eiv"], c["id"]),
    )


def cmd_list(args):
    _, concepts = load_concepts()
    rows = ranked(concepts)
    if args.niche:
        rows = [r for r in rows if r["niche"] == args.niche]
    if args.top:
        rows = rows[: args.top]

    print(f"{'#':>3}  {'EIV':>5}  {'CONCEPT':<34} {'NICHE':<24} {'MIN':>5}  AUDIO")
    print("-" * 108)
    for i, c in enumerate(rows, 1):
        capability = AUDIO_CAPABILITY.get(c["audio_source_requirement"], "?")
        flag = "ok " if capability.startswith("AVAILABLE") else (
            "~  " if capability.startswith("PARTIAL") else "   ")
        print(f"{i:>3}  {c['eiv']:>5}  {c['id']:<34} {c['niche']:<24} "
              f"{c['video_length_minutes']:>5}  {flag}{capability.split(' - ')[0]}")
    print(f"\n{len(rows)} concept(s). EIV = expected information value, "
          f"normalised to the top concept. Higher = learn more per unit of cost.")
    return 0


def cmd_show(args):
    _, concepts = load_concepts()
    match = next((c for c in ranked(concepts) if c["id"] == args.concept_id), None)
    if not match:
        log.error("No such concept: %s", args.concept_id)
        return 1

    def section(label, value):
        print(f"\n{label}")
        if isinstance(value, list):
            for item in value:
                print(f"  - {item}")
        else:
            print(f"  {value}")

    print(f"\n{match['id']}  (EIV {match['eiv']}/100, niche: {match['niche']})")
    print(f"Working title: {match.get('working_title_pattern', '-')}")
    section("Target audience", match["target_audience"])
    section("Format", f"{match['content_format']} (~{match['video_length_minutes']} min)")
    section("Visual concept", match["visual_concept"])
    section("Audio concept", match["audio_concept"])
    section("Audio capability", AUDIO_CAPABILITY.get(match["audio_source_requirement"], "?"))
    section("Publishing", match["publishing_format"])
    section("Monetization hypothesis (UNVERIFIED)", match["monetization_hypothesis"])
    section("Production complexity", f"{match['production_complexity']}/5 "
                                     f"(generation dependence {match['generation_dependence']}/5)")
    section("Risks", match["risks"])
    section("Continue if", match["continue_evidence"])
    section("Kill if", match["kill_evidence"])
    section("Must research before producing", match["assumptions_requiring_research"])
    section("Spec template", match["spec_template"])
    return 0


def cmd_blockers(args):
    """What actually stands between us and producing each concept."""
    _, concepts = load_concepts()
    buckets = {}
    for c in ranked(concepts):
        buckets.setdefault(c["audio_source_requirement"], []).append(c)

    print("\nProduction blockers by audio requirement "
          "(audio, not imagery, is the binding constraint):\n")
    for requirement, group in sorted(
            buckets.items(), key=lambda kv: not AUDIO_CAPABILITY[kv[0]].startswith("AVAILABLE")):
        print(f"{AUDIO_CAPABILITY[requirement]}")
        for c in group:
            print(f"    {c['eiv']:>5}  {c['id']}")
        print()

    available = [c for c in ranked(concepts)
                 if AUDIO_CAPABILITY[c["audio_source_requirement"]].startswith("AVAILABLE")]
    partial = [c for c in ranked(concepts)
               if AUDIO_CAPABILITY[c["audio_source_requirement"]].startswith("PARTIAL")]
    print(f"{len(available)} of {len(concepts)} concepts are fully producible today; "
          f"{len(partial)} more can be tested with rights-clean procedural audio.")
    return 0


def cmd_validate(args):
    data, concepts = load_concepts()
    problems = []
    seen = set()

    for index, c in enumerate(concepts):
        label = c.get("id", f"index {index}")
        for field in REQUIRED_FIELDS:
            if field not in c:
                problems.append(f"{label}: missing required field '{field}'")
        if c.get("id") in seen:
            problems.append(f"{label}: duplicate id")
        seen.add(c.get("id"))

        for key in BENEFIT_KEYS + ("policy_risk",):
            value = c.get("scores", {}).get(key)
            if not isinstance(value, int) or not 1 <= value <= 5:
                problems.append(f"{label}: scores.{key} must be an int 1-5, got {value!r}")
        for key in COST_KEYS:
            value = c.get(key)
            if not isinstance(value, int) or not 1 <= value <= 5:
                problems.append(f"{label}: {key} must be an int 1-5, got {value!r}")

        if c.get("audio_source_requirement") not in AUDIO_CAPABILITY:
            problems.append(f"{label}: unknown audio_source_requirement "
                            f"{c.get('audio_source_requirement')!r}")

        # Must be an actual bool: the concept-drift check in project.py
        # keys off this flag, and a truthy string like "no" would be a
        # silent footgun.
        if not isinstance(c.get("procedural_visuals_acceptable"), bool):
            problems.append(
                f"{label}: procedural_visuals_acceptable must be true/false, "
                f"got {c.get('procedural_visuals_acceptable')!r}"
            )

        template = EXPERIMENTS_DIR / str(c.get("spec_template", ""))
        if not template.is_file():
            problems.append(f"{label}: spec_template not found: {template}")

        for field in ("risks", "continue_evidence", "kill_evidence",
                      "assumptions_requiring_research"):
            if not c.get(field):
                problems.append(f"{label}: '{field}' must be a non-empty list")

    if problems:
        log.error("Concept validation failed with %d problem(s):", len(problems))
        for p in problems:
            log.error("  - %s", p)
        return 1

    log.info("All %d concepts valid; %d templates referenced and present.",
             len(concepts), len({c["spec_template"] for c in concepts}))
    return 0


class ScaffoldError(project_mod.ProjectError):
    """scaffold_project refused. ``code`` is one of "invalid" (bad input),
    "unknown_concept", "exists" or "assets" so an adapter can map it to the
    right HTTP status without parsing the message."""

    def __init__(self, code, problem):
        super().__init__([problem])
        self.code = code


# A project id is a directory name, a URL segment and a YouTube-facing
# slug at once: lowercase, digits and hyphens only, 3-64 characters.
VIDEO_ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{2,63}$")


def find_concept(concept_id, concepts=None):
    """The concept with this id, from the catalogue or from a derived one.

    A production derived from an operator goal carries its own concept in
    experiments/derived/; it is a real concept to everything downstream and
    is deliberately not written into the curated catalogue.
    """
    derived = goal_mod.load_derived_concept(concept_id)
    if derived is not None:
        return derived
    if concepts is None:
        _, concepts = load_concepts()
    return next((c for c in concepts if c["id"] == concept_id), None)


def scaffold_project(concept_id, video_id, duration=None, images=None, audio=None, force=False):
    """Turn a concept into a real project directory.

    The one implementation behind `experiment.py scaffold`, the web API's
    POST /projects/ and run_produce's scaffold step. Returns a summary dict;
    raises ScaffoldError rather than logging-and-returning so every caller
    sees the same refusal.
    """
    if not VIDEO_ID_RE.match(video_id or ""):
        raise ScaffoldError(
            "invalid",
            f"invalid project id {video_id!r}: use 3-64 lowercase letters, digits or hyphens")
    concept = find_concept(concept_id)
    if not concept:
        raise ScaffoldError("unknown_concept", f"No such concept: {concept_id}")
    if duration is not None and float(duration) <= 0:
        raise ScaffoldError("invalid", f"duration must be positive, got {duration!r}")

    pdir = project_mod.project_dir(video_id)
    if pdir.exists() and not force:
        raise ScaffoldError("exists", f"project already exists: {video_id}")

    template_path = EXPERIMENTS_DIR / concept["spec_template"]
    spec = json.loads(template_path.read_text())
    spec.pop("_comment", None)
    if duration:
        spec["duration_seconds"] = float(duration)

    for sub in project_mod.SUBDIRS:
        (pdir / sub).mkdir(parents=True, exist_ok=True)

    assets_ready = False
    if images and audio:
        images_src = Path(images).expanduser().resolve()
        audio_src = Path(audio).expanduser().resolve()
        sources = project_mod.list_assets(images_src, project_mod.SUPPORTED_IMAGE_EXTENSIONS)
        if not sources:
            raise ScaffoldError("assets", f"no supported images found in {images_src}")
        if not audio_src.is_file():
            raise ScaffoldError("assets", f"audio file not found: {audio_src}")
        for image in sources:
            project_mod.ingest(image, pdir / "images")
        audio_dest, _ = project_mod.ingest(audio_src, pdir / "audio")
        spec["audio"]["file"] = f"audio/{audio_dest.name}"
        assets_ready = True
        log.info("Ingested %d image(s) + audio", len(sources))

    (pdir / "video_spec.json").write_text(json.dumps(spec, indent=2) + "\n")

    metadata = project_mod.build_metadata(
        video_id,
        title=concept.get("working_title_pattern"),
        concept=concept["content_format"],
        audience=concept["target_audience"],
        duration=spec["duration_seconds"],
        resolution=f"{spec['width']}x{spec['height']}",
        fps=spec["fps"],
    )
    metadata["hypothesis"] = concept["monetization_hypothesis"]
    metadata["visual_style"] = concept["visual_concept"]
    metadata["thumbnail_concept"] = concept["visual_concept"]
    metadata["audio_plan"] = {
        "mode": "single_track",
        "requirement": concept["audio_source_requirement"],
        "capability": AUDIO_CAPABILITY[concept["audio_source_requirement"]],
        "concept": concept["audio_concept"],
    }
    metadata["experiment"].update({
        "concept_id": concept["id"],
        "batch_id": load_concepts()[0]["batch_id"],
        "niche": concept["niche"],
        "continue_evidence": concept["continue_evidence"],
        "kill_evidence": concept["kill_evidence"],
        "open_questions": concept["assumptions_requiring_research"],
        "variables": {
            "niche": concept["niche"],
            "length_minutes": concept["video_length_minutes"],
            "template": concept["spec_template"],
        },
    })
    metadata["status"]["assets"] = "READY" if assets_ready else "PENDING"
    project_mod.save_metadata(pdir, metadata)
    log.info("Scaffolded %s from concept '%s'", pdir, concept["id"])
    return {
        "video_id": video_id,
        "concept_id": concept["id"],
        "project_dir": str(pdir),
        "duration_seconds": spec["duration_seconds"],
        "assets_ready": assets_ready,
    }


def cmd_scaffold(args):
    """Turn a concept into a real project directory."""
    try:
        result = scaffold_project(
            args.concept_id, args.video_id, duration=args.duration,
            images=args.images, audio=args.audio, force=args.force)
    except ScaffoldError as e:
        log.error("%s", e)
        if e.code == "exists":
            log.error("Use --force to re-scaffold over it.")
        return 1

    if result["assets_ready"]:
        log.info("Assets present. Render with: ./content-machine run %s", args.video_id)
    else:
        pdir = Path(result["project_dir"])
        log.info("Assets PENDING. Add images to %s/images and audio to %s/audio,",
                 pdir.name, pdir.name)
        log.info("then: ./content-machine validate %s", args.video_id)
        concept = find_concept(result["concept_id"])
        log.info("Audio requirement: %s", AUDIO_CAPABILITY[concept["audio_source_requirement"]])
    return 0


# --------------------------------------------------------------------------
# concept catalogue - the read model behind "start a new production"
# --------------------------------------------------------------------------

AUDIO_READINESS = {
    "synthesisable_now": "ok",
    "tts_required": "ok",
    "licensed_or_recorded": "partial",
    "licensed_or_recorded_plus_mixing": "partial",
    "music_generation_or_licensed": "blocked",
    "music_generation_plus_mixing": "blocked",
}


# Concept kinds. "reference" is the curated showcase set - what the channel
# would actually publish; "technical" is a regression concept kept for the
# pipeline's own sake (dark-screen noise beds that exercise the QC floor);
# "experiment" is the ranked information-value batch. The New Production
# surface leads with reference, keeps technical out of the default view.
CONCEPT_KINDS = ("reference", "experiment", "technical")
DEFAULT_CONCEPT_KIND = "experiment"

# Which GPU-worker readiness states mean a depicted render would start now.
_GPU_STARTS_NOW = ("worker_ready", "worker_busy")


def host_capabilities():
    """What this host can do right now, from configuration and the worker
    registry alone.

    No network probes: ``configured()`` is a pure settings check and the GPU
    worker's readiness is derived from its last heartbeat on disk, so this
    is cheap enough for a page load. Live provider health is `providers`'
    job. A future control adapter (or NeuroSpace) reads the same dict.
    """
    router = generation.Router()
    depicted = [p.name for p in router.candidates() if p.produces_depicted and p.configured()]
    search = subject_research.provider_status()
    narration = audio.narration_status()
    gpu = worker.depicted_readiness()
    if depicted:
        state, detail = "provider_configured", f"depicted-image provider configured: {', '.join(depicted)}"
    elif gpu["state"] == worker.GPU_NO_WORKER:
        state, detail = "unavailable", gpu["detail"]
    else:
        state, detail = gpu["state"], gpu["detail"]
    return {
        "depicted_image_providers": depicted,
        "procedural_images": True,
        "search_provider": search["configured"],
        "search_available": search["available"],
        "narration_available": narration["available"],
        "narration": narration,
        "depicted_imagery": {"state": state, "detail": detail,
                             "starts_now": bool(depicted) or gpu["state"] in _GPU_STARTS_NOW,
                             "worker_id": gpu.get("worker_id")},
        "remote_gpu": gpu,
    }


def concept_readiness(concept, capabilities):
    """Per-concept answer to "can this run here, and what would stop it?"

    Derived from the concept's declared requirements and host_capabilities()
    only - never from a run's outcome, and never a promise about quality.
    """
    notes = []
    depicted = capabilities.get("depicted_imagery") or {}
    gpu_state = (capabilities.get("remote_gpu") or {}).get("state")
    if concept.get("procedural_visuals_acceptable"):
        images, images_via = "ok", "procedural"
    elif capabilities["depicted_image_providers"]:
        images, images_via = "ok", f"provider:{capabilities['depicted_image_providers'][0]}"
    elif gpu_state in _GPU_STARTS_NOW:
        images, images_via = "ok", "gpu-worker"
    elif gpu_state and gpu_state != worker.GPU_NO_WORKER:
        # A worker exists but cannot start right now: the job will queue
        # and wait, at no cost, and production pauses at the image stage.
        images, images_via = "partial", "gpu-worker"
        notes.append(f"Depicted imagery will queue for the GPU worker "
                     f"({depicted.get('detail', gpu_state)}); production pauses at "
                     "the image stage until it renders.")
    else:
        images, images_via = "blocked", None
        notes.append("Needs depicted imagery; no image provider that produces it is "
                     "configured and no GPU worker is enrolled (procedural plates "
                     "would be rejected at review).")

    audio = AUDIO_READINESS.get(concept.get("audio_source_requirement"), "blocked")
    if audio != "ok":
        notes.append(AUDIO_CAPABILITY.get(concept.get("audio_source_requirement"), "audio requirement unknown"))

    if not concept.get("requires_subject_research"):
        research = "n/a"
    elif capabilities["search_available"]:
        research = "ok"
    else:
        research = "blocked"
        notes.append("Needs source-backed subject research; no search provider is configured "
                     "(set SEARXNG_URL, or SEARCH_ORDER/SEARCH_PROVIDER, or remove "
                     "SEARCH_KEYLESS=0 to allow the keyless fallbacks), "
                     "so produce fails closed at the research stage.")

    return {
        "images": images,
        "images_via": images_via,
        "audio": audio,
        "research": research,
        "runnable_now": images == "ok" and audio != "blocked" and research != "blocked",
        "waits_for_gpu": images == "partial",
        "notes": notes,
    }


def concept_kind(concept):
    kind = concept.get("kind") or DEFAULT_CONCEPT_KIND
    return kind if kind in CONCEPT_KINDS else DEFAULT_CONCEPT_KIND


def narration_status(concept):
    requirement = concept.get("audio_source_requirement")
    if requirement == "tts_required":
        return "narrated"
    return "none"


def concept_catalog():
    """Every concept as the new-production UI needs it, ranked as `list` ranks."""
    _, concepts = load_concepts()
    capabilities = host_capabilities()
    catalog = []
    for concept in ranked(concepts):
        catalog.append({
            "id": concept["id"],
            "kind": concept_kind(concept),
            "niche": concept["niche"],
            "title_pattern": concept.get("working_title_pattern"),
            "tagline": concept.get("tagline"),
            "creative_intent": concept.get("creative_intent"),
            "visual_direction": concept.get("visual_direction") or {},
            "narration": narration_status(concept),
            "preview_seconds": concept.get("preview_seconds"),
            "content_format": concept["content_format"],
            "target_audience": concept["target_audience"],
            "video_length_minutes": concept["video_length_minutes"],
            "visual_concept": concept["visual_concept"],
            "audio_concept": concept["audio_concept"],
            "audio_requirement": concept["audio_source_requirement"],
            "procedural_visuals_acceptable": bool(concept.get("procedural_visuals_acceptable")),
            "requires_subject_research": bool(concept.get("requires_subject_research")),
            "production_complexity": concept["production_complexity"],
            "risks": concept["risks"],
            "score": round(score(concept), 3),
            "readiness": concept_readiness(concept, capabilities),
        })
    return {"capabilities": capabilities, "concepts": catalog}


def main():
    parser = argparse.ArgumentParser(description="Content experiment framework.")
    sub = parser.add_subparsers(dest="command", required=True)

    p_list = sub.add_parser("list", help="concepts ranked by expected information value")
    p_list.add_argument("--niche", default=None)
    p_list.add_argument("--top", type=int, default=None)
    p_list.set_defaults(func=cmd_list)

    p_show = sub.add_parser("show", help="full detail for one concept")
    p_show.add_argument("concept_id")
    p_show.set_defaults(func=cmd_show)

    p_block = sub.add_parser("blockers", help="what stands between us and production")
    p_block.set_defaults(func=cmd_blockers)

    p_val = sub.add_parser("validate", help="check the concept file is well-formed")
    p_val.set_defaults(func=cmd_validate)

    p_scaf = sub.add_parser("scaffold", help="create a project from a concept")
    p_scaf.add_argument("concept_id")
    p_scaf.add_argument("video_id")
    p_scaf.add_argument("--images", default=None)
    p_scaf.add_argument("--audio", default=None)
    p_scaf.add_argument("--duration", type=float, default=None)
    p_scaf.add_argument("--force", action="store_true")
    p_scaf.set_defaults(func=cmd_scaffold)

    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
