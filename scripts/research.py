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
import hashlib
import json
import logging
import os
import re
import statistics
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import subject_research  # noqa: E402 - reuses its SearchProvider seam rather than a second one

logging.basicConfig(level=logging.INFO, format="[%(levelname)s] %(message)s")
log = logging.getLogger("research")

ROOT = Path(__file__).resolve().parent.parent
RESEARCH_DIR = ROOT / "research"
OBSERVATIONS_DIR = RESEARCH_DIR / "observations"
PROFILES_DIR = RESEARCH_DIR / "profiles"
BRIEFS_DIR = RESEARCH_DIR / "briefs"
FINDINGS_DIR = RESEARCH_DIR / "findings"
SEARCH_CACHE_DIR = RESEARCH_DIR / "cache"

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


_ISO8601_DURATION = re.compile(
    r"P(?:(?P<days>\d+)D)?T(?:(?P<hours>\d+)H)?(?:(?P<minutes>\d+)M)?(?:(?P<seconds>\d+)S)?")


def _iso8601_seconds(value):
    """YouTube's contentDetails.duration ("PT1H4M9S") in seconds, or None."""
    match = _ISO8601_DURATION.fullmatch((value or "").strip())
    if not match:
        return None
    parts = {k: int(v) for k, v in match.groupdict(default="0").items()}
    return (parts["days"] * 86400 + parts["hours"] * 3600
            + parts["minutes"] * 60 + parts["seconds"]) or None


