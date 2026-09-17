#!/usr/bin/env python3
"""Format intelligence: what a niche's videos are *shaped* like.

This layer learns structure and statistics - how long videos run, how often
the image changes, how dense the narration is, which sections recur, what the
ambience is. It is deliberately incapable of storing creative expression:
scripts, transcripts, thumbnail imagery and verbatim titles are refused at the
gate, not filtered out later. What survives is the kind of fact you could
state out loud about a category ("these run 8-12 minutes and change image
every 6 seconds"), which is not anyone's property.

Acquisition sits behind adapters so that adding a YouTube or search backend
later cannot leak vendor shapes into project semantics. The internal model is
the contract; a provider's job is to produce ``FormatObservation`` dicts and
nothing else.

    python3 scripts/research.py providers
    python3 scripts/research.py observe --provider local --niche sleep
    python3 scripts/research.py profile --niche sleep
    python3 scripts/research.py show --niche sleep

Standard library only.
"""
import argparse
import json
import logging
import os
import re
import statistics
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="[%(levelname)s] %(message)s")
log = logging.getLogger("research")

ROOT = Path(__file__).resolve().parent.parent
RESEARCH_DIR = ROOT / "research"
OBSERVATIONS_DIR = RESEARCH_DIR / "observations"
PROFILES_DIR = RESEARCH_DIR / "profiles"

OBSERVATION_VERSION = 1
PROFILE_VERSION = 1

# The whole vocabulary. A provider may supply any subset; anything outside
# this set is refused, which is what stops a future adapter from quietly
# widening the model into territory this layer is not allowed to hold.
OBSERVATION_FIELDS = {
    "observation_id",
    "niche",
    "topic",
    "category",
    "title_pattern",
    "thumbnail_characteristics",
    "duration_seconds",
    "hook_structure",
    "intro_structure",
    "pacing",
    "seconds_per_shot",
    "narration_words_per_minute",
    "narration_density",
    "text_on_screen",
    "music_characteristics",
    "ambience",
    "visual_categories",
    "structural_sections",
    "recurring_patterns",
    "source",
    "observed_utc",
    "confidence",
}

REQUIRED_OBSERVATION_FIELDS = ("niche", "source", "confidence")

CONFIDENCE_LEVELS = ("VERIFIED", "INFERRED", "UNVERIFIED")

# Field names a provider might reasonably try to pass through, and must not.
# Refusing by name is blunt on purpose: a rule that inspects content for
# "enough" copying is a rule that eventually lets something through.
PROTECTED_FIELDS = {
    "script", "transcript", "captions", "subtitles", "narration_text",
    "title", "titles", "verbatim_title", "description", "thumbnail",
    "thumbnail_url", "thumbnail_image", "thumbnail_bytes", "frames",
    "screenshots", "creator", "channel_id", "video_id", "video_url",
    "comments",
}

# A pattern describes a shape; a string with no variable part is just
# somebody's title. Requiring a placeholder is a mechanical test for the
# difference, and it is the reason `title_pattern` can exist at all.
_PLACEHOLDER = re.compile(r"(\{[a-z_]+\}|\[[a-z_ ]+\]|<[a-z_]+>)", re.I)

# How much a single observation is worth. A profile drawn from two videos is
# not evidence, and saying so in the artefact is cheaper than remembering it.
_CONFIDENCE_WEIGHT = {"VERIFIED": 1.0, "INFERRED": 0.6, "UNVERIFIED": 0.3}
MIN_OBSERVATIONS_FOR_CONFIDENCE = 5


class ResearchError(Exception):
    """Raised when an observation is malformed or carries protected content."""


def utc_now():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


# --------------------------------------------------------------------------
# the gate
# --------------------------------------------------------------------------

