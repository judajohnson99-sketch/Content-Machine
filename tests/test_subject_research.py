#!/usr/bin/env python3
"""Tests for source-backed subject research: the search-provider abstraction,
the fail-closed adequacy checks, and the once-per-video cache.

No test touches a network - the fixture provider is the local stand-in this
project's tests always use in place of a real vendor.
"""
import json
import shutil
import sys
import tempfile
import unittest
import unittest.mock
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import subject_research  # noqa: E402


def concept(**overrides):
    base = {
        "id": "test-history-concept",
        "working_title_pattern": "The History of the Suez Canal | Boring Story for Sleep | 60 Minutes",
        "content_format": "Deliberately monotone narration of dry factual material, 60 min",
    }
    base.update(overrides)
    return base


class FakeSearchProvider(subject_research.SearchProvider):
    """Scriptable provider - mirrors FakeProvider in test_generation.py."""

    name = "fake"

    def __init__(self, results=None, configured=True, error=None):
        self._results = results if results is not None else []
        self._configured = configured
        self._error = error

    def configured(self):
        return self._configured

    def search(self, query, max_results=5):
        if self._error:
            raise self._error
        return self._results[:max_results]


class TopicQueryTest(unittest.TestCase):

    def test_takes_the_part_before_the_first_pipe(self):
        self.assertEqual(
            subject_research.topic_query(concept()), "The History of the Suez Canal")

    def test_falls_back_to_content_format_with_no_title_pattern(self):
        c = concept(working_title_pattern="")
        self.assertEqual(subject_research.topic_query(c), c["content_format"])

    def test_none_when_nothing_to_derive_a_topic_from(self):
        self.assertIsNone(subject_research.topic_query(
            concept(working_title_pattern="", content_format="")))


class ResearchSubjectTest(unittest.TestCase):

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self._prev_dir = subject_research.SUBJECTS_DIR
        subject_research.SUBJECTS_DIR = self.tmp
        # provider=None must select nothing here, not the live keyless route.
        self.env = routing_env(SEARCH_KEYLESS="0")

    def tearDown(self):
        self.env.stop()
        subject_research.SUBJECTS_DIR = self._prev_dir
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _adequate_results(self):
        return [
            {"title": "A", "url": "https://example.test/a",
             "snippet": "A long enough sentence describing the canal's history in some detail."},
            {"title": "B", "url": "https://example.test/b",
             "snippet": "Another adequately long sentence from a second, independent source."},
        ]

    def test_no_provider_selected_fails_closed(self):
        with self.assertRaises(subject_research.SubjectResearchError):
            subject_research.research_subject("vid-1", concept(), provider=None)

    def test_unconfigured_provider_fails_closed(self):
        provider = FakeSearchProvider(configured=False)
        with self.assertRaises(subject_research.SubjectResearchError):
            subject_research.research_subject("vid-1", concept(), provider=provider)

    def test_too_few_adequate_results_fails_closed_rather_than_publish_thin_facts(self):
        provider = FakeSearchProvider(results=[
            {"title": "A", "url": "https://example.test/a", "snippet": "too short"},
        ])
        with self.assertRaises(subject_research.SubjectResearchError):
            subject_research.research_subject("vid-1", concept(), provider=provider)

    def test_search_failure_fails_closed_not_silently(self):
        provider = FakeSearchProvider(error=subject_research.SearchError("vendor down"))
        with self.assertRaises(subject_research.SubjectResearchError):
            subject_research.research_subject("vid-1", concept(), provider=provider)

    def test_adequate_results_produce_sourced_facts_separate_from_interpretation(self):
        provider = FakeSearchProvider(results=self._adequate_results())
        artifact = subject_research.research_subject("vid-1", concept(), provider=provider)
        self.assertEqual(len(artifact["facts"]), 2)
        for fact in artifact["facts"]:
            self.assertIn("statement", fact)
            self.assertIn("source_url", fact)
        self.assertEqual(artifact["provider"], "fake")
        self.assertEqual(artifact["query"], "The History of the Suez Canal")

    def test_result_is_cached_and_a_second_call_does_not_search_again(self):
        provider = FakeSearchProvider(results=self._adequate_results())
        subject_research.research_subject("vid-1", concept(), provider=provider)

        exploding_provider = FakeSearchProvider(
            error=subject_research.SearchError("must not be called"))
        artifact = subject_research.research_subject(
            "vid-1", concept(), provider=exploding_provider)
        self.assertEqual(len(artifact["facts"]), 2)

    def test_force_re_researches_even_with_a_cache_present(self):
        provider = FakeSearchProvider(results=self._adequate_results())
        subject_research.research_subject("vid-1", concept(), provider=provider)

        provider2 = FakeSearchProvider(results=[
            {"title": "C", "url": "https://example.test/c",
             "snippet": "A third, differently-worded adequately long source sentence."},
            {"title": "D", "url": "https://example.test/d",
             "snippet": "A fourth, differently-worded adequately long source sentence."},
        ])
        artifact = subject_research.research_subject(
            "vid-1", concept(), provider=provider2, force=True)
        self.assertEqual(artifact["facts"][0]["source_url"], "https://example.test/c")

    def test_load_returns_none_when_nothing_cached(self):
        self.assertIsNone(subject_research.load_subject_research("no-such-video"))


