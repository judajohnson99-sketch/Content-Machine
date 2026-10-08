#!/usr/bin/env python3
"""Tests for the research brief and brief-driven findings: validation,
storage, sourced/interpreted provenance, and the fail-open/fail-closed split
between "no brief" (nothing to do) and "searched and came back thin"
(refused).

No test touches a network - FakeSearchProvider mirrors the stand-in
test_subject_research.py already uses.
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
import research  # noqa: E402
import subject_research  # noqa: E402


class FakeSearchProvider(subject_research.SearchProvider):
    name = "fake"

    def __init__(self, by_query=None, error=None):
        self._by_query = by_query or {}
        self._error = error

    def configured(self):
        return True

    def search(self, query, max_results=5):
        if self._error:
            raise self._error
        return self._by_query.get(query, [])[:max_results]


def brief(**overrides):
    base = {"niche": "sleep ambience", "creative_intent": "cozy rainy night"}
    base.update(overrides)
    return base


_NETWORK_ENV = ("TEST_MODE", "SEARCH_ORDER", "SEARCH_PROVIDER", "SEARXNG_URL",
                "BRAVE_SEARCH_API_KEY", "YOUTUBE_API_KEY", "SEARCH_KEYLESS")


def isolated_env(**values):
    """os.environ with every research-routing variable cleared, keyless
    routes off, then ``values`` applied. Returns the started patcher."""
    import os
    patcher = unittest.mock.patch.dict(os.environ, {})
    patcher.start()
    for name in _NETWORK_ENV:
        os.environ.pop(name, None)
    os.environ["SEARCH_KEYLESS"] = "0"
    os.environ.update(values)
    return patcher


class BriefValidationTest(unittest.TestCase):

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self._prev = research.BRIEFS_DIR
        research.BRIEFS_DIR = self.tmp

    def tearDown(self):
        research.BRIEFS_DIR = self._prev
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_niche_is_required(self):
        with self.assertRaises(research.ResearchError):
            research.save_brief("vid-1", {})

    def test_unknown_field_is_refused(self):
        with self.assertRaises(research.ResearchError):
            research.save_brief("vid-1", brief(script="not allowed here"))

    def test_seed_reference_needs_a_type_and_value(self):
        with self.assertRaises(research.ResearchError):
            research.save_brief("vid-1", brief(seed_references=[{"value": "x"}]))
        with self.assertRaises(research.ResearchError):
            research.save_brief("vid-1", brief(
                seed_references=[{"type": "podcast", "value": "x"}]))

    def test_valid_brief_round_trips(self):
        saved = research.save_brief("vid-1", brief(
            likes=["gentle pacing"], dislikes=["jump scares"],
            seed_references=[{"type": "channel", "value": "@example"}]))
        self.assertEqual(saved["niche"], "sleep ambience")
        self.assertEqual(research.load_brief("vid-1"), saved)

    def test_load_returns_none_when_nothing_saved(self):
        self.assertIsNone(research.load_brief("no-such-video"))


class ResearchFixture(unittest.TestCase):
    """Isolated findings/search-cache dirs and a provider that answers a
    brief's own queries. Split out so the focused suites below inherit the
    fixture without re-running ResearchProjectTest's cases."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self._prev = research.FINDINGS_DIR
        self._prev_cache = research.SEARCH_CACHE_DIR
        research.FINDINGS_DIR = self.tmp
        # The search cache is deliberately cross-project (research/cache/);
        # a test must never read or write the real one.
        research.SEARCH_CACHE_DIR = self.tmp / "cache"
        # No keyless route and no YouTube key: provider=None selects nothing
        # and the competitor listing has no source, so no test can reach a
        # real network. Competitor tests pass their own stand-in sources.
        self.env = isolated_env()

    def tearDown(self):
        self.env.stop()
        research.FINDINGS_DIR = self._prev
        research.SEARCH_CACHE_DIR = self._prev_cache
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _adequate_results(self, query):
        return [
            {"title": "A", "url": "https://example.test/a",
             "snippet": f"Adequately long observation about pacing for {query} content."},
            {"title": "B", "url": "https://example.test/b",
             "snippet": f"A second sourced observation about pacing and {query} audience reaction."},
        ]

    def _provider_for(self, b):
        """A stand-in that answers every query the brief asks for.

        `_search_queries` returns (topic, query) pairs - the topic a finding
        is filed under comes from the question asked, not from guessing at
        the answer - so the fake is keyed on the query half.
        """
        return FakeSearchProvider(by_query={
            q: self._adequate_results(q) for _topic, q in research._search_queries(b)
        })


