#!/usr/bin/env python3
"""Subject research: source-backed facts about one video's topic, cached once.

A small number of concepts are not just staged imagery - they narrate real
claims about the world (``story-sleepy-history-adult``'s "dry factual
material" is the example this module exists for). An LLM asked to write that
narration from nothing but a one-line concept description will confidently
invent history. This module exists to stop that: it never asks a model what
it "knows" about a subject. It asks a search provider, keeps only what the
provider actually returned with a source attached, and refuses outright if
that isn't enough to work with.

Three properties are load-bearing:

**Search-backed, never memory-backed.** ``research_subject`` calls a
``SearchProvider`` and turns its results into facts. There is no code path
here that asks an LLM to supply a "fact" - that would be exactly the
hallucination risk this module is refusing to take on.

**Fail closed.** No configured provider, or too few adequately-sourced
results, raises ``SubjectResearchError`` rather than returning something thin
or falling back to model knowledge. "Nobody could source it" is not "publish
it anyway".

**Research once, cache always.** A video's subject research is written to
``research/subjects/<video-id>.json`` and every later stage (``creative``,
``storyboard``) reads that file rather than searching again. Facts are kept
separate from creative interpretation: this module writes down only what a
source said and where it said it - turning that into a narration script or an
image prompt is ``creative.py``'s job, not this one's.

Search vendors sit behind ``SearchProvider`` so adding a real one later is a
new subclass, not a change to any caller - the same shape as the image
generation providers in ``scripts/generation.py``. No production web-search
vendor is wired in yet: only the local, deterministic ``FixtureSearchProvider``
exists today, matching the paid-provider rule ("off unless explicitly
configured, and never on by accident") already applied to image generation.

    python3 scripts/subject_research.py research <video-id> --concept-id ID
    python3 scripts/subject_research.py show <video-id>

Standard library only.
"""
import argparse
import json
import logging
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="[%(levelname)s] %(message)s")
log = logging.getLogger("subject_research")

ROOT = Path(__file__).resolve().parent.parent
SUBJECTS_DIR = ROOT / "research" / "subjects"

SUBJECT_RESEARCH_VERSION = 1

# Adequacy floor. Below this, a "researched" video is really an unresearched
# one with two footnotes - fail closed rather than publish it as sourced.
MIN_SOURCES = 2
MIN_SNIPPET_CHARS = 40
DEFAULT_MAX_RESULTS = 5


class SubjectResearchError(Exception):
    """Raised when a subject cannot be adequately, honestly sourced."""


class SearchError(Exception):
    """Raised by a SearchProvider when a search attempt itself fails."""


def _env(name, default=None):
    value = os.environ.get(name)
    return value if value not in (None, "") else default


def utc_now():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


# --------------------------------------------------------------------------
# search providers - vendor specifics stay inside the subclass
# --------------------------------------------------------------------------

class SearchProvider:
    """Common provider interface. Subclasses own all vendor specifics."""

    name = "base"

    def configured(self):
        raise NotImplementedError

    def search(self, query, max_results=DEFAULT_MAX_RESULTS):
        """Return a list of ``{"title", "url", "snippet"}`` dicts.

        Must never raise for "no results" - return ``[]``. Raise
        ``SearchError`` only when the attempt itself failed (network error,
        malformed vendor response).
        """
        raise NotImplementedError


class FixtureSearchProvider(SearchProvider):
    """Local, deterministic stand-in - no network, ever.

    This is the "local stand-in" this project's tests always use in place of
    a real external service. It is also the only provider ``TEST_MODE=1``
    will ever select, so the test suite can exercise the whole
    research-required path without a search vendor existing.
    """

    name = "fixture"

    def configured(self):
        return True

    def search(self, query, max_results=DEFAULT_MAX_RESULTS):
        canned = [
            {
                "title": f"[MOCK SOURCE 1] {query}",
                "url": "https://mock.test/source-1",
                "snippet": f"[MOCK] A sourced-looking sentence about {query}, "
                           "standing in for a real search result in tests.",
            },
            {
                "title": f"[MOCK SOURCE 2] {query}",
                "url": "https://mock.test/source-2",
                "snippet": f"[MOCK] A second sourced-looking sentence about "
                           f"{query}, from a different mock source.",
            },
        ]
        return canned[:max_results]


def build_search_providers():
    """Every search provider this build knows how to construct.

    No production web-search vendor is wired in yet - deliberately. Adding
    one is a new ``SearchProvider`` subclass registered here; no caller
    changes. Until then, ``SEARCH_PROVIDER`` pointing at anything but
    ``fixture`` resolves to nothing, and subject research fails closed.
    """
    return {"fixture": FixtureSearchProvider()}


def _select_provider(providers=None):
    providers = providers if providers is not None else build_search_providers()
    if os.environ.get("TEST_MODE") == "1":
        return providers.get("fixture")
    name = _env("SEARCH_PROVIDER")
    if not name:
        return None
    return providers.get(name)


# --------------------------------------------------------------------------
# topic derivation - deterministic, no LLM
# --------------------------------------------------------------------------