class GeminiSearchProviderTest(unittest.TestCase):
    """Parsing of grounding metadata only - the network step is replaced."""

    PAYLOAD = json.dumps({
        "chunks": [
            {"uri": "https://example.test/a", "title": "example.test"},
            {"uri": "https://example.test/b", "title": "example.test"},
            {"uri": None, "title": "no-uri"},
        ],
        "supports": [
            {"text": "Sleep ambience videos commonly run for eight to ten hours.", "chunks": [0]},
            {"text": "Many channels favour a single slow-moving still image.", "chunks": [2, 1]},
            {"text": "", "chunks": [0]},
            {"text": "Dangling reference.", "chunks": [7]},
            {"text": "Sleep ambience videos commonly run for eight to ten hours.", "chunks": [0]},
        ],
    })

    def _provider(self, payload=None):
        provider = subject_research.GeminiSearchProvider()
        provider._run = lambda query: self.PAYLOAD if payload is None else payload
        return provider

    def test_each_result_is_one_sentence_attributed_to_one_url(self):
        with unittest.mock.patch.dict("os.environ", {"GEMINI_API_KEY": "k"}):
            results = self._provider().search("sleep ambience formats")
        self.assertEqual([r["url"] for r in results],
                         ["https://example.test/a", "https://example.test/b"])
        self.assertTrue(all(r["snippet"] and r["title"] for r in results))
        self.assertEqual(results[1]["snippet"],
                         "Many channels favour a single slow-moving still image.")

    def test_max_results_and_empty_grounding(self):
        with unittest.mock.patch.dict("os.environ", {"GEMINI_API_KEY": "k"}):
            self.assertEqual(len(self._provider().search("q", max_results=1)), 1)
            self.assertEqual(self._provider('{"chunks": [], "supports": []}').search("q"), [])

    def test_unconfigured_or_malformed_is_a_search_error_not_results(self):
        with unittest.mock.patch.dict("os.environ", {"GEMINI_API_KEY": ""}):
            with self.assertRaises(subject_research.SearchError):
                self._provider().search("q")
        with unittest.mock.patch.dict("os.environ", {"GEMINI_API_KEY": "k"}):
            with self.assertRaises(subject_research.SearchError):
                self._provider("not json").search("q")

    def test_gemini_is_known_but_never_selected_by_default(self):
        with unittest.mock.patch.dict("os.environ", {"GEMINI_API_KEY": "k"}, clear=False):
            os_env = __import__("os").environ
            for name in _ROUTING_ENV:
                os_env.pop(name, None)
            os_env["GEMINI_API_KEY"] = "k"
            status = subject_research.provider_status()
        self.assertIn("gemini", status["known"])
        self.assertNotIn("gemini", [p["name"] for p in status["route"]])
        with unittest.mock.patch.dict("os.environ", {"GEMINI_API_KEY": "k", "SEARCH_PROVIDER": "gemini",
                                                     "SEARCH_KEYLESS": "0"}):
            selected = subject_research._select_provider()
            self.assertIsInstance(selected, subject_research.GeminiSearchProvider)
            self.assertTrue(selected.configured())