class ResearchProjectTest(ResearchFixture):

    def test_no_provider_selected_fails_closed(self):
        with self.assertRaises(research.ResearchError):
            research.research_project("vid-1", brief(), provider=None)

    def test_unconfigured_provider_fails_closed(self):
        class Unconfigured(FakeSearchProvider):
            def configured(self):
                return False
        with self.assertRaises(research.ResearchError):
            research.research_project("vid-1", brief(), provider=Unconfigured())

    def test_brief_with_no_niche_or_seeds_has_nothing_to_search(self):
        with self.assertRaises(research.ResearchError):
            research.research_project("vid-1", {}, provider=FakeSearchProvider())

    def test_too_few_adequate_results_fails_closed(self):
        b = brief()
        first_query = research._search_queries(b)[0][1]
        provider = FakeSearchProvider(by_query={
            first_query: [
                {"title": "A", "url": "https://example.test/a", "snippet": "too short"},
            ],
        })
        with self.assertRaises(research.ResearchError):
            research.research_project("vid-1", b, provider=provider)

    def test_adequate_results_produce_sourced_observations(self):
        b = brief()
        provider = self._provider_for(b)
        artifact = research.research_project("vid-1", b, provider=provider)
        observations = [f for f in artifact["findings"] if f["kind"] == "observation"]
        self.assertGreaterEqual(len(observations), research.subject_research.MIN_SOURCES)
        for f in observations:
            self.assertEqual(f["confidence"], "VERIFIED")
            self.assertTrue(f["source_url"])
            self.assertIn(f["topic"], research.FINDING_TOPICS)

    def test_recurring_terms_produce_an_interpretation_never_a_hypothesis(self):
        b = brief()
        provider = self._provider_for(b)
        artifact = research.research_project("vid-1", b, provider=provider)
        kinds = {f["kind"] for f in artifact["findings"]}
        self.assertIn("interpretation", kinds)
        self.assertNotIn("hypothesis", kinds)
        interpretation = next(f for f in artifact["findings"] if f["kind"] == "interpretation")
        self.assertEqual(interpretation["confidence"], "INFERRED")
        self.assertIsNone(interpretation["source_url"])

    def test_result_is_cached_and_a_second_call_does_not_search_again(self):
        b = brief()
        provider = self._provider_for(b)
        research.research_project("vid-1", b, provider=provider)

        exploding = FakeSearchProvider(error=subject_research.SearchError("must not be called"))
        artifact = research.research_project("vid-1", b, provider=exploding)
        self.assertTrue(artifact["findings"])

    def test_force_re_researches_even_with_a_cache_present(self):
        b = brief()
        provider = self._provider_for(b)
        research.research_project("vid-1", b, provider=provider)
        artifact = research.research_project("vid-1", b, provider=provider, force=True)
        self.assertTrue(artifact["findings"])

    def test_load_returns_none_when_nothing_cached(self):
        self.assertIsNone(research.load_findings("no-such-video"))


class RequestedTopicsTest(unittest.TestCase):

    def test_no_topics_means_the_usual_sweep_not_nothing(self):
        self.assertEqual(research.requested_topics(brief()),
                         list(research.DEFAULT_RESEARCH_TOPICS))

    def test_an_explicit_list_is_honoured_exactly(self):
        b = brief(research_topics=["business", "titles"])
        self.assertEqual(research.requested_topics(b), ["business", "titles"])
        topics = [t for t, _q in research._search_queries(b)]
        self.assertEqual(topics, ["business", "titles", "concept"])

    def test_an_unknown_topic_is_refused_at_the_brief(self):
        problems = research.validate_brief(brief(research_topics=["astrology"]))
        self.assertTrue(any("astrology" in p for p in problems))


