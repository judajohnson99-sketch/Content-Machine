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

    def tearDown(self):
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


if __name__ == "__main__":
    unittest.main()