class YouTubeProvider(ResearchProvider):
    """Live format acquisition through the YouTube Data API v3.

    Produces ``FormatObservation`` dicts and nothing else, which is the
    whole reason this layer has a provider seam: the API's shape (video
    ids, titles, thumbnails, descriptions) stops here. What crosses the
    boundary is structural - how long these videos run, what category they
    sit in, which tags recur - because that is what the model is allowed to
    hold (see ``PROTECTED_FIELDS``). Verbatim titles and per-video URLs are
    never carried, so ``source`` names the query, not a creator's video.

    Inert without ``YOUTUBE_API_KEY``: it says so rather than failing deep
    in a pipeline, and never falls back to a different provider's data.
    """

    name = "youtube"
    requires_network = True
    api_root = "https://www.googleapis.com/youtube/v3"
    timeout_seconds = 20

    def __init__(self, api_key=None):
        self.api_key = api_key if api_key is not None else os.environ.get(
            "YOUTUBE_API_KEY", "").strip()

    def configured(self):
        return bool(self.api_key)

    def detail(self):
        if not self.api_key:
            return "YOUTUBE_API_KEY unset; live format research unavailable"
        return "configured"

    def _get(self, endpoint, params):
        """The one network-touching step, isolated so tests replace it."""
        import urllib.error
        import urllib.parse
        import urllib.request
        query = urllib.parse.urlencode(dict(params, key=self.api_key))
        url = f"{self.api_root}/{endpoint}?{query}"
        try:
            with urllib.request.urlopen(url, timeout=self.timeout_seconds) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            body = ""
            try:
                body = json.loads(e.read().decode("utf-8")).get(
                    "error", {}).get("message", "")
            except Exception:       # noqa: BLE001 - the status is the useful part
                pass
            raise ResearchError(
                f"youtube {endpoint} failed: HTTP {e.code}"
                f"{f' - {body}' if body else ''}") from e
        except urllib.error.URLError as e:
            raise ResearchError(f"youtube {endpoint} is unreachable: {e.reason}") from e

    def _video_details(self, video_ids):
        if not video_ids:
            return []
        payload = self._get("videos", {
            "part": "contentDetails,snippet,statistics",
            "id": ",".join(video_ids[:50]),
            "maxResults": 50,
        })
        return payload.get("items") or []

    def search_video_ids(self, query, limit=25, channel_id=None):
        params = {
            "part": "id", "type": "video", "maxResults": min(int(limit), 50),
            "order": "relevance",
        }
        if channel_id:
            params["channelId"] = channel_id
        if query:
            params["q"] = query
        payload = self._get("search", params)
        return [item["id"]["videoId"] for item in payload.get("items") or []
                if (item.get("id") or {}).get("videoId")]

    def observe(self, niche, limit=50, query=None):
        """Structural observations for a niche, one per returned video.

        Every field is either measured (duration) or a category-level
        attribute (YouTube's own category id, the uploader's tags). Nothing
        here is creative expression, so nothing has to be filtered out
        afterwards - the mapping simply cannot express it.
        """
        if not self.configured():
            raise ResearchError(
                "youtube provider is not configured (YOUTUBE_API_KEY unset). "
                "Use --provider local, or supply a key. No request was made.")
        search_query = query or niche.replace("_", " ")
        video_ids = self.search_video_ids(search_query, limit=min(int(limit), 50))
        observations = []
        for item in self._video_details(video_ids):
            seconds = _iso8601_seconds(
                (item.get("contentDetails") or {}).get("duration"))
            if not seconds:
                continue          # live streams and unparseable durations
            snippet = item.get("snippet") or {}
            tags = [t.strip().lower() for t in (snippet.get("tags") or [])
                    if isinstance(t, str) and t.strip()]
            observations.append({
                "niche": niche,
                "duration_seconds": seconds,
                "category": snippet.get("categoryId"),
                "recurring_patterns": sorted(set(tags))[:12] or None,
                "source": f"youtube-data-api search({search_query!r})",
                "observed_utc": utc_now(),
                "confidence": "VERIFIED",
            })
        if not observations:
            raise ResearchError(
                f"youtube returned no usable videos for {search_query!r} "
                "(no parseable durations)")
        return observations[:limit]

    def describe_seed(self, ref):
        """Structural facts about one explicitly named channel or video.

        A seed is a deliberate instruction - "study this" - so it is
        researched as itself rather than folded into a keyword search. The
        return shape is the finding statement plus the public URL of the
        thing the *user named*, which is provenance for their own
        reference, not extracted creative material.
        """
        kind = (ref.get("type") or "").strip()
        value = (ref.get("value") or "").strip()
        if not value:
            raise ResearchError("seed reference has no value")
        if kind == "video":
            video_id = _youtube_video_id(value)
            if not video_id:
                raise ResearchError(f"could not read a video id out of {value!r}")
            items = self._video_details([video_id])
            url = f"https://www.youtube.com/watch?v={video_id}"
        elif kind == "channel":
            channel_id = self._resolve_channel(value)
            ids = self.search_video_ids(None, limit=25, channel_id=channel_id)
            items = self._video_details(ids)
            url = f"https://www.youtube.com/channel/{channel_id}"
        else:
            raise ResearchError(f"unsupported seed type {kind!r}")
        durations, tags = [], Counter()
        for item in items:
            seconds = _iso8601_seconds(
                (item.get("contentDetails") or {}).get("duration"))
            if seconds:
                durations.append(seconds)
            for tag in (item.get("snippet") or {}).get("tags") or []:
                if isinstance(tag, str) and tag.strip():
                    tags[tag.strip().lower()] += 1
        if not durations:
            raise ResearchError(
                f"youtube returned no usable videos for seed {value!r}")
        statements = [
            (f"{kind} {value}: {len(durations)} video(s) sampled, typical runtime "
             f"{_median(durations) / 60:.0f} min "
             f"(range {min(durations) / 60:.0f}-{max(durations) / 60:.0f} min)"),
        ]
        recurring = [t for t, c in tags.most_common(8) if c >= 2]
        if recurring:
            statements.append(
                f"{kind} {value}: recurring topic tags across its videos: "
                f"{', '.join(recurring)}")
        return {"url": url, "statements": statements,
                "durations": durations, "recurring_tags": recurring}

    def _resolve_channel(self, value):
        """A channel id from an id, @handle or channel URL."""
        text = value.strip()
        match = re.search(r"(?:channel/)?(UC[A-Za-z0-9_-]{22})", text)
        if match:
            return match.group(1)
        handle = re.search(r"@([A-Za-z0-9._-]+)", text)
        params = {"part": "id"}
        if handle:
            params["forHandle"] = "@" + handle.group(1)
        else:
            params["forHandle"] = "@" + text.lstrip("@")
        payload = self._get("channels", params)
        items = payload.get("items") or []
        if not items:
            raise ResearchError(f"youtube knows no channel {value!r}")
        return items[0]["id"]


def _youtube_video_id(value):
    text = value.strip()
    match = re.search(r"(?:v=|youtu\.be/|shorts/|embed/)([A-Za-z0-9_-]{11})", text)
    if match:
        return match.group(1)
    if re.fullmatch(r"[A-Za-z0-9_-]{11}", text):
        return text
    return None


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
# research brief - the operator's own input, per project
# --------------------------------------------------------------------------
#
# A brief is not research; it is what points research at something. It
# stays a separate, small, human-authored artifact (parallel to
# research/subjects/<video_id>.json) so a project can be re-researched
# without re-typing intent, and so the brief itself is visible in the
# dashboard as what it is - a request, not a finding.

BRIEF_FIELDS = {
    "niche", "creative_intent", "likes", "dislikes", "seed_references", "notes",
    "research_topics",
}
REQUIRED_BRIEF_FIELDS = ("niche",)
SEED_REFERENCE_TYPES = ("channel", "video")