class TopicCoverageTest(ResearchFixture):
    """A topic is recorded as covered because the question was asked and
    answered - never because a keyword matched in the returned text."""

    def test_findings_are_filed_under_the_topic_that_was_asked(self):
        b = brief(research_topics=["business"])
        artifact = research.research_project("vid-1", b, provider=self._provider_for(b))
        topics = {f["topic"] for f in artifact["findings"] if f["kind"] == "observation"}
        self.assertEqual(topics, {"business", "concept"})

    def test_an_unanswered_topic_is_recorded_uncovered_not_fatal(self):
        b = brief(research_topics=["visuals", "thumbnails"])
        answers = dict(research._search_queries(b))
        provider = FakeSearchProvider(by_query={
            answers["visuals"]: self._adequate_results("visuals"),
            answers["concept"]: self._adequate_results("concept"),
        })
        artifact = research.research_project("vid-1", b, provider=provider)
        self.assertIn("visuals", artifact["topics_covered"])
        self.assertEqual(artifact["topics_uncovered"], ["thumbnails"])
        self.assertEqual(artifact["topics_requested"], ["visuals", "thumbnails"])


class SearchCacheTest(ResearchFixture):

    def test_an_identical_query_is_reused_across_projects(self):
        b = brief()
        provider = self._provider_for(b)
        research.research_project("vid-1", b, provider=provider)

        # A second project in the same niche asks the same questions; the
        # provider must not be paid for them again.
        exploding = FakeSearchProvider(error=subject_research.SearchError("must not search"))
        for query, results in provider._by_query.items():
            exploding._by_query[query] = results
        artifact = research.research_project("vid-2", b, provider=exploding)
        self.assertTrue(artifact["findings"])

    def test_force_bypasses_the_search_cache(self):
        provider = FakeSearchProvider(by_query={"q": [
            {"title": "A", "url": "https://example.test/a",
             "snippet": "A long enough snippet to count as an observation here."}]})
        research.cached_search(provider, "q", 5)
        results, from_cache = research.cached_search(provider, "q", 5)
        self.assertTrue(from_cache)
        self.assertTrue(results)
        _results, from_cache = research.cached_search(provider, "q", 5, force=True)
        self.assertFalse(from_cache)


class FakeYouTube(research.YouTubeProvider):
    """The real provider with only its single network step replaced."""

    def __init__(self, payloads):
        super().__init__(api_key="test-key")
        self.payloads = payloads
        self.calls = []

    def _get(self, endpoint, params):
        self.calls.append((endpoint, params))
        return self.payloads[endpoint]


def _yt_video(duration, tags):
    return {"contentDetails": {"duration": duration}, "snippet": {"tags": tags,
                                                                  "categoryId": "24"}}