class FixtureSearchProviderTest(unittest.TestCase):
    """The built-in local stand-in TEST_MODE selects automatically."""

    def test_configured_and_returns_adequate_canned_results(self):
        provider = subject_research.FixtureSearchProvider()
        self.assertTrue(provider.configured())
        results = provider.search("some topic")
        self.assertGreaterEqual(len(results), subject_research.MIN_SOURCES)
        for r in results:
            self.assertTrue(r["url"])
            self.assertGreaterEqual(len(r["snippet"]), subject_research.MIN_SNIPPET_CHARS)

    def test_test_mode_selects_the_fixture_provider(self):
        import os
        prev = os.environ.get("TEST_MODE")
        os.environ["TEST_MODE"] = "1"
        try:
            provider = subject_research._select_provider()
        finally:
            if prev is None:
                os.environ.pop("TEST_MODE", None)
            else:
                os.environ["TEST_MODE"] = prev
        self.assertEqual(provider.name, "fixture")

    def test_no_keys_in_production_selects_only_the_keyless_fallbacks(self):
        """Nothing configured and not TEST_MODE: the keyless public route,
        never a paid provider - and nothing at all once SEARCH_KEYLESS=0."""
        env = routing_env()
        try:
            selected = subject_research._select_provider()
            self.assertEqual([p.name for p in selected.providers], ["duckduckgo", "wikipedia"])
            import os
            os.environ["SEARCH_KEYLESS"] = "0"
            self.assertIsNone(subject_research._select_provider())
        finally:
            env.stop()


_ROUTING_ENV = ("TEST_MODE", "SEARCH_ORDER", "SEARCH_PROVIDER", "SEARXNG_URL",
                "SEARXNG_ENGINES", "SEARXNG_LANGUAGE", "BRAVE_SEARCH_API_KEY",
                "GEMINI_API_KEY", "ANTHROPIC_API_KEY", "WIKIPEDIA_API_URL",
                "SEARCH_COOLDOWN", "SEARCH_KEYLESS", "DUCKDUCKGO_HTML_URL",
                "YOUTUBE_API_KEY")


def routing_env(**values):
    """Patch os.environ with every routing variable cleared, then ``values`` set."""
    import os
    patcher = unittest.mock.patch.dict(os.environ, {})
    patcher.start()
    for name in _ROUTING_ENV:
        os.environ.pop(name, None)
    os.environ.update(values)
    return patcher


def adequate(prefix, n=2):
    return [{"title": f"{prefix} {i}", "url": f"https://{prefix}.test/{i}",
             "snippet": f"An adequately long sourced sentence number {i} from {prefix}."}
            for i in range(n)]


class TieredFake(FakeSearchProvider):
    def __init__(self, name, tier=subject_research.TIER_FREE, **kwargs):
        super().__init__(**kwargs)
        self.name, self.tier, self.calls = name, tier, 0

    def search(self, query, max_results=5):
        self.calls += 1
        return super().search(query, max_results)