def validate_observation(raw):
    """Return a list of problems with one observation. Empty means usable."""
    problems = []
    if not isinstance(raw, dict):
        return ["observation must be a JSON object"]

    protected = sorted(set(raw) & PROTECTED_FIELDS)
    if protected:
        problems.append(
            "refuses protected creative expression: "
            f"{', '.join(protected)}. This layer records structure and "
            "statistics, never a creator's script, thumbnail or title.")

    unknown = sorted(set(raw) - OBSERVATION_FIELDS - PROTECTED_FIELDS)
    if unknown:
        problems.append(
            f"unknown field(s): {', '.join(unknown)}. Extend "
            "OBSERVATION_FIELDS deliberately rather than smuggling a "
            "provider's shape through.")

    for field in REQUIRED_OBSERVATION_FIELDS:
        if not raw.get(field):
            problems.append(f"'{field}' is required")

    confidence = raw.get("confidence")
    if confidence and confidence not in CONFIDENCE_LEVELS:
        problems.append(
            f"confidence must be one of {', '.join(CONFIDENCE_LEVELS)}, "
            f"got {confidence!r}")

    pattern = raw.get("title_pattern")
    if pattern is not None:
        if not isinstance(pattern, str):
            problems.append("'title_pattern' must be a string")
        elif not _PLACEHOLDER.search(pattern):
            problems.append(
                f"'title_pattern' has no placeholder ({pattern!r}), so it is "
                "a verbatim title rather than a pattern. Write it as e.g. "
                "'{number} Hours of {topic}'.")

    for field in ("duration_seconds", "seconds_per_shot",
                  "narration_words_per_minute"):
        value = raw.get(field)
        if value is not None and (not isinstance(value, (int, float))
                                  or isinstance(value, bool) or value <= 0):
            problems.append(f"'{field}' must be a positive number, got {value!r}")

    for field in ("visual_categories", "structural_sections",
                  "recurring_patterns"):
        value = raw.get(field)
        if value is not None and not isinstance(value, list):
            problems.append(f"'{field}' must be a list")
    return problems


def normalise_observation(raw):
    """Validate and fill in the derived/defaulted fields."""
    problems = validate_observation(raw)
    if problems:
        raise ResearchError("; ".join(problems))
    obs = {k: raw[k] for k in OBSERVATION_FIELDS if k in raw}
    obs.setdefault("observed_utc", utc_now())
    obs.setdefault("observation_id", _observation_id(obs))
    obs["observation_version"] = OBSERVATION_VERSION
    return obs