class YouTubeProviderTest(unittest.TestCase):

    def test_unconfigured_says_so_and_makes_no_request(self):
        provider = research.YouTubeProvider(api_key="")
        self.assertFalse(provider.configured())
        with self.assertRaises(research.ResearchError):
            provider.observe("sleep ambience")

    def test_observations_carry_structure_never_titles(self):
        yt = FakeYouTube({
            "search": {"items": [{"id": {"videoId": "abcdefghijk"}}]},
            "videos": {"items": [_yt_video("PT3H1M", ["sleep", "rain"])]},
        })
        observed = yt.observe("sleep ambience", limit=5)
        self.assertEqual(len(observed), 1)
        self.assertEqual(observed[0]["duration_seconds"], 3 * 3600 + 60)
        self.assertEqual(observed[0]["recurring_patterns"], ["rain", "sleep"])
        self.assertNotIn("title", observed[0])
        self.assertIn("youtube-data-api", observed[0]["source"])

    def test_live_streams_without_a_parseable_duration_are_refused(self):
        yt = FakeYouTube({
            "search": {"items": [{"id": {"videoId": "abcdefghijk"}}]},
            "videos": {"items": [_yt_video("P0D", [])]},
        })
        with self.assertRaises(research.ResearchError):
            yt.observe("sleep ambience")

    def test_a_channel_seed_is_described_from_its_own_videos(self):
        yt = FakeYouTube({
            "channels": {"items": [{"id": "UC" + "a" * 22}]},
            "search": {"items": [{"id": {"videoId": "abcdefghijk"}},
                                 {"id": {"videoId": "bbcdefghijk"}}]},
            "videos": {"items": [_yt_video("PT2H", ["dog", "calm"]),
                                 _yt_video("PT4H", ["dog", "calm"])]},
        })
        described = yt.describe_seed({"type": "channel", "value": "@example"})
        self.assertIn("UC" + "a" * 22, described["url"])
        self.assertTrue(described["statements"])
        self.assertEqual(sorted(described["recurring_tags"]), ["calm", "dog"])
        self.assertEqual(described["durations"], [2 * 3600, 4 * 3600])

    def test_a_video_seed_reads_its_id_out_of_a_url(self):
        yt = FakeYouTube({"videos": {"items": [_yt_video("PT1H", [])]}})
        described = yt.describe_seed(
            {"type": "video", "value": "https://www.youtube.com/watch?v=abcdefghijk"})
        self.assertIn("abcdefghijk", described["url"])
        self.assertEqual(described["durations"], [3600])

    def test_an_unreadable_video_seed_is_refused(self):
        yt = FakeYouTube({})
        with self.assertRaises(research.ResearchError):
            yt.describe_seed({"type": "video", "value": "not a video"})


class SeedContractTest(ResearchFixture):
    """A named channel or video is an instruction, not a hint: a run that
    researched everything except the seed has not done what it was told."""

    def _seeded(self):
        return brief(research_topics=["visuals"],
                     seed_references=[{"type": "channel", "value": "@example"}])

    def test_a_seed_that_cannot_be_researched_fails_the_run(self):
        b = self._seeded()
        provider = self._provider_for(b)       # answers topics, not the seed
        unconfigured = research.YouTubeProvider(api_key="")
        with self.assertRaises(research.ResearchError) as caught:
            research.research_project("vid-1", b, provider=provider, youtube=unconfigured)
        self.assertIn("@example", str(caught.exception))

    def test_a_seed_researched_through_youtube_is_recorded_with_its_provider(self):
        b = self._seeded()
        yt = FakeYouTube({
            "channels": {"items": [{"id": "UC" + "a" * 22}]},
            "search": {"items": [{"id": {"videoId": "abcdefghijk"}},
                                 {"id": {"videoId": "bbcdefghijk"}}]},
            "videos": {"items": [_yt_video("PT2H", ["dog", "calm"]),
                                 _yt_video("PT4H", ["dog", "calm"])]},
        })
        artifact = research.research_project(
            "vid-1", b, provider=self._provider_for(b), youtube=yt)
        self.assertEqual(len(artifact["seed_coverage"]), 1)
        self.assertEqual(artifact["seed_coverage"][0]["provider"], "youtube")
        self.assertGreaterEqual(artifact["seed_coverage"][0]["findings"], 1)
        self.assertIn("youtube", artifact["provider"])

    def test_a_seed_falls_back_to_search_when_youtube_is_absent(self):
        b = self._seeded()
        answers = dict(research._search_queries(b))
        seed_query = research._seed_queries(b)[0][1]
        provider = FakeSearchProvider(by_query={
            answers["visuals"]: self._adequate_results("visuals"),
            answers["concept"]: self._adequate_results("concept"),
            seed_query: self._adequate_results("the seed channel"),
        })
        artifact = research.research_project(
            "vid-1", b, provider=provider, youtube=research.YouTubeProvider(api_key=""))
        self.assertEqual(artifact["seed_coverage"][0]["provider"], "fake")