class SearXNGSearchProviderTest(unittest.TestCase):
    """Served by a real local HTTP stand-in, so the urllib path is exercised."""

    def setUp(self):
        import http.server
        import threading
        test = self
        self.status, self.body, self.requests = 200, b"{}", []

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                test.requests.append(self.path)
                self.send_response(test.status)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(test.body)

            def log_message(self, *args):
                pass

        self.server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.env = routing_env(
            SEARXNG_URL=f"http://127.0.0.1:{self.server.server_port}/",
            SEARXNG_ENGINES="wikipedia,duckduckgo",
            NO_PROXY="127.0.0.1,localhost", no_proxy="127.0.0.1,localhost")

    def tearDown(self):
        self.env.stop()
        self.server.shutdown()
        self.server.server_close()

    def test_parses_results_and_passes_settings_through(self):
        self.body = json.dumps({"results": [
            {"title": "Suez <b>Canal</b>", "url": "https://example.test/a",
             "content": "The canal opened in  1869 &amp; links the seas."},
            {"title": "dup", "url": "https://example.test/a", "content": "duplicate url"},
            {"title": "no content", "url": "https://example.test/b", "content": ""},
            {"title": "bad url", "url": "javascript:alert(1)", "content": "nope"},
            {"title": "C", "url": "https://example.test/c", "content": "Third source."},
        ]}).encode()
        results = subject_research.SearXNGSearchProvider().search("Suez Canal")
        self.assertEqual([r["url"] for r in results],
                         ["https://example.test/a", "https://example.test/c"])
        self.assertEqual(results[0]["title"], "Suez Canal")
        self.assertEqual(results[0]["snippet"], "The canal opened in 1869 & links the seas.")
        path = self.requests[0]
        self.assertTrue(path.startswith("/search?"))
        self.assertIn("format=json", path)
        self.assertIn("engines=wikipedia%2Cduckduckgo", path)

    def test_json_format_disabled_is_reported_not_treated_as_empty(self):
        self.status = 403
        with self.assertRaises(subject_research.SearchError) as ctx:
            subject_research.SearXNGSearchProvider().search("q")
        self.assertIn("search.formats", str(ctx.exception))

    def test_malformed_or_unreachable_is_a_search_error(self):
        self.body = b"<html>not json</html>"
        with self.assertRaises(subject_research.SearchError):
            subject_research.SearXNGSearchProvider().search("q")
        self.body = b'{"no_results_key": true}'
        with self.assertRaises(subject_research.SearchError):
            subject_research.SearXNGSearchProvider().search("q")
        import os
        os.environ["SEARXNG_URL"] = "http://127.0.0.1:9"   # discard port: nothing listens
        with self.assertRaises(subject_research.SearchError):
            subject_research.SearXNGSearchProvider().search("q")

    def test_unset_url_means_unconfigured(self):
        import os
        os.environ.pop("SEARXNG_URL")
        provider = subject_research.SearXNGSearchProvider()
        self.assertFalse(provider.configured())
        with self.assertRaises(subject_research.SearchError):
            provider.search("q")


class BraveAndWikipediaProviderTest(unittest.TestCase):
    """Response parsing only - the network step is replaced."""

    def setUp(self):
        self.env = routing_env(BRAVE_SEARCH_API_KEY="brave-key")

    def tearDown(self):
        self.env.stop()

    def test_brave_strips_markup_and_sends_the_key(self):
        provider = subject_research.BraveSearchProvider()
        seen = {}

        def fetch(url, key):
            seen.update(url=url, key=key)
            return {"web": {"results": [
                {"title": "A", "url": "https://example.test/a",
                 "description": "The <strong>Suez Canal</strong> is an artificial waterway."},
                {"title": "B", "url": "https://example.test/a", "description": "dup"},
            ]}}
        provider._fetch = fetch
        results = provider.search("Suez Canal", max_results=3)
        self.assertEqual(seen["key"], "brave-key")
        self.assertIn("count=3", seen["url"])
        self.assertEqual(results, [{"title": "A", "url": "https://example.test/a",
                                    "snippet": "The Suez Canal is an artificial waterway."}])
        provider._fetch = lambda url, key: {"type": "search"}
        self.assertEqual(provider.search("q"), [])

    def test_brave_without_key_is_unconfigured(self):
        import os
        os.environ.pop("BRAVE_SEARCH_API_KEY")
        self.assertFalse(subject_research.BraveSearchProvider().configured())

    def test_wikipedia_returns_article_leads_in_search_rank_order(self):
        provider = subject_research.WikipediaSearchProvider()
        provider._fetch = lambda url: {"query": {"pages": [
            {"title": "Second", "index": 2, "fullurl": "https://en.wikipedia.org/wiki/Second",
             "extract": "Second article lead."},
            {"title": "Suez Canal", "index": 1, "fullurl": "https://en.wikipedia.org/wiki/Suez_Canal",
             "extract": "The Suez Canal is a sea-level waterway in Egypt."},
            {"title": "Empty", "index": 3, "fullurl": "https://en.wikipedia.org/wiki/Empty",
             "extract": ""},
        ]}}
        results = provider.search("Suez Canal")
        self.assertEqual([r["title"] for r in results], ["Suez Canal", "Second"])
        provider._fetch = lambda url: {"error": {"info": "bad request"}}
        with self.assertRaises(subject_research.SearchError):
            provider.search("q")


