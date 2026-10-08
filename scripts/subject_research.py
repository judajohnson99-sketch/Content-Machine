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

Search vendors sit behind ``SearchProvider`` so adding one is a new
subclass, not a change to any caller - the same shape as the image
generation providers in ``scripts/generation.py``. ``SearchRouter`` tries
them cheapest-first: a self-hosted SearXNG instance (``SEARXNG_URL``) is the
free primary, Brave's API (``BRAVE_SEARCH_API_KEY``) a metered fallback,
Wikipedia a keyless opt-in, and the LLM-grounded Gemini/Anthropic providers
are always last and only ever reached when named in ``SEARCH_ORDER`` (or
``SEARCH_PROVIDER``) - the paid-provider rule ("off unless explicitly
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

# Cost tiers. The router sorts by these so a paid provider is always last,
# whatever order it was configured in.
TIER_FREE, TIER_METERED, TIER_PAID = 0, 1, 2
TIER_NAMES = {TIER_FREE: "free", TIER_METERED: "metered", TIER_PAID: "paid"}

# The route when neither SEARCH_ORDER nor SEARCH_PROVIDER is set. Each only
# participates once its own setting exists (SEARXNG_URL, BRAVE_SEARCH_API_KEY).
DEFAULT_SEARCH_ORDER = ("searxng", "brave")
DEFAULT_SEARCH_TIMEOUT = 15.0
DEFAULT_SEARCH_COOLDOWN = 300.0


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
    tier = TIER_FREE

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
    tier = TIER_FREE

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


_GEMINI_SEARCH_SNIPPET = """
import json, os, sys
from dotenv import load_dotenv
load_dotenv()
from google import genai
from google.genai import types
client = genai.Client()
query = sys.stdin.read().strip()
response = client.models.generate_content(
    model=os.environ.get("RESEARCH_MODEL", os.environ.get("CREATIVE_MODEL", "gemini-3.5-flash-lite")),
    contents=(
        "Search the web for: " + query + "\\n"
        "Summarise what the top sources say as short factual sentences. "
        "Every sentence must be grounded in a search result."),
    config=types.GenerateContentConfig(
        tools=[types.Tool(google_search=types.GoogleSearch())], temperature=0.0),
)
out = {"chunks": [], "supports": []}
candidate = (response.candidates or [None])[0]
meta = getattr(candidate, "grounding_metadata", None) if candidate else None
for chunk in (getattr(meta, "grounding_chunks", None) or []):
    web = getattr(chunk, "web", None)
    out["chunks"].append({"uri": getattr(web, "uri", None), "title": getattr(web, "title", None)})
for support in (getattr(meta, "grounding_supports", None) or []):
    segment = getattr(support, "segment", None)
    out["supports"].append({"text": getattr(segment, "text", None),
                            "chunks": list(getattr(support, "grounding_chunk_indices", None) or [])})
sys.stdout.write(json.dumps(out))
"""


class GeminiSearchProvider(SearchProvider):
    """Web search through Gemini's Google Search grounding.

    Each result is one *grounding support*: a sentence the model wrote that
    Google's grounding metadata attributes to a specific retrieved page.
    The page's URI and title are the source; the supported sentence is the
    snippet. Nothing un-attributed is returned, so an observation built
    from this provider is still traceable to one URL, exactly as the
    fixture's are. Uses the same GEMINI_API_KEY and the same
    subprocess-into-the-SDK pattern as ``creative``; a grounded request is
    a paid call, so this provider only runs when ``SEARCH_PROVIDER=gemini``
    is set - never by default.
    """

    name = "gemini"
    tier = TIER_PAID

    def configured(self):
        return bool(_env("GEMINI_API_KEY"))

    def _run(self, query):
        """The one network-touching step, isolated so tests replace it."""
        import subprocess
        import creative  # local: creative already knows where the SDK lives
        python = creative._sdk_python("gemini")
        if not python:
            raise SearchError("no Python interpreter has the google-genai SDK installed")
        try:
            result = subprocess.run(
                [python, "-c", _GEMINI_SEARCH_SNIPPET], input=query,
                capture_output=True, text=True, cwd=str(ROOT), timeout=120)
        except subprocess.TimeoutExpired as e:
            raise SearchError("gemini grounded search timed out") from e
        if result.returncode != 0:
            lines = [l for l in result.stderr.strip().splitlines() if l.strip()]
            raise SearchError(f"gemini grounded search failed: "
                              f"{lines[-1][-300:] if lines else result.returncode}")
        return result.stdout

    def search(self, query, max_results=DEFAULT_MAX_RESULTS):
        if not self.configured():
            raise SearchError("gemini search provider is not configured (GEMINI_API_KEY unset)")
        try:
            payload = json.loads(self._run(query) or "{}")
        except ValueError as e:
            raise SearchError(f"gemini grounded search returned malformed JSON: {e}") from e
        chunks = payload.get("chunks") or []
        results, seen = [], set()
        for support in payload.get("supports") or []:
            text = (support.get("text") or "").strip()
            if not text:
                continue
            for index in support.get("chunks") or []:
                if not isinstance(index, int) or index < 0 or index >= len(chunks):
                    continue
                chunk = chunks[index]
                url = (chunk.get("uri") or "").strip()
                if not url or (url, text) in seen:
                    continue
                seen.add((url, text))
                results.append({"title": (chunk.get("title") or "").strip(),
                                "url": url, "snippet": text})
                break   # one source per supported sentence keeps provenance one-to-one
            if len(results) >= max_results:
                break
        return results


_ANTHROPIC_SEARCH_SNIPPET = """
import json, os, sys
from dotenv import load_dotenv
load_dotenv()
from anthropic import Anthropic
client = Anthropic()
query = sys.stdin.read().strip()
message = client.messages.create(
    model=os.environ.get("RESEARCH_MODEL", "claude-sonnet-4-5"),
    max_tokens=2048,
    tools=[{"type": "web_search_20250305", "name": "web_search", "max_uses": 4}],
    messages=[{"role": "user", "content": (
        "Search the web for: " + query + "\\n"
        "Then write short factual sentences summarising what the sources say. "
        "Every sentence must be supported by a search result you actually "
        "retrieved. Do not add anything you did not find.")}],
)
out = []
for block in message.content:
    if getattr(block, "type", None) != "text":
        continue
    for citation in (getattr(block, "citations", None) or []):
        url = getattr(citation, "url", None)
        if not url:
            continue
        out.append({"title": getattr(citation, "title", None) or "",
                    "url": url,
                    "snippet": getattr(citation, "cited_text", None) or block.text})
sys.stdout.write(json.dumps(out))
"""


class AnthropicSearchProvider(SearchProvider):
    """Web search through Claude's server-side ``web_search`` tool.

    Each result is one *citation*: a passage the model retrieved, with the
    page it came from. Same contract as the Gemini provider - nothing
    un-attributed is returned, so an observation built from this is
    traceable to one URL - and the same opt-in rule: a search is a paid
    call, so this provider only runs when ``SEARCH_PROVIDER=anthropic`` is
    set. It exists alongside the Gemini one because a single vendor's
    grounding quota must not be the only thing standing between this build
    and sourced research.
    """

    name = "anthropic"
    tier = TIER_PAID

    def configured(self):
        return bool(_env("ANTHROPIC_API_KEY"))

    def _run(self, query):
        """The one network-touching step, isolated so tests replace it."""
        import subprocess
        import creative

        python = creative._sdk_python("anthropic")
        if not python:
            raise SearchError("no Python interpreter has the anthropic SDK installed")
        try:
            result = subprocess.run(
                [python, "-c", _ANTHROPIC_SEARCH_SNIPPET], input=query,
                capture_output=True, text=True, cwd=str(ROOT), timeout=180)
        except subprocess.TimeoutExpired as e:
            raise SearchError("anthropic web search timed out") from e
        if result.returncode != 0:
            lines = [l for l in result.stderr.strip().splitlines() if l.strip()]
            raise SearchError(f"anthropic web search failed: "
                              f"{lines[-1][-300:] if lines else result.returncode}")
        return result.stdout

    def search(self, query, max_results=DEFAULT_MAX_RESULTS):
        if not self.configured():
            raise SearchError(
                "anthropic search provider is not configured (ANTHROPIC_API_KEY unset)")
        try:
            payload = json.loads(self._run(query) or "[]")
        except ValueError as e:
            raise SearchError(f"anthropic web search returned malformed JSON: {e}") from e
        results, seen = [], set()
        for item in payload if isinstance(payload, list) else []:
            url = (item.get("url") or "").strip()
            snippet = " ".join((item.get("snippet") or "").split())
            if not url or not snippet or (url, snippet) in seen:
                continue
            seen.add((url, snippet))
            results.append({"title": (item.get("title") or "").strip(),
                            "url": url, "snippet": snippet})
            if len(results) >= max_results:
                break
        return results


# --------------------------------------------------------------------------
# low-cost web search providers - plain HTTP, standard library only
# --------------------------------------------------------------------------

_USER_AGENT = "ContentMachine/1.0 (subject research; +https://github.com/judajohnson99-sketch/Content-Machine)"
_TAG = re.compile(r"<[^>]+>")


def _clean_text(text):
    """Vendor snippets carry highlight markup and entities; facts must not."""
    import html
    return " ".join(html.unescape(_TAG.sub("", text or "")).split())


def _search_timeout():
    try:
        return float(_env("SEARCH_TIMEOUT", DEFAULT_SEARCH_TIMEOUT))
    except ValueError:
        return DEFAULT_SEARCH_TIMEOUT


def _http_get_json(url, headers=None, timeout=None, label="search"):
    """GET ``url`` and decode JSON, turning every failure into ``SearchError``."""
    import urllib.error
    import urllib.request

    request = urllib.request.Request(url, headers={
        "Accept": "application/json", "User-Agent": _USER_AGENT, **(headers or {})})
    try:
        with urllib.request.urlopen(request, timeout=timeout or _search_timeout()) as response:
            body = response.read()
    except urllib.error.HTTPError as e:
        raise SearchError(f"{label} returned HTTP {e.code}") from e
    except (urllib.error.URLError, OSError) as e:
        raise SearchError(f"{label} unreachable: {getattr(e, 'reason', e)}") from e
    try:
        return json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as e:
        raise SearchError(f"{label} returned malformed JSON: {e}") from e


class SearXNGSearchProvider(SearchProvider):
    """A self-hosted SearXNG metasearch instance - the free primary route.

    ``SEARXNG_URL`` is the instance's base URL and has no default: unset
    means "there is no instance", exactly as an unset ``COMFYUI_URL`` means
    the PC is off. The instance must allow the JSON output format
    (``search.formats`` in its ``settings.yml``); one that does not answers
    403, which is reported as such rather than as "no results".
    ``SEARXNG_ENGINES`` and ``SEARXNG_LANGUAGE`` are passed through when set.
    """

    name = "searxng"
    tier = TIER_FREE

    def configured(self):
        return bool(_env("SEARXNG_URL"))

    def _fetch(self, url):
        """The one network-touching step, isolated so tests replace it."""
        try:
            return _http_get_json(url, label="searxng")
        except SearchError as e:
            if "HTTP 403" in str(e):
                raise SearchError("searxng refused JSON output (HTTP 403); enable "
                                  "'json' under search.formats in settings.yml") from e
            raise

    def search(self, query, max_results=DEFAULT_MAX_RESULTS):
        from urllib.parse import urlencode
        base = _env("SEARXNG_URL")
        if not base:
            raise SearchError("searxng search provider is not configured (SEARXNG_URL unset)")
        params = {"q": query, "format": "json", "safesearch": "1"}
        for key, env in (("engines", "SEARXNG_ENGINES"), ("language", "SEARXNG_LANGUAGE")):
            if _env(env):
                params[key] = _env(env)
        payload = self._fetch(f"{base.rstrip('/')}/search?{urlencode(params)}")
        items = payload.get("results") if isinstance(payload, dict) else None
        if not isinstance(items, list):
            raise SearchError("searxng response has no 'results' list")
        results, seen = [], set()
        for item in items:
            if not isinstance(item, dict):
                continue
            url = (item.get("url") or "").strip()
            snippet = _clean_text(item.get("content"))
            if not url.startswith(("http://", "https://")) or not snippet or url in seen:
                continue
            seen.add(url)
            results.append({"title": _clean_text(item.get("title")), "url": url,
                            "snippet": snippet})
            if len(results) >= max_results:
                break
        return results


class BraveSearchProvider(SearchProvider):
    """Brave's Search API - a keyed, metered web index used as a fallback.

    Setting ``BRAVE_SEARCH_API_KEY`` is the opt-in: the key is used for
    nothing else, so unlike ``GEMINI_API_KEY`` it cannot have been set for a
    different reason. It still routes after every free provider, so it is
    only spent when the free route failed or came back too thin.
    """

    name = "brave"
    tier = TIER_METERED
    endpoint = "https://api.search.brave.com/res/v1/web/search"

    def configured(self):
        return bool(_env("BRAVE_SEARCH_API_KEY"))

    def _fetch(self, url, key):
        """The one network-touching step, isolated so tests replace it."""
        return _http_get_json(url, headers={"X-Subscription-Token": key}, label="brave")

    def search(self, query, max_results=DEFAULT_MAX_RESULTS):
        from urllib.parse import urlencode
        key = _env("BRAVE_SEARCH_API_KEY")
        if not key:
            raise SearchError("brave search provider is not configured (BRAVE_SEARCH_API_KEY unset)")
        payload = self._fetch(
            f"{self.endpoint}?{urlencode({'q': query, 'count': max(1, min(20, max_results))})}", key)
        web = payload.get("web") if isinstance(payload, dict) else None
        items = (web or {}).get("results") if isinstance(web, dict) else []
        results, seen = [], set()
        for item in items if isinstance(items, list) else []:
            if not isinstance(item, dict):
                continue
            url = (item.get("url") or "").strip()
            snippet = _clean_text(item.get("description"))
            if not url.startswith(("http://", "https://")) or not snippet or url in seen:
                continue
            seen.add(url)
            results.append({"title": _clean_text(item.get("title")), "url": url,
                            "snippet": snippet})
            if len(results) >= max_results:
                break
        return results


class WikipediaSearchProvider(SearchProvider):
    """Wikipedia's public search API: free and keyless, opt-in by name.

    Each result is the plain-text lead of one matching article, attributed
    to that article's URL - well suited to the dry factual subjects
    ``requires_subject_research`` concepts narrate, poor for competitor or
    format research. Because it needs no key it is always "configured", so
    it never joins the default route (that would make research silently
    available everywhere); name it in ``SEARCH_ORDER`` to use it.
    ``WIKIPEDIA_API_URL`` selects another language edition.
    """

    name = "wikipedia"
    tier = TIER_FREE
    default_endpoint = "https://en.wikipedia.org/w/api.php"

    def configured(self):
        return True

    def _fetch(self, url):
        """The one network-touching step, isolated so tests replace it."""
        return _http_get_json(url, label="wikipedia")

    def search(self, query, max_results=DEFAULT_MAX_RESULTS):
        from urllib.parse import urlencode
        params = {
            "action": "query", "format": "json", "formatversion": "2",
            "generator": "search", "gsrsearch": query,
            "gsrlimit": max(1, min(20, max_results)),
            "prop": "extracts|info", "inprop": "url", "exintro": "1",
            "explaintext": "1", "exsentences": "3", "exlimit": "max",
        }
        payload = self._fetch(
            f"{_env('WIKIPEDIA_API_URL', self.default_endpoint)}?{urlencode(params)}")
        if not isinstance(payload, dict):
            raise SearchError("wikipedia response is not a JSON object")
        if payload.get("error"):
            raise SearchError(f"wikipedia error: {payload['error'].get('info', 'unknown')}")
        pages = (payload.get("query") or {}).get("pages") or []
        results = []
        for page in sorted((p for p in pages if isinstance(p, dict)),
                           key=lambda p: p.get("index", 0)):
            url = (page.get("fullurl") or "").strip()
            snippet = _clean_text(page.get("extract"))
            if not url.startswith(("http://", "https://")) or not snippet:
                continue
            results.append({"title": (page.get("title") or "").strip(), "url": url,
                            "snippet": snippet})
            if len(results) >= max_results:
                break
        return results


# --------------------------------------------------------------------------
# routing - cheapest capable provider first, fall back cleanly
# --------------------------------------------------------------------------

class SearchRouter(SearchProvider):
    """Tries providers cheapest-first and stops as soon as research is adequate.

    Routing rules, all fail-closed:

    - Providers are tried in configured order, but stably re-sorted by cost
      tier (free, then metered search, then LLM-grounded search), so a paid
      provider is last however the order was written.
    - An unconfigured provider is skipped. One that raises ``SearchError``
      is recorded, put in cooldown for the rest of this router's life (or
      ``SEARCH_COOLDOWN`` seconds), and the next one is tried.
    - Results accumulate across providers, de-duplicated by URL, and the
      route stops once ``MIN_SOURCES`` adequate results exist - a later
      provider is only reached when the earlier ones failed or were thin.
    - If every attempted provider errored, the router raises
      ``SearchError``; otherwise it returns what it has, and the caller's
      adequacy check decides. It never invents anything to fill the gap.

    Every result carries ``provider`` - the name of the provider that
    actually returned it - so provenance survives routing and caching.
    ``last_attempts`` records what happened on the most recent search.
    """

    name = "routed"

    def __init__(self, providers, cooldown_seconds=None, clock=None):
        self.providers = sorted(providers, key=lambda p: getattr(p, "tier", TIER_FREE))
        if cooldown_seconds is None:
            try:
                cooldown_seconds = float(_env("SEARCH_COOLDOWN", DEFAULT_SEARCH_COOLDOWN))
            except ValueError:
                cooldown_seconds = DEFAULT_SEARCH_COOLDOWN
        self.cooldown_seconds = cooldown_seconds
        import time
        self._clock = clock or time.monotonic
        self._cooldown_until = {}
        self.last_attempts = []

    @property
    def tier(self):
        return min((getattr(p, "tier", TIER_FREE) for p in self.providers), default=TIER_FREE)

    def configured(self):
        return any(p.configured() for p in self.providers)

    def search(self, query, max_results=DEFAULT_MAX_RESULTS):
        attempts, results, seen_urls = [], [], set()
        tried = errored = 0
        for provider in self.providers:
            if not provider.configured():
                attempts.append({"provider": provider.name, "outcome": "skipped",
                                 "detail": "not configured"})
                continue
            if self._clock() < self._cooldown_until.get(provider.name, float("-inf")):
                attempts.append({"provider": provider.name, "outcome": "skipped",
                                 "detail": "cooling down after an earlier failure"})
                continue
            tried += 1
            try:
                found = provider.search(query, max_results=max_results) or []
            except SearchError as e:
                errored += 1
                self._cooldown_until[provider.name] = self._clock() + self.cooldown_seconds
                attempts.append({"provider": provider.name, "outcome": "error",
                                 "detail": str(e)[:300]})
                log.warning("search: %s failed (%s); trying the next provider",
                            provider.name, e)
                continue
            added = 0
            for r in found:
                url = r.get("url")
                if not url or url in seen_urls or len(results) >= max_results:
                    continue
                seen_urls.add(url)
                results.append({**r, "provider": r.get("provider") or provider.name})
                added += 1
            enough = sum(1 for r in results if _adequate(r)) >= MIN_SOURCES
            attempts.append({"provider": provider.name,
                             "outcome": "ok" if enough else "thin",
                             "detail": f"{len(found)} result(s), {added} new"})
            if enough:
                break
        self.last_attempts = attempts
        if tried == 0:
            raise SearchError("no configured search provider in the route ("
                              + ", ".join(p.name for p in self.providers) + ")")
        if errored == tried:
            raise SearchError("every search provider failed: " + "; ".join(
                f"{a['provider']}: {a['detail']}" for a in attempts if a["outcome"] == "error"))
        return results


def _adequate(result):
    return bool(result.get("url")) and \
        len((result.get("snippet") or "").strip()) >= MIN_SNIPPET_CHARS


def served_by(results, default):
    """The provider name(s) that actually produced ``results``, for provenance."""
    names = sorted({r.get("provider") for r in results or [] if r.get("provider")})
    return "+".join(names) or default


def build_search_providers():
    """Every search provider this build knows how to construct.

    Adding one is a new ``SearchProvider`` subclass registered here; no
    caller changes.
    """
    return {"fixture": FixtureSearchProvider(),
            "searxng": SearXNGSearchProvider(),
            "brave": BraveSearchProvider(),
            "wikipedia": WikipediaSearchProvider(),
            "gemini": GeminiSearchProvider(),
            "anthropic": AnthropicSearchProvider()}


def _route_names():
    """The route as configured: (names, explicit).

    ``SEARCH_ORDER`` (comma-separated) wins, then ``SEARCH_PROVIDER`` (one
    name, or a comma list, kept for existing configs). With neither, the
    default route is ``DEFAULT_SEARCH_ORDER`` - free and opt-in-by-key
    providers only; Wikipedia and the LLM-grounded providers are never in
    it and are reached only when named.
    """
    for env in ("SEARCH_ORDER", "SEARCH_PROVIDER"):
        raw = _env(env)
        if raw:
            return [n.strip() for n in raw.split(",") if n.strip()], True
    return list(DEFAULT_SEARCH_ORDER), False


def _select_provider(providers=None):
    """The provider (or router) research should use, or None to fail closed.

    ``TEST_MODE=1`` always selects the fixture. An explicit route of one
    known provider returns that provider itself; several return a
    ``SearchRouter``. The default route only counts providers that are
    actually configured, so a host with nothing set still selects nothing.
    """
    providers = providers if providers is not None else build_search_providers()
    if os.environ.get("TEST_MODE") == "1":
        return providers.get("fixture")
    names, explicit = _route_names()
    chosen = []
    for name in names:
        provider = providers.get(name)
        if provider is None:
            log.warning("unknown search provider in route: %s", name)
        elif provider not in chosen and (explicit or provider.configured()):
            chosen.append(provider)
    if not chosen:
        return None
    if len(chosen) == 1:
        return chosen[0]
    return SearchRouter(chosen)


def provider_status(providers=None):
    """Whether sourced research can run here, from configuration alone.

    ``configured`` names the route (what ``SEARCH_ORDER``/``SEARCH_PROVIDER``
    say, the configured part of the default route, or "fixture" under
    TEST_MODE); ``available`` is whether at least one provider on it reports
    itself configured; ``route`` lists each one with its cost tier. Read by
    the concept catalogue so the UI can say, before a run, that a
    research-required concept would fail closed - never a reason to fall
    back to model memory. No network probe.
    """
    providers = providers if providers is not None else build_search_providers()
    provider = _select_provider(providers)
    if os.environ.get("TEST_MODE") == "1":
        configured = "fixture"
    else:
        names, explicit = _route_names()
        if explicit:
            configured = ",".join(names)
        else:
            configured = ",".join(n for n in names if providers[n].configured()) or None
    route = provider.providers if isinstance(provider, SearchRouter) else \
        ([provider] if provider is not None else [])
    return {
        "configured": configured,
        "available": bool(provider is not None and provider.configured()),
        "known": sorted(providers),
        "route": [{"name": p.name, "configured": p.configured(),
                   "tier": TIER_NAMES.get(getattr(p, "tier", TIER_FREE), "free")}
                  for p in route],
    }


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
            "no search provider selected. Set SEARXNG_URL (free, self-hosted), "
            "BRAVE_SEARCH_API_KEY, or SEARCH_ORDER/SEARCH_PROVIDER, or "
            "TEST_MODE=1 for the fixture stand-in. "
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
        tried = ""
        if isinstance(provider, SearchRouter) and provider.last_attempts:
            tried = " (route: " + ", ".join(
                f"{a['provider']} {a['outcome']}" for a in provider.last_attempts) + ")"
        raise SubjectResearchError(
            f"only {len(adequate)} adequately-sourced result(s) for {query!r} "
            f"(need >= {MIN_SOURCES}){tried}; refusing to research this subject from "
            "thin or absent sources rather than fall back to model memory")

    facts = [
        {
            "statement": r["snippet"].strip(),
            "source_url": r["url"],
            "source_title": (r.get("title") or "").strip(),
            "provider": r.get("provider") or provider.name,
        }
        for r in adequate
    ]
    artifact = {
        "subject_research_version": SUBJECT_RESEARCH_VERSION,
        "video_id": video_id,
        "concept_id": concept.get("id"),
        "query": query,
        # The provider(s) that actually returned the facts, not the route.
        "provider": served_by(adequate, provider.name),
        "researched_utc": utc_now(),
        # Sourced facts only - no creative interpretation. creative.py turns
        # these into narration/prompts; this module never does that itself.
        "facts": facts,
        "sources": [{"url": f["source_url"], "title": f["source_title"]} for f in facts],
    }
    if isinstance(provider, SearchRouter):
        artifact["routing"] = provider.last_attempts
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