def validate_brief(raw):
    problems = []
    if not isinstance(raw, dict):
        return ["brief must be a JSON object"]
    unknown = sorted(set(raw) - BRIEF_FIELDS)
    if unknown:
        problems.append(f"unknown field(s): {', '.join(unknown)}")
    for field in REQUIRED_BRIEF_FIELDS:
        if not raw.get(field):
            problems.append(f"'{field}' is required")
    for field in ("likes", "dislikes"):
        value = raw.get(field)
        if value is not None and not (
                isinstance(value, list) and all(isinstance(v, str) for v in value)):
            problems.append(f"'{field}' must be a list of strings")
    topics = raw.get("research_topics")
    if topics is not None:
        if not isinstance(topics, list):
            problems.append("'research_topics' must be a list")
        else:
            unknown_topics = sorted(set(topics) - set(FINDING_TOPICS))
            if unknown_topics:
                problems.append(
                    f"unknown research_topics: {', '.join(unknown_topics)}; "
                    f"known: {', '.join(FINDING_TOPICS)}")
    refs = raw.get("seed_references")
    if refs is not None:
        if not isinstance(refs, list):
            problems.append("'seed_references' must be a list")
        else:
            for i, ref in enumerate(refs):
                if not isinstance(ref, dict) or not ref.get("value"):
                    problems.append(f"seed_references[{i}] needs a 'value'")
                    continue
                if ref.get("type") not in SEED_REFERENCE_TYPES:
                    problems.append(
                        f"seed_references[{i}].type must be one of "
                        f"{SEED_REFERENCE_TYPES}, got {ref.get('type')!r}")
    return problems


def brief_path(video_id):
    return BRIEFS_DIR / f"{video_id}.json"


def load_brief(video_id):
    """This project's research brief, or ``None`` if none has been written."""
    path = brief_path(video_id)
    if not path.is_file():
        return None
    return json.loads(path.read_text())


def save_brief(video_id, raw):
    """Validate and persist a project's research brief. Returns it normalised."""
    problems = validate_brief(raw)
    if problems:
        raise ResearchError("; ".join(problems))
    brief = {k: raw.get(k) for k in BRIEF_FIELDS if k in raw}
    brief.setdefault("likes", [])
    brief.setdefault("dislikes", [])
    brief.setdefault("seed_references", [])
    brief.setdefault("research_topics", [])
    brief["video_id"] = video_id
    brief["updated_utc"] = utc_now()
    BRIEFS_DIR.mkdir(parents=True, exist_ok=True)
    brief_path(video_id).write_text(json.dumps(brief, indent=2, sort_keys=True) + "\n")
    return brief


# --------------------------------------------------------------------------
# findings - brief-driven competitor/format research, sourced and provenanced
# --------------------------------------------------------------------------
#
# A finding is looser than a FormatObservation (which describes a whole
# niche's shape from many videos) and tighter than a raw search result: it
# keeps a short attributed statement plus where it came from, the same
# "statement + source_url + source_title" shape research_subject already
# uses for sourced facts, extended with what the statement is *about*
# (topic) and how strong a claim it is (kind) - never a verbatim title,
# thumbnail or transcript, matching the protected-fields discipline above.

FINDING_TOPICS = (
    "concept", "visuals", "audio", "pacing", "titles", "thumbnails",
    "audience", "editing", "duration", "presentation", "business",
)

# Which of those a project researches when its brief does not say. Each is
# independently useful, and each is a question about the *format*, never
# about a specific creator's work: what this kind of video looks like,
# sounds like, is titled like, who watches it and how it earns.
DEFAULT_RESEARCH_TOPICS = (
    "concept", "audience", "visuals", "audio", "duration", "titles",
    "thumbnails",
)

# One deliberate query per requested topic, so coverage is designed rather
# than whatever a generic search happened to return. ``{niche}`` and
# ``{intent}`` are the brief's own words - no topic is invented here.
_TOPIC_QUERIES = {
    "concept": "{niche} youtube channels what formats work and why",
    "audience": "who watches {niche} youtube videos and what they say they want",
    "visuals": "{niche} youtube video visual style imagery that performs",
    "audio": "{niche} youtube video music and sound design conventions",
    "pacing": "{niche} youtube video pacing and how often the visuals change",
    "editing": "{niche} youtube video editing and transition conventions",
    "duration": "how long {niche} youtube videos usually run and why",
    "titles": "{niche} youtube title conventions and naming patterns",
    "thumbnails": "{niche} youtube thumbnail design conventions",
    "presentation": "{niche} youtube video presentation and on-screen text conventions",
    "business": "{niche} youtube monetization cpm advertiser suitability and policy risk",
}
# "observation": a statement traceable to one search result (source_url set).
# "interpretation": Content Machine's own synthesis across >=2 observations
# (e.g. a recurring shared term) - never presented as sourced fact by itself.
# "hypothesis": an untested claim a human (or a future synthesis pass) adds
# deliberately; research_project() never manufactures one, because a
# frequency count is an interpretation of what was found, not a guess about
# what has not been tested.
FINDING_KINDS = ("observation", "interpretation", "hypothesis")

MIN_OBSERVATIONS_FOR_INTERPRETATION = 2