class FindingsDigestTest(unittest.TestCase):

    def test_empty_is_an_empty_string(self):
        self.assertEqual(research.findings_digest(None), "")
        self.assertEqual(research.findings_digest({"findings": []}), "")

    def test_each_line_states_topic_and_kind_never_just_the_statement(self):
        artifact = {"findings": [
            research._make_finding("v", "observation", "pacing", "cuts every few seconds",
                                    source_url="https://x.test", confidence="VERIFIED"),
        ]}
        digest = research.findings_digest(artifact)
        self.assertIn("[pacing/observation]", digest)
        self.assertIn("cuts every few seconds", digest)

    def test_caps_lines_per_topic(self):
        findings = [
            research._make_finding("v", "observation", "audio", f"statement {i}",
                                    source_url="https://x.test", confidence="VERIFIED")
            for i in range(10)
        ]
        digest = research.findings_digest({"findings": findings}, max_per_topic=2)
        self.assertEqual(digest.count("[audio/observation]"), 2)


# --------------------------------------------------------------------------
# competitor analysis - every source stubbed at its single network step
# --------------------------------------------------------------------------

def _renderer(vid, title, channel, views, length, published, channel_id=None,
              badges=(), rich=False):
    r = {
        "videoId": vid,
        "title": {"runs": [{"text": title}]},
        "ownerText": {"runs": [{"text": channel, "navigationEndpoint": {
            "browseEndpoint": {"browseId": channel_id or f"UC{channel[:3]}"}}}]},
        "viewCountText": {"simpleText": views},
        "publishedTimeText": {"simpleText": published},
        "thumbnail": {"thumbnails": [{"url": f"https://i.ytimg.test/{vid}/default.jpg"},
                                     {"url": f"https://i.ytimg.test/{vid}/hq720.jpg"}]},
        "badges": [{"metadataBadgeRenderer": {"label": b}} for b in badges],
    }
    if length:
        r["lengthText"] = {"simpleText": length}
    if rich:
        r["richThumbnail"] = {"movingThumbnailRenderer": {}}
    return {"videoRenderer": r}


def results_page(*renderers):
    data = {"contents": {"twoColumnSearchResultsRenderer": {"primaryContents": {
        "sectionListRenderer": {"contents": [{"itemSectionRenderer": {
            "contents": list(renderers) + [{"adSlotRenderer": {}}]}}]}}}}}
    return ("<html><script>var ytInitialData = " + json.dumps(data)
            + ";</script><script>var other = {};</script></html>")


SLEEP_PAGE = results_page(
    _renderer("aaaaaaaaaa1", "Rain Sounds for Sleeping | 10 Hours Black Screen", "Rain Channel",
              "12,345,678 views", "10:00:00", "2 years ago", "UCrain", badges=["4K"], rich=True),
    _renderer("bbbbbbbbbb2", "Thunderstorm & Rain for Deep Sleep", "Storm Sleep",
              "1,500,000 views", "8:00:00", "5 months ago", "UCstorm"),
    _renderer("cccccccccc3", "BROWN NOISE dark screen - 8 hours", "Rain Channel",
              "900K views", "8:00:01", "3 hours ago", "UCrain"),
    _renderer("dddddddddd4", "Live: rain now", "Live Rain", "1.2K watching", None, "", "UClive"),
)


class StubPage(research.YouTubeSearchPageSource):
    def __init__(self, pages=None, error=None):
        self.pages, self.error, self.urls = pages or {}, error, []

    def configured(self):
        return True

    def _fetch(self, url):
        self.urls.append(url)
        if self.error:
            raise self.error
        return self.pages.get("default", "")


FEED_XML = b"""<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns:yt="http://www.youtube.com/xml/schemas/2015"
      xmlns:media="http://search.yahoo.com/mrss/" xmlns="http://www.w3.org/2005/Atom">
 <title>Rain Channel</title>
 <entry><yt:videoId>v3</yt:videoId><title>c</title><published>2026-09-15T00:00:00+00:00</published>
  <media:group><media:community><media:statistics views="3000"/></media:community></media:group></entry>
 <entry><yt:videoId>v2</yt:videoId><title>b</title><published>2026-09-08T00:00:00+00:00</published>
  <media:group><media:community><media:statistics views="2000"/></media:community></media:group></entry>
 <entry><yt:videoId>v1</yt:videoId><title>a</title><published>2026-09-01T00:00:00+00:00</published>
  <media:group><media:community><media:statistics views="1000"/></media:community></media:group></entry>
</feed>"""