def _observation_id(obs):
    """Stable id from the fields that identify *what was observed*."""
    import hashlib
    key = json.dumps(
        {k: obs.get(k) for k in ("niche", "topic", "category", "source",
                                 "duration_seconds", "title_pattern")},
        sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(key).hexdigest()[:16]


# --------------------------------------------------------------------------
# providers
# --------------------------------------------------------------------------

class ResearchProvider:
    """Adapter contract: produce raw observation dicts for a niche."""

    name = "base"
    requires_network = False

    def configured(self):
        return True

    def detail(self):
        return ""

    def observe(self, niche, limit=50):
        raise NotImplementedError


class LocalCorpusProvider(ResearchProvider):
    """Observations a human (or an earlier run) wrote down by hand.

    The only provider that works with no credentials and no network, which
    makes it the one the tests and the offline pipeline use.
    """

    name = "local"

    def __init__(self, directory=None):
        self.directory = Path(directory) if directory else OBSERVATIONS_DIR

    def configured(self):
        return self.directory.is_dir()

    def detail(self):
        if not self.directory.is_dir():
            return f"no corpus directory at {self.directory}"
        return f"{len(list(self.directory.glob('*.json')))} file(s) in {self.directory}"

    def observe(self, niche, limit=50):
        if not self.directory.is_dir():
            raise ResearchError(
                f"no observation corpus at {self.directory}. Write one, or "
                f"use a provider that can acquire it.")
        found = []
        for path in sorted(self.directory.glob("*.json")):
            try:
                payload = json.loads(path.read_text())
            except json.JSONDecodeError as e:
                raise ResearchError(f"{path.name} is not valid JSON: {e}")
            entries = payload if isinstance(payload, list) else [payload]
            for entry in entries:
                if entry.get("niche") != niche:
                    continue
                found.append(entry)
        return found[:limit]


class YouTubeProvider(ResearchProvider):
    """Seam for live acquisition. Deliberately inert without a key.

    Present so the shape of the boundary is fixed and tested now: an
    unconfigured provider reports *why* rather than failing somewhere deep in
    a pipeline, and nothing downstream has to know whether the observations
    came from a person or an API.
    """

    name = "youtube"
    requires_network = True

    def __init__(self, api_key=None):
        self.api_key = api_key if api_key is not None else os.environ.get(
            "YOUTUBE_API_KEY", "").strip()

    def configured(self):
        return bool(self.api_key)

    def detail(self):
        if not self.api_key:
            return "YOUTUBE_API_KEY unset; live format research unavailable"
        return "configured"

    def observe(self, niche, limit=50):
        if not self.configured():
            raise ResearchError(
                "youtube provider is not configured (YOUTUBE_API_KEY unset). "
                "Use --provider local, or supply a key. No request was made.")
        raise ResearchError(
            "youtube acquisition is not implemented in this pass. The "
            "provider-neutral model and this seam are: implement observe() "
            "here and nothing downstream changes.")


def build_providers():
    return [LocalCorpusProvider(), YouTubeProvider()]


def get_provider(name):
    for provider in build_providers():
        if provider.name == name:
            return provider
    known = ", ".join(p.name for p in build_providers())
    raise ResearchError(f"unknown research provider {name!r}; known: {known}")


# --------------------------------------------------------------------------
# aggregation
# --------------------------------------------------------------------------

def _median(values):
    clean = [v for v in values if isinstance(v, (int, float))
             and not isinstance(v, bool)]
    return round(statistics.median(clean), 3) if clean else None


def _modes(values, top=5):
    """Most common values with their counts, in a stable order."""
    flat = []
    for value in values:
        if isinstance(value, list):
            flat.extend(str(v) for v in value)
        elif value is not None:
            flat.append(str(value))
    counts = Counter(flat)
    # Sort by count then name so the profile is deterministic.
    ordered = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
    return [{"value": v, "count": c} for v, c in ordered[:top]]


def aggregate(observations, niche):
    """Turn observations into one format profile.

    Medians rather than means: one eight-hour sleep compilation should not
    drag the typical duration of a niche with it.
    """
    if not observations:
        raise ResearchError(f"no observations for niche {niche!r}")

    obs = [normalise_observation(o) for o in observations]
    weight = sum(_CONFIDENCE_WEIGHT.get(o.get("confidence"), 0.3) for o in obs)
    if len(obs) < MIN_OBSERVATIONS_FOR_CONFIDENCE:
        confidence = "UNVERIFIED"
    elif weight >= len(obs) * 0.8:
        confidence = "VERIFIED"
    else:
        confidence = "INFERRED"

    seconds_per_shot = _median([o.get("seconds_per_shot") for o in obs])
    profile = {
        "profile_version": PROFILE_VERSION,
        "niche": niche,
        "built_utc": utc_now(),
        "observation_count": len(obs),
        "observation_ids": sorted(o["observation_id"] for o in obs),
        "sources": sorted({str(o.get("source")) for o in obs}),
        "confidence": confidence,
        "duration_seconds_median": _median([o.get("duration_seconds") for o in obs]),
        "seconds_per_shot_median": seconds_per_shot,
        "narration_words_per_minute_median": _median(
            [o.get("narration_words_per_minute") for o in obs]),
        "title_patterns": _modes([o.get("title_pattern") for o in obs]),
        "thumbnail_characteristics": _modes(
            [o.get("thumbnail_characteristics") for o in obs]),
        "hook_structures": _modes([o.get("hook_structure") for o in obs]),
        "intro_structures": _modes([o.get("intro_structure") for o in obs]),
        "pacing": _modes([o.get("pacing") for o in obs]),
        "narration_density": _modes([o.get("narration_density") for o in obs]),
        "text_on_screen": _modes([o.get("text_on_screen") for o in obs]),
        "music_characteristics": _modes([o.get("music_characteristics") for o in obs]),
        "ambience": _modes([o.get("ambience") for o in obs]),
        "visual_categories": _modes([o.get("visual_categories") for o in obs], top=8),
        "structural_sections": _modes([o.get("structural_sections") for o in obs], top=8),
        "recurring_patterns": _modes([o.get("recurring_patterns") for o in obs], top=8),
        "provenance": {
            "layer": "structural/statistical only",
            "excludes": sorted(PROTECTED_FIELDS),
        },
    }
    return profile


def profile_path(niche):
    return PROFILES_DIR / f"{_slug(niche)}.json"


def _slug(text):
    return re.sub(r"[^a-z0-9]+", "-", str(text).lower()).strip("-") or "unnamed"


def load_profile(niche):
    path = profile_path(niche)
    if not path.is_file():
        return None
    return json.loads(path.read_text())


def save_profile(profile):
    PROFILES_DIR.mkdir(parents=True, exist_ok=True)
    path = profile_path(profile["niche"])
    path.write_text(json.dumps(profile, indent=2, sort_keys=True) + "\n")
    return path


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

def cmd_providers(args):
    for provider in build_providers():
        state = "OK  " if provider.configured() else "DOWN"
        network = " [needs network]" if provider.requires_network else ""
        print(f"{state}  {provider.name:<10} {provider.detail()}{network}")
    return 0


def cmd_observe(args):
    provider = get_provider(args.provider)
    try:
        raw = provider.observe(args.niche, limit=args.limit)
    except ResearchError as e:
        log.error("%s", e)
        return 1
    problems = 0
    for entry in raw:
        issues = validate_observation(entry)
        if issues:
            problems += 1
            log.error("rejected observation: %s", "; ".join(issues))
    log.info("%d observation(s) for niche %r via %s (%d rejected)",
             len(raw) - problems, args.niche, provider.name, problems)
    return 1 if problems else 0


def cmd_profile(args):
    provider = get_provider(args.provider)
    try:
        raw = provider.observe(args.niche, limit=args.limit)
        profile = aggregate(raw, args.niche)
    except ResearchError as e:
        log.error("%s", e)
        return 1
    path = save_profile(profile)
    log.info("Format profile for %r: %d observation(s), confidence %s",
             args.niche, profile["observation_count"], profile["confidence"])
    log.info("  duration median      %ss", profile["duration_seconds_median"])
    log.info("  seconds/shot median  %ss", profile["seconds_per_shot_median"])
    log.info("Written: %s", path)
    return 0


def cmd_show(args):
    profile = load_profile(args.niche)
    if profile is None:
        log.error("No profile for %r. Build one: research.py profile --niche %s",
                  args.niche, args.niche)
        return 1
    print(json.dumps(profile, indent=2, sort_keys=True))
    return 0


def main():
    parser = argparse.ArgumentParser(
        description="Provider-neutral format/competitor intelligence.")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("providers", help="which acquisition adapters are usable")
    p.set_defaults(func=cmd_providers)

    p = sub.add_parser("observe", help="read and validate observations")
    p.add_argument("--niche", required=True)
    p.add_argument("--provider", default="local")
    p.add_argument("--limit", type=int, default=50)
    p.set_defaults(func=cmd_observe)

    p = sub.add_parser("profile", help="aggregate observations into a profile")
    p.add_argument("--niche", required=True)
    p.add_argument("--provider", default="local")
    p.add_argument("--limit", type=int, default=50)
    p.set_defaults(func=cmd_profile)

    p = sub.add_parser("show", help="print a stored profile")
    p.add_argument("--niche", required=True)
    p.set_defaults(func=cmd_show)

    args = parser.parse_args()
    try:
        raise SystemExit(args.func(args))
    except ResearchError as e:
        log.error("%s", e)
        raise SystemExit(1)


if __name__ == "__main__":
    main()