_TOPIC_KEYWORDS = (
    (("thumbnail",), "thumbnails"),
    (("title", "titled", "clickbait", "headline"), "titles"),
    (("comment", "audience", "viewer", "reaction", "subscriber"), "audience"),
    (("edit", "cut", "transition"), "editing"),
    (("pacing", "pace", "tempo", "rhythm"), "pacing"),
    (("duration", "minute", "hour", "length", " long", "runtime"), "duration"),
    (("music", "sound", "audio", "ambience", "narration", "voice", "asmr"), "audio"),
    (("visual", "imagery", "footage", "scene", "shot", "color", "colour", "aesthetic"), "visuals"),
    (("concept", "format", "niche", "genre", "channel"), "concept"),
    (("monetiz", "cpm", "rpm", "revenue", "advertiser", "sponsor", "policy"), "business"),
)

_STOPWORDS = frozenset((
    "the", "a", "an", "and", "or", "but", "is", "are", "was", "were", "be",
    "to", "of", "in", "on", "for", "with", "that", "this", "it", "as", "by",
    "at", "from", "its", "their", "they", "you", "your", "mock", "source",
))


def _guess_topic(text):
    lowered = text.lower()
    for keywords, topic in _TOPIC_KEYWORDS:
        if any(k in lowered for k in keywords):
            return topic
    return "presentation"


def _finding_id(video_id, kind, topic, statement):
    key = json.dumps({"video_id": video_id, "kind": kind, "topic": topic,
                      "statement": statement}, sort_keys=True).encode()
    return hashlib.sha256(key).hexdigest()[:16]


def _make_finding(video_id, kind, topic, statement, source_url=None,
                  source_title=None, confidence="UNVERIFIED"):
    return {
        "finding_id": _finding_id(video_id, kind, topic, statement),
        "kind": kind,
        "topic": topic,
        "statement": statement,
        "source_url": source_url,
        "source_title": source_title,
        "confidence": confidence,
        "derived_utc": utc_now(),
    }


def requested_topics(brief):
    """The topics this brief asks to have researched.

    An explicit list is an instruction and is honoured exactly; an empty one
    means "the usual", not "none".
    """
    topics = [t for t in (brief.get("research_topics") or []) if t in FINDING_TOPICS]
    return topics or list(DEFAULT_RESEARCH_TOPICS)


def _search_queries(brief):
    """What to search for, derived from the brief - never invented topics.

    Returns ``(topic, query)`` pairs. Pairing the topic with the query it
    came from is what makes coverage checkable: an answer to the audience
    question is filed under `audience` because that is what was asked, not
    because the words in the snippet happened to match a keyword list.
    """
    niche = (brief.get("niche") or "").strip().replace("_", " ")
    intent = (brief.get("creative_intent") or "").strip()
    if not niche:
        return []
    queries = []
    for topic in requested_topics(brief):
        template = _TOPIC_QUERIES.get(topic)
        if template:
            queries.append((topic, template.format(niche=niche)))
    if intent:
        queries.append(("concept", f"{niche} {intent} youtube"))
    return queries


def _seed_queries(brief):
    """One query per explicitly named channel/video seed.

    Separate from the topic sweep because a seed is a direct instruction:
    the user pointed at something and asked for it to be studied, so a run
    that researched everything *except* the seed has not done what it was
    told (see ``research_project``'s seed contract).
    """
    seeds = []
    for ref in brief.get("seed_references") or []:
        value = (ref.get("value") or "").strip()
        if value:
            seeds.append((ref, f"{value} youtube channel format style "
                               f"audience reaction what it does well"))
    return seeds


def _interpretations(video_id, observations):
    """Deterministic, frequency-counted synthesis across sourced observations.

    Never an LLM call: a shared term across >=2 independently sourced
    statements on the same topic is a fact about *what was found*, safe to
    state without asking a model to editorialise about it.
    """
    by_topic = {}
    for obs in observations:
        by_topic.setdefault(obs["topic"], []).append(obs)
    findings = []
    for topic, obs_list in sorted(by_topic.items()):
        if len(obs_list) < MIN_OBSERVATIONS_FOR_INTERPRETATION:
            continue
        words = Counter()
        for obs in obs_list:
            for word in re.findall(r"[a-z']{4,}", obs["statement"].lower()):
                if word not in _STOPWORDS:
                    words[word] += 1
        shared = [w for w, c in words.most_common(5) if c >= 2]
        if not shared:
            continue
        statement = (f"{len(obs_list)} sourced observation(s) about {topic} "
                     f"recurringly mention: {', '.join(shared)}")
        findings.append(_make_finding(
            video_id, "interpretation", topic, statement, confidence="INFERRED"))
    return findings


def _cache_key(provider_name, query):
    blob = json.dumps({"provider": provider_name, "query": query},
                      sort_keys=True).encode()
    return hashlib.sha256(blob).hexdigest()[:20]


