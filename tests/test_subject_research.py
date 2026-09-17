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


if __name__ == "__main__":
    unittest.main()
