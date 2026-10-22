"""Semantic evidence is content-bound; metadata never asserts licensing."""
import unittest
from unittest.mock import patch
from scripts import media_intelligence as intelligence


class RetrievalTests(unittest.TestCase):
    def asset(self, identity, vector, description="Unhelpful description"):
        return {"id": identity, "description": description, "tags": [],
                "analysis": {"model": intelligence.MODEL_ID, "source_sha256": identity,
                             "embedding": vector}}

    @patch.object(intelligence, "available", return_value=True)
    @patch.object(intelligence, "text_vector", return_value=[1.0, 0.0])
    def test_visual_semantics_retrieve_without_filename_or_description(self, *_):
        forest = self.asset("forest", [0.8, 0.2])
        city = self.asset("city", [0.1, 0.9])
        self.assertEqual(intelligence.rank([city, forest], "quiet moonlit forest"), [forest])
        self.assertNotIn("rights", forest)

    @patch.object(intelligence, "available", return_value=True)
    @patch.object(intelligence, "text_vector", return_value=[1.0, 0.0])
    def test_stale_model_or_content_hash_cannot_influence_selection(self, *_):
        stale = self.asset("new-bytes", [1.0, 0.0])
        stale["analysis"]["source_sha256"] = "old-bytes"
        self.assertEqual(intelligence.rank([stale], "forest"), [])
        stale["analysis"]["source_sha256"] = "new-bytes"
        stale["analysis"]["model"] = "another-model"
        self.assertEqual(intelligence.rank([stale], "forest"), [])

    @patch.object(intelligence, "available", return_value=False)
    def test_offline_description_search_needs_no_model(self, *_):
        asset = self.asset("audio", [], "Quiet rain and wind in a forest")
        self.assertEqual(intelligence.rank([asset], "quiet forest"), [asset])
        asset["filename"] = "ocean.wav"
        self.assertEqual(intelligence.rank([asset], "ocean"), [])

    @patch.object(intelligence, "available", return_value=True)
    @patch.object(intelligence, "text_vector", return_value=[1.0, 0.0])
    def test_malformed_vector_is_not_trusted(self, *_):
        for vector in ([float("nan"), 1], [1], ["1", 0]):
            self.assertEqual(intelligence.rank([self.asset("bad", vector)], "forest"), [])

    @patch.object(intelligence, "available", return_value=True)
    @patch.object(intelligence, "text_vector", side_effect=RuntimeError("model unavailable"))
    def test_model_failure_and_malformed_analysis_preserve_source_search(self, *_):
        asset = self.asset("bad", [])
        asset.update(source="Forest archive", analysis="malformed")
        self.assertEqual(intelligence.rank([asset], "forest archive"), [asset])