def cached_search(provider, query, max_results, force=False):
    """A search result set, reused across projects.

    A topic query is about a *niche*, not about one video, so the second
    project in the same niche has no reason to pay for the same grounded
    search again. Cached by provider and exact query text, and bypassed by
    ``force``, so refreshing is always possible and never automatic.
    """
    path = SEARCH_CACHE_DIR / f"{_cache_key(provider.name, query)}.json"
    if not force and path.is_file():
        try:
            cached = json.loads(path.read_text())
        except json.JSONDecodeError:
            cached = None
        if cached and cached.get("query") == query:
            return cached.get("results") or [], True
    results = provider.search(query, max_results=max_results)
    SEARCH_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(
        {"provider": provider.name, "query": query, "cached_utc": utc_now(),
         "results": results}, indent=2) + "\n")
    return results, False


def _observations_from_results(video_id, results, topic, fallback_to_guess=True):
    observations = []
    for r in results or []:
        snippet = (r.get("snippet") or "").strip()
        if not r.get("url") or len(snippet) < subject_research.MIN_SNIPPET_CHARS:
            continue
        resolved = topic
        if resolved is None and fallback_to_guess:
            resolved = _guess_topic(snippet)
        observations.append(_make_finding(
            video_id, "observation", resolved or "presentation", snippet,
            source_url=r["url"], source_title=(r.get("title") or "").strip(),
            confidence="VERIFIED"))
    return observations


def _seed_findings(video_id, ref, query, provider, youtube, force):
    """Everything sourced about one explicitly named seed.

    Prefers the YouTube Data API when it is configured, because a seed is a
    specific channel or video and the API can state measured facts about it
    (how long its videos run, which topics recur) rather than whatever the
    open web says about it. Falls back to the search provider otherwise, so
    a seed is still researched when only search is available.
    """
    if youtube is not None and youtube.configured():
        described = youtube.describe_seed(ref)
        return [_make_finding(video_id, "observation", "concept", statement,
                              source_url=described["url"],
                              source_title=(ref.get("value") or "").strip(),
                              confidence="VERIFIED")
                for statement in described["statements"]], "youtube"
    results, _ = cached_search(
        provider, query, subject_research.DEFAULT_MAX_RESULTS, force=force)
    return (_observations_from_results(video_id, results, "concept"),
            subject_research.served_by(results, provider.name))


def research_project(video_id, brief, provider=None, force=False, youtube=None):
    """Brief-driven competitor/format findings for one project, cached once.

    Optional and additive, unlike ``subject_research.research_subject``: a
    project with no brief, or no configured search provider, simply has no
    findings - callers must not treat that as a pipeline failure the way an
    unsourced ``requires_subject_research`` concept is. Raises
    ``ResearchError`` only when a search was attempted and came back too
    thin to say anything sourced.

    Two contracts, deliberately different:

    - **Topic sweep.** One query per requested topic (``research_topics``,
      or ``DEFAULT_RESEARCH_TOPICS``). A topic that returns nothing is
      recorded as uncovered, not fatal - the web may simply have little to
      say about thumbnails in a small niche.
    - **Seeds.** A named channel or video is an instruction to study that
      thing. Every seed must yield at least one sourced finding or this
      raises: silently producing a video "informed by" a reference nobody
      actually looked at is the failure this contract exists to prevent.
    """
    if not force:
        cached = load_findings(video_id)
        if cached is not None:
            return cached

    provider = provider if provider is not None else subject_research._select_provider()
    if provider is None:
        raise ResearchError(
            "no search provider selected. Set SEARXNG_URL, "
            "BRAVE_SEARCH_API_KEY or SEARCH_ORDER/SEARCH_PROVIDER, or "
            "TEST_MODE=1 for the fixture stand-in.")
    if not provider.configured():
        raise ResearchError(f"search provider {provider.name!r} is not configured")
    youtube = youtube if youtube is not None else YouTubeProvider()

    queries = _search_queries(brief)
    seeds = _seed_queries(brief)
    if not queries and not seeds:
        raise ResearchError("brief has no niche or seed references to research from")

    observations, covered, uncovered, providers_used = [], [], [], set()
    for topic, query in queries:
        try:
            results, from_cache = cached_search(
                provider, query, subject_research.DEFAULT_MAX_RESULTS, force=force)
        except subject_research.SearchError as e:
            raise ResearchError(f"search failed for {query!r}: {e}") from e
        found = _observations_from_results(video_id, results, topic)
        if found:
            covered.append(topic)
            providers_used.update(
                subject_research.served_by(results, provider.name).split("+"))
            observations.extend(found)
        else:
            uncovered.append(topic)
        if from_cache:
            log.info("research: %s reused a cached result set", topic)

    seed_coverage = []
    for ref, query in seeds:
        label = (ref.get("value") or "").strip()
        try:
            found, source = _seed_findings(
                video_id, ref, query, provider, youtube, force)
        except (ResearchError, subject_research.SearchError) as e:
            raise ResearchError(
                f"seed {ref.get('type')} {label!r} was named in the research "
                f"brief and could not be researched: {e}. An explicit seed is "
                "an instruction, not a hint - fix the seed or the provider "
                "rather than producing a video that claims to be informed by "
                "it.") from e
        if not found:
            raise ResearchError(
                f"seed {ref.get('type')} {label!r} produced no sourced "
                "finding. Refusing to record research that did not actually "
                "look at a reference the brief named.")
        providers_used.update(source.split("+"))
        observations.extend(found)
        seed_coverage.append({"type": ref.get("type"), "value": label,
                              "provider": source, "findings": len(found)})

    if len(observations) < subject_research.MIN_SOURCES:
        raise ResearchError(
            f"only {len(observations)} adequately-sourced finding(s) across "
            f"{len(queries)} quer{'y' if len(queries) == 1 else 'ies'} "
            f"(need >= {subject_research.MIN_SOURCES}); refusing to record "
            "competitor research this thin")

    findings = observations + _interpretations(video_id, observations)
    artifact = {
        "video_id": video_id,
        "brief_niche": brief.get("niche"),
        "provider": "+".join(sorted(providers_used)) or provider.name,
        "researched_utc": utc_now(),
        "topics_requested": requested_topics(brief),
        "topics_covered": sorted(set(covered)),
        "topics_uncovered": sorted(set(uncovered)),
        "seed_coverage": seed_coverage,
        "findings": findings,
    }
    FINDINGS_DIR.mkdir(parents=True, exist_ok=True)
    findings_path(video_id).write_text(json.dumps(artifact, indent=2) + "\n")
    return artifact