DDG_PAGE = """<html><body>
<div class="result results_links results_links_deep result--ad">
  <a rel="nofollow" class="result__a" href="https://duckduckgo.com/y.js?ad_domain=x">Sponsored</a>
  <a class="result__snippet" href="#">An advert that must never be treated as a source at all.</a>
</div>
<div class="result results_links results_links_deep web-result">
  <h2 class="result__title"><a rel="nofollow" class="result__a"
     href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.test%2Fsleep%3Fa%3D1&amp;rut=abc">Sleep <b>Sounds</b> Guide</a></h2>
  <a class="result__snippet" href="#">Most <b>sleep</b> videos run eight hours &amp; use rain or brown noise.</a>
</div>
<div class="result results_links results_links_deep web-result">
  <h2 class="result__title"><a rel="nofollow" class="result__a" href="https://example.test/direct">Direct</a></h2>
  <a class="result__snippet" href="#">A second organic result linking directly to its page.</a>
</div>
<div class="result results_links web-result">
  <h2 class="result__title"><a rel="nofollow" class="result__a" href="https://example.test/nosnippet">No snippet</a></h2>
</div>
</body></html>"""


class DuckDuckGoProviderTest(unittest.TestCase):
    """HTML parsing and failure reporting - the network step is replaced."""

    def setUp(self):
        self.env = routing_env()

    def tearDown(self):
        self.env.stop()

    def test_parses_organic_results_unwraps_redirects_and_drops_ads(self):
        provider = subject_research.DuckDuckGoSearchProvider()
        seen = {}

        def fetch(url, data):
            seen.update(url=url, data=data)
            return 200, DDG_PAGE
        provider._fetch = fetch
        results = provider.search("sleep sounds", max_results=5)
        self.assertEqual(seen["url"], "https://html.duckduckgo.com/html/")
        self.assertIn(b"q=sleep+sounds", seen["data"])
        self.assertEqual(results, [
            {"title": "Sleep Sounds Guide", "url": "https://example.test/sleep?a=1",
             "snippet": "Most sleep videos run eight hours & use rain or brown noise."},
            {"title": "Direct", "url": "https://example.test/direct",
             "snippet": "A second organic result linking directly to its page."},
        ])
        self.assertEqual(len(provider.search("q", max_results=1)), 1)

    def test_bot_challenge_or_unknown_markup_is_a_search_error_not_no_results(self):
        provider = subject_research.DuckDuckGoSearchProvider()
        provider._fetch = lambda url, data: (202, "<div class='anomaly-modal'>")
        with self.assertRaises(subject_research.SearchError) as ctx:
            provider.search("q")
        self.assertIn("bot challenge", str(ctx.exception))
        provider._fetch = lambda url, data: (200, "<html>new layout</html>")
        with self.assertRaises(subject_research.SearchError):
            provider.search("q")
        provider._fetch = lambda url, data: (200, "<div class='no-results'>No results.</div>")
        self.assertEqual(provider.search("q"), [])

    def test_is_keyless_and_tiered_after_metered_before_paid(self):
        provider = subject_research.DuckDuckGoSearchProvider()
        self.assertTrue(provider.configured())
        self.assertGreater(provider.tier, subject_research.TIER_METERED)
        self.assertLess(provider.tier, subject_research.TIER_PAID)

    def test_transport_failure_is_a_search_error_naming_the_source(self):
        import urllib.error
        with unittest.mock.patch("urllib.request.urlopen",
                                 side_effect=urllib.error.URLError("Tunnel connection failed: 403")):
            with self.assertRaises(subject_research.SearchError) as ctx:
                subject_research.DuckDuckGoSearchProvider().search("q")
        self.assertIn("duckduckgo unreachable", str(ctx.exception))