_PATTERN_SEP = re.compile(r"\s*\|\s*")


def topic_query(concept):
    """The search query for a concept's subject.

    ``working_title_pattern`` carries the concrete topic before its first
    ``|`` (e.g. "The History of the Suez Canal | Boring Story for Sleep | 60
    Minutes" -> "The History of the Suez Canal"); ``content_format`` is the
    fallback for a concept with no title pattern yet.
    """
    pattern = (concept.get("working_title_pattern") or "").strip()
    if pattern:
        return _PATTERN_SEP.split(pattern)[0].strip()
    return (concept.get("content_format") or "").strip() or None


# --------------------------------------------------------------------------
# research, cache, load
# --------------------------------------------------------------------------

def subject_path(video_id):
    return SUBJECTS_DIR / f"{video_id}.json"


def load_subject_research(video_id):
    """Cached research for a video, or None if none exists yet."""
    path = subject_path(video_id)
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return None


def _save(video_id, artifact):
    path = subject_path(video_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(artifact, indent=2) + "\n")
    tmp.replace(path)
    return path


def research_subject(video_id, concept, provider=None, force=False):
    """Source-backed facts for one video's subject, cached to disk.

    Returns the cached artifact unchanged if one already exists and
    ``force`` is not set - downstream stages call this and get the cache,
    never a repeat search. Raises ``SubjectResearchError`` if no provider is
    selected/configured, or if what came back cannot be adequately sourced;
    it never substitutes model knowledge for either.
    """
    if not force:
        cached = load_subject_research(video_id)
        if cached is not None:
            return cached

    provider = provider if provider is not None else _select_provider()
    if provider is None:
        raise SubjectResearchError(
            "no search provider selected. Set SEARCH_PROVIDER to a "
            "configured provider, or TEST_MODE=1 for the fixture stand-in. "
            "Refusing to write subject facts from model memory alone.")
    if not provider.configured():
        raise SubjectResearchError(f"search provider {provider.name!r} is not configured")

    query = topic_query(concept)
    if not query:
        raise SubjectResearchError(
            f"concept {concept.get('id')!r} has no working_title_pattern or "
            "content_format to derive a research topic from")

    try:
        results = provider.search(query, max_results=DEFAULT_MAX_RESULTS)
    except SearchError as e:
        raise SubjectResearchError(f"search failed for {query!r}: {e}") from e

    adequate = [
        r for r in (results or [])
        if r.get("url") and len((r.get("snippet") or "").strip()) >= MIN_SNIPPET_CHARS
    ]
    if len(adequate) < MIN_SOURCES:
        raise SubjectResearchError(
            f"only {len(adequate)} adequately-sourced result(s) for {query!r} "
            f"(need >= {MIN_SOURCES}); refusing to research this subject from "
            "thin or absent sources rather than fall back to model memory")

    facts = [
        {
            "statement": r["snippet"].strip(),
            "source_url": r["url"],
            "source_title": (r.get("title") or "").strip(),
        }
        for r in adequate
    ]
    artifact = {
        "subject_research_version": SUBJECT_RESEARCH_VERSION,
        "video_id": video_id,
        "concept_id": concept.get("id"),
        "query": query,
        "provider": provider.name,
        "researched_utc": utc_now(),
        # Sourced facts only - no creative interpretation. creative.py turns
        # these into narration/prompts; this module never does that itself.
        "facts": facts,
        "sources": [{"url": f["source_url"], "title": f["source_title"]} for f in facts],
    }
    _save(video_id, artifact)
    return artifact


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

def cmd_research(args):
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from experiment import load_concepts

    _, concepts = load_concepts()
    concept = next((c for c in concepts if c["id"] == args.concept_id), None)
    if concept is None:
        log.error("No such concept: %s", args.concept_id)
        return 1
    try:
        artifact = research_subject(args.video_id, concept, force=args.force)
    except SubjectResearchError as e:
        log.error("Subject research failed closed: %s", e)
        return 1
    log.info("Subject research: %d sourced fact(s) for %r via %s",
              len(artifact["facts"]), artifact["query"], artifact["provider"])
    print(json.dumps(artifact, indent=2))
    return 0


def cmd_show(args):
    artifact = load_subject_research(args.video_id)
    if artifact is None:
        log.error("No cached subject research for %s", args.video_id)
        return 1
    print(json.dumps(artifact, indent=2))
    return 0


def main():
    parser = argparse.ArgumentParser(description="Source-backed subject research for a video.")
    sub = parser.add_subparsers(dest="command", required=True)

    p_research = sub.add_parser("research", help="research and cache one video's subject")
    p_research.add_argument("video_id")
    p_research.add_argument("--concept-id", dest="concept_id", required=True)
    p_research.add_argument("--force", action="store_true",
                            help="re-research even if a cached artifact exists")
    p_research.set_defaults(func=cmd_research)

    p_show = sub.add_parser("show", help="print a video's cached subject research")
    p_show.add_argument("video_id")
    p_show.set_defaults(func=cmd_show)

    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