def findings_path(video_id):
    return FINDINGS_DIR / f"{video_id}.json"


def load_findings(video_id):
    """This project's cached findings, or ``None`` if none exist yet."""
    path = findings_path(video_id)
    if not path.is_file():
        return None
    return json.loads(path.read_text())


def findings_digest(findings, max_per_topic=3):
    """A compact, topic-grouped digest for a prompt - never the raw list.

    Mirrors the pipeline's LLM-economy rule (stages hand each other compact
    structured artefacts, never a full dump): one short line per finding,
    capped per topic, kind always stated so the model cannot mistake an
    interpretation for a sourced fact.
    """
    if not findings:
        return ""
    by_topic = {}
    for f in findings["findings"] if isinstance(findings, dict) else findings:
        by_topic.setdefault(f["topic"], []).append(f)
    lines = []
    for topic in sorted(by_topic):
        for f in by_topic[topic][:max_per_topic]:
            tag = f["kind"]
            lines.append(f"- [{topic}/{tag}] {f['statement']}")
    return "\n".join(lines)


# --------------------------------------------------------------------------
# production directives - what the findings actually change about the build
# --------------------------------------------------------------------------
#
# Research that only reaches the title and the image prompt is research the
# finished video does not really carry. This section turns sourced findings
# into the small set of numbers the rest of the pipeline builds from - how
# long a shot is held, how long a dissolve runs, how the camera moves, how
# long the video should be, which audio layers lead - and keeps, for each
# one, the finding ids and source URLs that produced it.
#
# Deterministic, never an LLM, for the same reason ``_interpretations`` is:
# "three sourced statements about pacing say thirty seconds" is a fact about
# what was found. Asking a model to pick a pacing from them would be a
# creative act wearing a citation. A parameter with no supporting evidence
# gets no directive at all, so the consumer keeps its own default rather
# than being handed an invented one.

DIRECTIVES_DIR = RESEARCH_DIR / "directives"

# Shot length floor and ceiling. Below the floor an ambient video is a
# slideshow of flashes; above the ceiling it is a still image with a soundtrack.
MIN_SECONDS_PER_SCENE = 6.0
MAX_SECONDS_PER_SCENE = 45.0

_DURATION_PHRASE = re.compile(
    r"(\d+(?:\.\d+)?)\s*(?:-|to|–)?\s*(\d+(?:\.\d+)?)?\s*"
    r"(hour|hr|minute|min|second|sec)s?\b", re.I)
_UNIT_SECONDS = {"hour": 3600.0, "hr": 3600.0, "minute": 60.0, "min": 60.0,
                 "second": 1.0, "sec": 1.0}

# Vocabulary -> what it implies, with the *reason* stated so a directive can
# say why in the operator's own language rather than naming a keyword list.
_PACE_SIGNALS = (
    (("barely", "imperceptib", "almost still", "static", "unchanging",
      "single image", "one image"), 40.0, "sources describe visuals that barely change"),
    (("slow", "gentle", "gradual", "lingering", "drift", "sustained"), 26.0,
     "sources describe slow, sustained visuals"),
    (("steady", "calm", "measured"), 18.0, "sources describe a steady, calm pace"),
    (("frequent", "fast", "quick", "rapid", "dynamic", "energetic", "cuts every"),
     9.0, "sources describe frequent visual change"),
)

