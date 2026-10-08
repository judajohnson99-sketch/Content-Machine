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

    def tearDown(self):
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
            os_env.pop("SEARCH_PROVIDER", None); os_env.pop("TEST_MODE", None)
            status = subject_research.provider_status()
        self.assertIn("gemini", status["known"])
        self.assertFalse(status["available"])
        with unittest.mock.patch.dict("os.environ", {"GEMINI_API_KEY": "k", "SEARCH_PROVIDER": "gemini"}):
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

    def test_no_provider_configured_in_production_by_default(self):
        """No SEARCH_PROVIDER set and not TEST_MODE: nothing is selected -
        the production-unconfigured, fail-closed default."""
        import os
        self.assertIsNone(os.environ.get("TEST_MODE"))
        self.assertIsNone(subject_research._select_provider())


_ROUTING_ENV = ("TEST_MODE", "SEARCH_ORDER", "SEARCH_PROVIDER", "SEARXNG_URL",
                "SEARXNG_ENGINES", "SEARXNG_LANGUAGE", "BRAVE_SEARCH_API_KEY",
                "GEMINI_API_KEY", "ANTHROPIC_API_KEY", "WIKIPEDIA_API_URL",
                "SEARCH_COOLDOWN")


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

    def test_nothing_set_selects_nothing(self):
        self.assertIsNone(self.select())

    def test_keys_for_llm_providers_never_join_the_default_route(self):
        self.assertIsNone(self.select(GEMINI_API_KEY="k", ANTHROPIC_API_KEY="k"))

    def test_searxng_url_alone_makes_searxng_the_primary(self):
        selected = self.select(SEARXNG_URL="http://searx.internal:8080")
        self.assertIsInstance(selected, subject_research.SearXNGSearchProvider)

    def test_searxng_and_brave_route_free_first(self):
        selected = self.select(SEARXNG_URL="http://searx.internal:8080",
                               BRAVE_SEARCH_API_KEY="b")
        self.assertIsInstance(selected, subject_research.SearchRouter)
        self.assertEqual([p.name for p in selected.providers], ["searxng", "brave"])

    def test_explicit_order_wins_and_paid_is_reordered_last(self):
        selected = self.select(SEARCH_ORDER="gemini, searxng, unknown-vendor, wikipedia",
                               SEARCH_PROVIDER="anthropic", SEARXNG_URL="http://s",
                               GEMINI_API_KEY="k")
        self.assertEqual([p.name for p in selected.providers],
                         ["searxng", "wikipedia", "gemini"])

    def test_legacy_single_search_provider_still_selects_that_provider(self):
        selected = self.select(SEARCH_PROVIDER="anthropic", ANTHROPIC_API_KEY="k")
        self.assertIsInstance(selected, subject_research.AnthropicSearchProvider)

    def test_status_reports_the_route_with_tiers(self):
        self.env = routing_env(SEARXNG_URL="http://s", SEARCH_ORDER="searxng,brave,gemini")
        status = subject_research.provider_status()
        self.assertTrue(status["available"])
        self.assertEqual(status["configured"], "searxng,brave,gemini")
        self.assertEqual(status["route"], [
            {"name": "searxng", "configured": True, "tier": "free"},
            {"name": "brave", "configured": False, "tier": "metered"},
            {"name": "gemini", "configured": False, "tier": "paid"}])
        self.env.stop()
        self.env = routing_env(SEARXNG_URL="http://s")
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