class StubFeed(research.YouTubeFeedSource):
    def __init__(self, bodies=None):
        self.bodies, self.urls = bodies or {}, []

    def configured(self):
        return True

    def _fetch(self, url):
        self.urls.append(url)
        for channel_id, body in self.bodies.items():
            if url.endswith(channel_id):
                if isinstance(body, Exception):
                    raise body
                return body
        raise research.ResearchError("youtube-rss returned HTTP 404")


class YouTubeListingParseTest(unittest.TestCase):

    def test_reads_videos_out_of_yt_initial_data_in_rank_order(self):
        videos = research.videos_from_initial_data(
            research.extract_yt_initial_data(SLEEP_PAGE))
        self.assertEqual([v["video_id"] for v in videos],
                         ["aaaaaaaaaa1", "bbbbbbbbbb2", "cccccccccc3", "dddddddddd4"])
        first = videos[0]
        self.assertEqual(first["url"], "https://www.youtube.com/watch?v=aaaaaaaaaa1")
        self.assertEqual(first["channel"], "Rain Channel")
        self.assertEqual(first["channel_id"], "UCrain")
        self.assertEqual(first["views"], 12345678)
        self.assertEqual(first["duration_seconds"], 36000)
        self.assertEqual(first["badges"], ["4K"])
        self.assertTrue(first["animated_thumbnail"])
        self.assertEqual(first["thumbnail_url"], "https://i.ytimg.test/aaaaaaaaaa1/hq720.jpg")
        self.assertAlmostEqual(first["age_days"], 730.5, places=0)
        self.assertEqual(videos[2]["views"], 900000)
        self.assertIsNone(videos[3]["duration_seconds"])   # live: no running time

    def test_parsers_for_counts_clocks_and_ages(self):
        self.assertEqual(research.parse_view_count("No views"), 0)
        self.assertEqual(research.parse_view_count("1.2M views"), 1200000)
        self.assertIsNone(research.parse_view_count(""))
        self.assertEqual(research.parse_clock_duration("1:02:03"), 3723)
        self.assertIsNone(research.parse_clock_duration("LIVE"))
        self.assertEqual(research.parse_relative_age_days("Streamed 3 weeks ago"), 21)
        self.assertIsNone(research.parse_relative_age_days("Premieres soon"))

    def test_a_page_without_yt_initial_data_is_an_error_not_an_empty_niche(self):
        page = StubPage({"default": "<html>Before you continue to YouTube</html>"})
        with self.assertRaises(research.ResearchError) as ctx:
            page.videos("rain sounds")
        self.assertIn("ytInitialData", str(ctx.exception))
        self.assertIn("search_query=rain+sounds", page.urls[0])

    def test_feed_gives_dated_uploads_and_refuses_entity_declarations(self):
        feed = StubFeed({"UCrain": FEED_XML})
        data = feed.uploads("UCrain")
        self.assertEqual(data["channel"], "Rain Channel")
        self.assertEqual([e["views"] for e in data["entries"]], [3000, 2000, 1000])
        self.assertEqual(feed.urls[0],
                         "https://www.youtube.com/feeds/videos.xml?channel_id=UCrain")
        evil = StubFeed({"UCevil": b'<?xml version="1.0"?><!DOCTYPE x [<!ENTITY a "aaaa">]><feed/>'})
        with self.assertRaises(research.ResearchError):
            evil.uploads("UCevil")

    def test_the_data_api_answers_in_the_same_shape(self):
        yt = FakeYouTube({
            "search": {"items": [{"id": {"videoId": "eeeeeeeeee5"}}]},
            "videos": {"items": [{"id": "eeeeeeeeee5",
                                  "contentDetails": {"duration": "PT8H"},
                                  "statistics": {"viewCount": "4200"},
                                  "snippet": {"title": "Ocean Waves 8 Hours",
                                              "channelTitle": "Sea", "channelId": "UCsea",
                                              "publishedAt": "2026-01-01T00:00:00Z",
                                              "thumbnails": {"high": {"url": "https://t/h.jpg"}}}}]},
        })
        [video] = yt.videos("ocean waves")
        self.assertEqual((video["video_id"], video["views"], video["duration_seconds"],
                          video["channel_id"], video["source"]),
                         ("eeeeeeeeee5", 4200, 28800, "UCsea", "youtube"))
        self.assertIsNotNone(video["age_days"])