_MOTION_SIGNALS = (
    (("no motion", "static", "still image", "motionless", "dark screen"),
     "still", "sources describe held, motionless frames"),
    (("pan", "ken burns", "parallax", "camera move", "sweep", "travel"),
     "travelling", "sources describe camera movement across the image"),
    (("zoom", "push in", "breathing", "pulse", "drift"), "drifting",
     "sources describe slow drifting or breathing movement"),
)

_TRANSITION_SIGNALS = (
    (("crossfade", "dissolve", "fade between", "blend", "seamless"), 2.0,
     "sources describe long dissolves between shots"),
    (("hard cut", "cuts", "cut to", "jump cut"), 0.4,
     "sources describe cuts rather than dissolves"),
)

# Audio layers this pipeline can actually build, and the words that call for
# them. Nothing is listed here that sound_design cannot produce, so a
# directive never asks for a layer that would then fail closed.
_AUDIO_SIGNALS = (
    ("rain", ("rain", "rainfall", "downpour", "storm", "thunder")),
    ("water", ("stream", "river", "waves", "ocean", "water", "creek")),
    ("wind", ("wind", "breeze", "forest", "leaves", "trees")),
    ("fire", ("fire", "campfire", "crackle", "fireplace", "hearth")),
    ("night", ("crickets", "night", "owl", "insects")),
    ("music", ("music", "piano", "pad", "chord", "melody", "ambient music",
               "soundtrack", "score")),
    ("noise", ("brown noise", "white noise", "pink noise", "noise")),
    ("binaural", ("binaural", "isochronic", "hz", "delta", "theta")),
)


def _evidence(observations):
    """Finding ids and source URLs behind one directive, deduplicated."""
    ids, urls = [], []
    for obs in observations:
        if obs["finding_id"] not in ids:
            ids.append(obs["finding_id"])
        url = obs.get("source_url")
        if url and url not in urls:
            urls.append(url)
    return ids, urls


def _directive(parameter, value, rationale, observations, confidence="INFERRED"):
    ids, urls = _evidence(observations)
    return {
        "parameter": parameter,
        "value": value,
        "rationale": rationale,
        "evidence_finding_ids": ids,
        "source_urls": urls,
        "confidence": confidence,
    }


def _observations_for(findings, topics):
    """Sourced observations on the given topics. Interpretations are excluded
    deliberately: a directive cites what a source said, not this project's own
    word-frequency count across them."""
    items = findings["findings"] if isinstance(findings, dict) else (findings or [])
    return [f for f in items
            if f.get("kind") == "observation" and f.get("topic") in topics]


def _durations_in(text, units=("hour", "hr", "minute", "min")):
    """Every duration a sentence states, in seconds. A range counts as its
    midpoint, which is what "20 to 40 minutes" actually tells us."""
    found = []
    for low, high, unit in _DURATION_PHRASE.findall(text or ""):
        unit = unit.lower()
        if unit not in units:
            continue
        scale = _UNIT_SECONDS[unit]
        values = [float(low) * scale]
        if high:
            values.append(float(high) * scale)
        found.append(sum(values) / len(values))
    return found


def _signal_directive(parameter, signals, observations, clamp=None):
    """The first matching signal across the observations that mention one.

    Signals are ordered from most to least specific, and the *most* common
    match wins rather than the first sentence that happens to contain a word,
    so one offhand mention cannot outvote three deliberate ones.
    """
    hits = {}
    for obs in observations:
        lowered = (obs.get("statement") or "").lower()
        for keywords, value, rationale in signals:
            if any(k in lowered for k in keywords):
                entry = hits.setdefault((value, rationale), [])
                entry.append(obs)
                break
    if not hits:
        return None
    (value, rationale), supporting = max(hits.items(), key=lambda kv: len(kv[1]))
    if clamp:
        value = max(clamp[0], min(clamp[1], value))
    return _directive(
        parameter, value,
        f"{rationale} ({len(supporting)} sourced observation(s))", supporting)


