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
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import project as project_mod  # noqa: E402

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


def cmd_scaffold(args):
    """Turn a concept into a real project directory."""
    _, concepts = load_concepts()
    concept = next((c for c in concepts if c["id"] == args.concept_id), None)
    if not concept:
        log.error("No such concept: %s", args.concept_id)
        return 1

    pdir = project_mod.project_dir(args.video_id)
    if pdir.exists() and not args.force:
        log.error("Project already exists: %s (use --force)", pdir)
        return 1

    template_path = EXPERIMENTS_DIR / concept["spec_template"]
    spec = json.loads(template_path.read_text())
    spec.pop("_comment", None)
    if args.duration:
        spec["duration_seconds"] = float(args.duration)

    for sub in project_mod.SUBDIRS:
        (pdir / sub).mkdir(parents=True, exist_ok=True)

    assets_ready = False
    if args.images and args.audio:
        images_src = Path(args.images).expanduser().resolve()
        audio_src = Path(args.audio).expanduser().resolve()
        sources = project_mod.list_assets(images_src, project_mod.SUPPORTED_IMAGE_EXTENSIONS)
        if not sources:
            log.error("No supported images found in %s", images_src)
            return 1
        if not audio_src.is_file():
            log.error("Audio file not found: %s", audio_src)
            return 1
        for image in sources:
            project_mod.ingest(image, pdir / "images")
        audio_dest, _ = project_mod.ingest(audio_src, pdir / "audio")
        spec["audio"]["file"] = f"audio/{audio_dest.name}"
        assets_ready = True
        log.info("Ingested %d image(s) + audio", len(sources))

    (pdir / "video_spec.json").write_text(json.dumps(spec, indent=2) + "\n")

    metadata = project_mod.build_metadata(
        args.video_id,
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
    if assets_ready:
        log.info("Assets present. Render with: ./content-machine run %s", args.video_id)
    else:
        log.info("Assets PENDING. Add images to %s/images and audio to %s/audio,",
                 pdir.name, pdir.name)
        log.info("then: ./content-machine validate %s", args.video_id)
        log.info("Audio requirement: %s", AUDIO_CAPABILITY[concept["audio_source_requirement"]])
    return 0


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