class CompetitorRoutingTest(ResearchFixture):

    def test_api_failure_falls_through_to_the_keyless_page_with_reasons(self):
        class BrokenApi(FakeYouTube):
            def _get(self, endpoint, params):
                raise research.ResearchError("youtube search failed: HTTP 403 - quotaExceeded")
        page = StubPage({"default": SLEEP_PAGE})
        videos, attempts, urls = research.collect_competitor_videos(
            ["rain sounds"], [BrokenApi({}), page])
        self.assertEqual([(a["source"], a["outcome"]) for a in attempts],
                         [("youtube", "error"), ("youtube-keyless", "ok")])
        self.assertIn("quotaExceeded", attempts[0]["detail"])
        self.assertEqual(videos[0]["video_id"], "aaaaaaaaaa1")       # ranked by views
        self.assertEqual(urls, [page.search_url("rain sounds")])

    def test_every_source_failing_returns_nothing_and_never_raises(self):
        page = StubPage(error=research.ResearchError("youtube-keyless unreachable: 403"))
        videos, attempts, _ = research.collect_competitor_videos(["q1", "q2"], [page])
        self.assertEqual(videos, [])
        self.assertEqual([a["outcome"] for a in attempts], ["error", "error"])

    def test_a_listing_is_cached_per_source_and_query(self):
        page = StubPage({"default": SLEEP_PAGE})
        research.collect_competitor_videos(["rain sounds"], [page])
        exploding = StubPage(error=AssertionError("must not fetch again"))
        videos, attempts, _ = research.collect_competitor_videos(["rain sounds"], [exploding])
        self.assertEqual(len(videos), 4)
        self.assertIn("cached", attempts[0]["detail"])

    def test_queries_come_from_the_concept_title_then_the_niche(self):
        concept = {"working_title_pattern": "Brown Noise for Deep Sleep | 8 Hours | Dark Screen"}
        self.assertEqual(research.competitor_queries({"niche": "adult_sleep"}, concept),
                         ["Brown Noise for Deep Sleep", "adult sleep video"])
        self.assertEqual(research.competitor_queries({"niche": ""}, None), [])

    def test_test_mode_uses_the_fixture_and_never_the_network(self):
        self.env.stop()
        self.env = isolated_env(TEST_MODE="1")
        sources = research.build_competitor_sources()
        self.assertEqual([s.name for s in sources], ["fixture-competitors"])
        self.assertIsNone(research.build_feed_source())

    def test_keyless_default_route_order(self):
        self.env.stop()
        self.env = isolated_env(SEARCH_KEYLESS="1", YOUTUBE_API_KEY="k")
        self.assertEqual([s.name for s in research.build_competitor_sources()],
                         ["youtube", "youtube-keyless"])
        self.env.stop()
        self.env = isolated_env(SEARCH_KEYLESS="1")
        self.assertEqual([s.name for s in research.build_competitor_sources()],
                         ["youtube-keyless"])