class KeylessFallThroughTest(unittest.TestCase):
    """The real provider classes on the real default route, every network
    step replaced: each failure is recorded and the next source answers."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self._prev_dir = subject_research.SUBJECTS_DIR
        subject_research.SUBJECTS_DIR = self.tmp

    def tearDown(self):
        self.env.stop()
        subject_research.SUBJECTS_DIR = self._prev_dir
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_searxng_down_brave_quota_ddg_challenge_then_wikipedia_answers(self):
        self.env = routing_env(SEARXNG_URL="http://searx.invalid", BRAVE_SEARCH_API_KEY="b")
        providers = subject_research.build_search_providers()

        def searx_down(url):
            raise subject_research.SearchError("searxng unreachable: refused")

        def brave_quota(url, key):
            raise subject_research.SearchError("brave returned HTTP 429")
        providers["searxng"]._fetch = searx_down
        providers["brave"]._fetch = brave_quota
        providers["duckduckgo"]._fetch = lambda url, data: (202, "anomaly-modal")
        providers["wikipedia"]._fetch = lambda url: {"query": {"pages": [
            {"title": "Suez Canal", "index": 1, "fullurl": "https://en.wikipedia.org/wiki/Suez_Canal",
             "extract": "The Suez Canal is an artificial sea-level waterway in Egypt."},
            {"title": "Isthmus of Suez", "index": 2,
             "fullurl": "https://en.wikipedia.org/wiki/Isthmus_of_Suez",
             "extract": "The Isthmus of Suez is the land bridge between Africa and Asia."}]}}
        gemini = providers["gemini"]
        gemini._run = lambda q: (_ for _ in ()).throw(AssertionError("paid provider reached"))

        router = subject_research._select_provider(providers)
        artifact = subject_research.research_subject("vid-k", concept(), provider=router)
        self.assertEqual(artifact["provider"], "wikipedia")
        self.assertEqual([(a["provider"], a["outcome"]) for a in artifact["routing"]],
                         [("searxng", "error"), ("brave", "error"),
                          ("duckduckgo", "error"), ("wikipedia", "ok")])
        self.assertIn("HTTP 429", artifact["routing"][1]["detail"])


class SearchRouterTest(unittest.TestCase):

    def test_stops_at_the_primary_when_it_is_adequate(self):
        primary = TieredFake("primary", results=adequate("p"))
        backup = TieredFake("backup", tier=subject_research.TIER_METERED, results=adequate("b"))
        router = subject_research.SearchRouter([primary, backup])
        results = router.search("q")
        self.assertEqual(backup.calls, 0)
        self.assertEqual({r["provider"] for r in results}, {"primary"})
        self.assertEqual([a["outcome"] for a in router.last_attempts], ["ok"])

    def test_falls_back_when_the_primary_errors_and_cools_it_down(self):
        primary = TieredFake("primary", error=subject_research.SearchError("instance down"))
        backup = TieredFake("backup", tier=subject_research.TIER_METERED, results=adequate("b"))
        now = [0.0]
        router = subject_research.SearchRouter([primary, backup], cooldown_seconds=60,
                                               clock=lambda: now[0])
        self.assertEqual({r["provider"] for r in router.search("q")}, {"backup"})
        self.assertEqual([a["outcome"] for a in router.last_attempts], ["error", "ok"])
        router.search("q2")
        self.assertEqual(primary.calls, 1)   # skipped while cooling down
        self.assertEqual(router.last_attempts[0]["outcome"], "skipped")
        now[0] = 61.0
        router.search("q3")
        self.assertEqual(primary.calls, 2)   # retried once the cooldown lapses

    def test_thin_primary_is_topped_up_by_the_next_provider_deduplicated(self):
        primary = TieredFake("primary", results=adequate("p", n=1))
        backup = TieredFake("backup", results=adequate("p", n=1) + adequate("b", n=1))
        router = subject_research.SearchRouter([primary, backup])
        results = router.search("q")
        self.assertEqual([r["url"] for r in results],
                         ["https://p.test/0", "https://b.test/0"])
        self.assertEqual([r["provider"] for r in results], ["primary", "backup"])
        self.assertEqual([a["outcome"] for a in router.last_attempts], ["thin", "ok"])

    def test_paid_providers_are_always_last_whatever_the_order(self):
        paid = TieredFake("paid", tier=subject_research.TIER_PAID, results=adequate("x"))
        metered = TieredFake("metered", tier=subject_research.TIER_METERED, results=adequate("m"))
        free = TieredFake("free", results=adequate("f"))
        router = subject_research.SearchRouter([paid, metered, free])
        self.assertEqual([p.name for p in router.providers], ["free", "metered", "paid"])
        router.search("q")
        self.assertEqual((free.calls, metered.calls, paid.calls), (1, 0, 0))

    def test_unconfigured_providers_are_skipped_not_failed(self):
        off = TieredFake("off", configured=False, results=adequate("o"))
        on = TieredFake("on", results=adequate("n"))
        router = subject_research.SearchRouter([off, on])
        self.assertTrue(router.configured())
        router.search("q")
        self.assertEqual(off.calls, 0)
        self.assertEqual(router.last_attempts[0], {"provider": "off", "outcome": "skipped",
                                                   "detail": "not configured"})

    def test_every_provider_failing_or_none_configured_is_a_search_error(self):
        router = subject_research.SearchRouter([
            TieredFake("a", error=subject_research.SearchError("down")),
            TieredFake("b", error=subject_research.SearchError("quota"))])
        with self.assertRaises(subject_research.SearchError) as ctx:
            router.search("q")
        self.assertIn("a: down", str(ctx.exception))
        self.assertIn("b: quota", str(ctx.exception))
        empty = subject_research.SearchRouter([TieredFake("a", configured=False)])
        self.assertFalse(empty.configured())
        with self.assertRaises(subject_research.SearchError):
            empty.search("q")

    def test_one_error_and_one_thin_returns_the_thin_set_for_the_caller_to_judge(self):
        router = subject_research.SearchRouter([
            TieredFake("a", error=subject_research.SearchError("down")),
            TieredFake("b", results=adequate("b", n=1))])
        self.assertEqual(len(router.search("q")), 1)


class RouteSelectionTest(unittest.TestCase):

    def tearDown(self):
        self.env.stop()

    def select(self, **env):
        self.env = routing_env(**env)
        return subject_research._select_provider()

    def test_nothing_set_selects_the_keyless_route(self):
        self.assertEqual([p.name for p in self.select().providers],
                         ["duckduckgo", "wikipedia"])

    def test_nothing_set_and_keyless_disabled_selects_nothing(self):
        self.assertIsNone(self.select(SEARCH_KEYLESS="0"))

    def test_keys_for_llm_providers_never_join_the_default_route(self):
        selected = self.select(GEMINI_API_KEY="k", ANTHROPIC_API_KEY="k")
        self.assertEqual([p.name for p in selected.providers], ["duckduckgo", "wikipedia"])
        self.env.stop()
        self.assertIsNone(self.select(GEMINI_API_KEY="k", ANTHROPIC_API_KEY="k",
                                      SEARCH_KEYLESS="0"))

    def test_searxng_url_alone_makes_searxng_the_primary(self):
        selected = self.select(SEARXNG_URL="http://searx.internal:8080")
        self.assertEqual(selected.providers[0].name, "searxng")
        self.env.stop()
        selected = self.select(SEARXNG_URL="http://searx.internal:8080", SEARCH_KEYLESS="0")
        self.assertIsInstance(selected, subject_research.SearXNGSearchProvider)

    def test_route_order_is_searxng_brave_keyless(self):
        selected = self.select(SEARXNG_URL="http://searx.internal:8080",
                               BRAVE_SEARCH_API_KEY="b")
        self.assertIsInstance(selected, subject_research.SearchRouter)
        self.assertEqual([p.name for p in selected.providers],
                         ["searxng", "brave", "duckduckgo", "wikipedia"])

    def test_competitor_purpose_leaves_wikipedia_off_the_automatic_route(self):
        self.env = routing_env(BRAVE_SEARCH_API_KEY="b")
        selected = subject_research._select_provider(purpose="competitor")
        self.assertEqual([p.name for p in selected.providers], ["brave", "duckduckgo"])

    def test_explicit_order_wins_and_paid_is_reordered_last(self):
        selected = self.select(SEARCH_ORDER="gemini, searxng, unknown-vendor, wikipedia",
                               SEARCH_PROVIDER="anthropic", SEARXNG_URL="http://s",
                               GEMINI_API_KEY="k")
        self.assertEqual([p.name for p in selected.providers],
                         ["searxng", "wikipedia", "duckduckgo", "gemini"])

    def test_an_exhausted_paid_provider_falls_through_to_keyless(self):
        """The VPS case: SEARCH_PROVIDER=gemini with no quota left must not
        stop research - keyless sources are tried first, and gemini is only
        reached if they come back thin."""
        selected = self.select(SEARCH_PROVIDER="gemini", GEMINI_API_KEY="k")
        self.assertEqual([p.name for p in selected.providers],
                         ["duckduckgo", "wikipedia", "gemini"])

    def test_legacy_single_search_provider_still_selects_that_provider(self):
        selected = self.select(SEARCH_PROVIDER="anthropic", ANTHROPIC_API_KEY="k",
                               SEARCH_KEYLESS="0")
        self.assertIsInstance(selected, subject_research.AnthropicSearchProvider)

    def test_status_reports_the_route_with_tiers(self):
        self.env = routing_env(SEARXNG_URL="http://s", SEARCH_ORDER="searxng,brave,gemini")
        status = subject_research.provider_status()
        self.assertTrue(status["available"])
        self.assertEqual(status["configured"], "searxng,brave,duckduckgo,wikipedia,gemini")
        self.assertEqual(status["route"], [
            {"name": "searxng", "configured": True, "tier": "free"},
            {"name": "brave", "configured": False, "tier": "metered"},
            {"name": "duckduckgo", "configured": True, "tier": "keyless"},
            {"name": "wikipedia", "configured": True, "tier": "keyless"},
            {"name": "gemini", "configured": False, "tier": "paid"}])
        self.env.stop()
        self.env = routing_env(SEARXNG_URL="http://s", SEARCH_KEYLESS="0")
        self.assertEqual(subject_research.provider_status()["configured"], "searxng")


class RoutedResearchSubjectTest(unittest.TestCase):

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self._prev_dir = subject_research.SUBJECTS_DIR
        subject_research.SUBJECTS_DIR = self.tmp

    def tearDown(self):
        subject_research.SUBJECTS_DIR = self._prev_dir
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_fallback_is_recorded_as_the_provider_that_actually_served(self):
        router = subject_research.SearchRouter([
            TieredFake("searxng", error=subject_research.SearchError("instance down")),
            TieredFake("brave", tier=subject_research.TIER_METERED, results=adequate("b"))])
        artifact = subject_research.research_subject("vid-r", concept(), provider=router)
        self.assertEqual(artifact["provider"], "brave")
        self.assertEqual({f["provider"] for f in artifact["facts"]}, {"brave"})
        self.assertEqual([a["outcome"] for a in artifact["routing"]], ["error", "ok"])

    def test_a_route_that_stays_thin_still_fails_closed_naming_the_route(self):
        router = subject_research.SearchRouter([
            TieredFake("searxng", results=adequate("s", n=1)),
            TieredFake("brave", error=subject_research.SearchError("quota"))])
        with self.assertRaises(subject_research.SubjectResearchError) as ctx:
            subject_research.research_subject("vid-r", concept(), provider=router)
        self.assertIn("searxng thin", str(ctx.exception))
        self.assertIn("brave error", str(ctx.exception))
        self.assertIsNone(subject_research.load_subject_research("vid-r"))


if __name__ == "__main__":
    unittest.main()