def production_directives(findings, brief=None, target_seconds=None):
    """Turn sourced findings into the parameters the build actually uses.

    Returns ``{"values": {...}, "decisions": [...], ...}``. ``values`` is what
    the storyboard and audio stages read; ``decisions`` is why, with the
    finding ids and URLs behind each one, so the dashboard can show a person
    what the research changed and let them disagree with it.

    An empty ``decisions`` list is a legitimate outcome: research that said
    nothing about pacing must not invent a pacing.
    """
    decisions = []

    pace_obs = _observations_for(findings, ("pacing", "editing", "visuals"))
    stated = []
    for obs in pace_obs:
        lowered = (obs.get("statement") or "").lower()
        if any(word in lowered for word in
               ("per shot", "each shot", "every", "shot length", "scene",
                "image change", "changes every", "held for")):
            stated.extend(s for s in _durations_in(
                obs["statement"], units=("minute", "min", "second", "sec"))
                if MIN_SECONDS_PER_SCENE <= s <= MAX_SECONDS_PER_SCENE)
    if stated:
        decisions.append(_directive(
            "seconds_per_scene", round(_median(stated), 1),
            f"sources state how long a shot is held ({len(stated)} stated value(s))",
            pace_obs, confidence="VERIFIED"))
    else:
        signal = _signal_directive(
            "seconds_per_scene", _PACE_SIGNALS, pace_obs,
            clamp=(MIN_SECONDS_PER_SCENE, MAX_SECONDS_PER_SCENE))
        if signal:
            decisions.append(signal)

    motion = _signal_directive(
        "motion_style", _MOTION_SIGNALS,
        _observations_for(findings, ("visuals", "editing", "pacing")))
    if motion:
        decisions.append(motion)

    transition = _signal_directive(
        "transition_seconds", _TRANSITION_SIGNALS,
        _observations_for(findings, ("editing", "pacing")))
    if transition:
        decisions.append(transition)

    duration_obs = _observations_for(findings, ("duration",))
    lengths = []
    for obs in duration_obs:
        lengths.extend(_durations_in(obs["statement"]))
    lengths = [s for s in lengths if 300.0 <= s <= 12 * 3600.0]
    if lengths:
        decisions.append(_directive(
            "typical_duration_seconds", round(_median(lengths)),
            f"sources state how long videos in this niche run "
            f"({len(lengths)} stated value(s))",
            duration_obs, confidence="VERIFIED"))

    audio_obs = _observations_for(findings, ("audio",))
    emphasis, audio_support = [], []
    for layer, keywords in _AUDIO_SIGNALS:
        supporting = [o for o in audio_obs
                      if any(k in (o.get("statement") or "").lower() for k in keywords)]
        if supporting:
            emphasis.append(layer)
            audio_support.extend(supporting)
    if emphasis:
        decisions.append(_directive(
            "audio_emphasis", emphasis,
            f"sources name these sound layers for this niche: {', '.join(emphasis)}",
            audio_support))

    values = {d["parameter"]: d["value"] for d in decisions}
    if target_seconds and "seconds_per_scene" in values:
        # A directive that would produce a single shot, or thousands, is a
        # misread of the evidence rather than a finding about this video.
        scenes = max(int(round(float(target_seconds) / values["seconds_per_scene"])), 1)
        values["scene_count"] = min(scenes, 1200)

    return {
        "video_id": (findings or {}).get("video_id") if isinstance(findings, dict) else None,
        "derived_utc": utc_now(),
        "brief_niche": (brief or {}).get("niche"),
        "findings_researched_utc": (findings or {}).get("researched_utc")
        if isinstance(findings, dict) else None,
        "values": values,
        "decisions": decisions,
    }


def directives_path(video_id):
    return DIRECTIVES_DIR / f"{video_id}.json"


def save_directives(video_id, directives):
    DIRECTIVES_DIR.mkdir(parents=True, exist_ok=True)
    directives_path(video_id).write_text(json.dumps(directives, indent=2) + "\n")
    return directives


def load_directives(video_id):
    path = directives_path(video_id)
    if not path.is_file():
        return None
    return json.loads(path.read_text())


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


def cmd_brief_show(args):
    brief = load_brief(args.video_id)
    if brief is None:
        log.error("No research brief for %s.", args.video_id)
        return 1
    print(json.dumps(brief, indent=2, sort_keys=True))
    return 0


def cmd_brief_set(args):
    raw = json.loads(Path(args.file).read_text()) if args.file else json.loads(sys.stdin.read())
    brief = save_brief(args.video_id, raw)
    log.info("Saved research brief for %s (niche=%r)", args.video_id, brief.get("niche"))
    return 0


def cmd_findings(args):
    brief = load_brief(args.video_id)
    if brief is None:
        log.error("No research brief for %s. Set one first: research.py brief-set %s",
                  args.video_id, args.video_id)
        return 1
    artifact = research_project(args.video_id, brief, force=args.force)
    log.info("Findings for %s: %d finding(s) via %s", args.video_id,
             len(artifact["findings"]), artifact["provider"])
    print(json.dumps(artifact, indent=2))
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

    p = sub.add_parser("brief-show", help="print a project's research brief")
    p.add_argument("video_id")
    p.set_defaults(func=cmd_brief_show)

    p = sub.add_parser("brief-set", help="set a project's research brief from JSON (--file or stdin)")
    p.add_argument("video_id")
    p.add_argument("--file", default=None)
    p.set_defaults(func=cmd_brief_set)

    p = sub.add_parser("findings", help="run (or reuse) brief-driven research for a project")
    p.add_argument("video_id")
    p.add_argument("--force", action="store_true")
    p.set_defaults(func=cmd_findings)

    args = parser.parse_args()
    try:
        raise SystemExit(args.func(args))
    except ResearchError as e:
        log.error("%s", e)
        raise SystemExit(1)


if __name__ == "__main__":
    main()