class CompetitorAnalysisTest(ResearchFixture):

    def analyse(self, feed=None):
        return research.competitor_analysis(
            "vid-c", {"niche": "adult_sleep"},
            concept={"working_title_pattern": "Rain Sounds for Sleep | 10 Hours"},
            sources=[StubPage({"default": SLEEP_PAGE})],
            feed=feed or StubFeed({"UCrain": FEED_XML,
                                   "UCstorm": research.ResearchError("youtube-rss returned HTTP 404")}))

    def test_one_sourced_observation_per_top_video_and_per_channel_cadence(self):
        findings, record = self.analyse()
        observations = [f for f in findings if f["kind"] == "observation"]
        per_video = [f for f in observations if f["source_url"].startswith("https://www.youtube.com/watch")]
        self.assertEqual(len(per_video), 4)
        top = per_video[0]
        self.assertEqual(top["topic"], "competitors")
        self.assertEqual(top["confidence"], "VERIFIED")
        self.assertEqual(top["source_title"], "Rain Sounds for Sleeping | 10 Hours Black Screen")
        self.assertIn("runs 600 minutes", top["statement"])
        self.assertIn("12.3M views", top["statement"])
        self.assertIn("black", top["statement"])
        # "3 hours ago" must never read as a three-hour running time.
        recent = next(f for f in per_video if "cccccccccc3" in f["source_url"])
        self.assertIn("within the last day", recent["statement"])
        self.assertNotIn("hours ago", recent["statement"])
        cadence = [f for f in observations if "feeds/videos.xml" in f["source_url"]]
        self.assertEqual(len(cadence), 1)
        self.assertIn("median gap of 7.0 days", cadence[0]["statement"])
        self.assertEqual(record["channels"][0]["uploads_sampled"], 3)
        self.assertIn(("youtube-rss", "error"),
                      [(a["source"], a["outcome"]) for a in record["routing"]])

    def test_interpretations_count_across_videos_and_cite_their_evidence(self):
        findings, record = self.analyse()
        interpretations = {f["topic"]: f for f in findings if f["kind"] == "interpretation"}
        self.assertEqual(set(interpretations), {"duration", "titles", "thumbnails", "competitors"})
        for f in interpretations.values():
            self.assertEqual(f["confidence"], "INFERRED")
            self.assertIsNone(f["source_url"])
            self.assertTrue(f["evidence_finding_ids"])
        self.assertIn("median of 480 minutes", interpretations["duration"]["statement"])
        self.assertIn("2 state a running time", interpretations["titles"]["statement"])
        self.assertIn("black screen (1 of 4)", interpretations["thumbnails"]["statement"])
        self.assertIn("animated preview", interpretations["thumbnails"]["statement"])
        self.assertEqual(record["summary"]["median_duration_seconds"], 28801)
        self.assertEqual(record["summary"]["videos"], 4)

    def test_competitor_evidence_reaches_the_production_directives(self):
        findings, _ = self.analyse()
        directives = research.production_directives({"findings": findings})
        values = directives["values"]
        self.assertEqual(values["typical_duration_seconds"], 28800)
        self.assertIn("rain", values["audio_emphasis"])
        self.assertEqual(values["motion_style"], "still")
        duration = next(d for d in directives["decisions"]
                        if d["parameter"] == "typical_duration_seconds")
        self.assertTrue(all(u.startswith("https://www.youtube.com/watch") for u in duration["source_urls"]))


class ResearchProjectCompetitorTest(ResearchFixture):

    def test_a_dead_web_route_still_researches_from_the_competitor_listing(self):
        dead = FakeSearchProvider(error=subject_research.SearchError("every search provider failed"))
        artifact = research.research_project(
            "vid-p", brief(), provider=dead,
            competitors=[StubPage({"default": SLEEP_PAGE})],
            concept={"working_title_pattern": "Rain Sounds for Sleep | 10 Hours"})
        self.assertIn("competitors", artifact["topics_covered"])
        self.assertTrue(artifact["search_errors"])
        self.assertEqual(artifact["competitor_analysis"]["videos"][0]["video_id"], "aaaaaaaaaa1")
        self.assertIn("youtube-keyless", artifact["provider"])
        self.assertEqual(research.load_findings("vid-p"), artifact)

    def test_both_halves_failing_is_refused_naming_every_route(self):
        dead = FakeSearchProvider(error=subject_research.SearchError("duckduckgo served a bot challenge"))
        page = StubPage(error=research.ResearchError("youtube-keyless unreachable: 403"))
        with self.assertRaises(research.ResearchError) as ctx:
            research.research_project("vid-p", brief(), provider=dead, competitors=[page])
        self.assertIn("bot challenge", str(ctx.exception))
        self.assertIn("youtube-keyless unreachable", str(ctx.exception))
        self.assertIsNone(research.load_findings("vid-p"))


if __name__ == "__main__":
    unittest.main()
