#!/usr/bin/env python3
"""Tests for scripts/storyboard.py: the deterministic scene planner.

Covers script splitting, scene-count/timeline arithmetic, the reused
GenerationRequest digest, motif application, and the save/load round trip.
No image is ever generated here - scene_request only builds the request
object, it never calls a provider.
"""
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import storyboard  # noqa: E402


def metadata(**overrides):
    base = {
        "concept": "A test concept",
        "script": "First sentence here. Second sentence follows. Third and final one.",
        "visual_plan": {"prompt": "dim archival stills", "negative_prompt": "text, watermark",
                        "style": "deep-night"},
    }
    base.update(overrides)
    return base


def spec(**overrides):
    base = {"duration_seconds": 30.0, "width": 1920, "height": 1080, "fps": 30}
    base.update(overrides)
    return base


class SplitScriptTest(unittest.TestCase):

    def test_keeps_sentences_whole_when_they_fit(self):
        out = storyboard.split_script("One. Two. Three.", 3)
        self.assertEqual(out, ["One.", "Two.", "Three."])

    def test_pads_with_empty_strings_when_script_is_shorter(self):
        out = storyboard.split_script("Only one sentence.", 3)
        self.assertEqual(out, ["Only one sentence.", "", ""])

    def test_front_loads_the_remainder_when_scenes_undercount_sentences(self):
        out = storyboard.split_script("A. B. C. D. E.", 2)
        self.assertEqual(len(out), 2)
        self.assertEqual(" ".join(out).replace("  ", " ").count("."), 5)

    def test_empty_script_produces_empty_strings_not_invented_text(self):
        self.assertEqual(storyboard.split_script("", 3), ["", "", ""])


class BuildStoryboardTest(unittest.TestCase):

    def test_scene_count_defaults_from_target_duration(self):
        board = storyboard.build_storyboard("vid-1", metadata(), spec(duration_seconds=30.0))
        self.assertGreaterEqual(len(board["scenes"]), 1)

    def test_explicit_scene_count_is_honoured(self):
        board = storyboard.build_storyboard(
            "vid-1", metadata(), spec(duration_seconds=30.0), scene_count=4)
        self.assertEqual(len(board["scenes"]), 4)

    def test_timeline_lands_on_target_duration(self):
        board = storyboard.build_storyboard(
            "vid-1", metadata(), spec(duration_seconds=20.0), scene_count=4)
        self.assertAlmostEqual(board["timeline_seconds"], 20.0, delta=0.05)

    def test_rejects_non_positive_duration(self):
        with self.assertRaises(storyboard.StoryboardError):
            storyboard.build_storyboard("vid-1", metadata(), spec(duration_seconds=0))

    def test_too_many_scenes_over_the_slot_limit_raises(self):
        with self.assertRaises(storyboard.StoryboardError):
            storyboard.build_storyboard(
                "vid-1", metadata(), spec(duration_seconds=5.0),
                scene_count=storyboard.MAX_SCENES + 1)

    def test_source_generation_is_512_by_default_not_the_output_size(self):
        board = storyboard.build_storyboard("vid-1", metadata(), spec(), scene_count=2)
        self.assertEqual(board["source_generation"]["width"], storyboard.DEFAULT_SOURCE_WIDTH)
        self.assertEqual(board["source_generation"]["height"], storyboard.DEFAULT_SOURCE_HEIGHT)
        self.assertEqual(board["target"]["width"], 1920)

    def test_scene_prompt_carries_the_visual_plan_prompt(self):
        board = storyboard.build_storyboard("vid-1", metadata(), spec(), scene_count=2)
        for scene in board["scenes"]:
            self.assertIn("dim archival stills", scene["image_prompt"])

    def test_is_deterministic_given_the_same_inputs(self):
        a = storyboard.build_storyboard("vid-1", metadata(), spec(), scene_count=3)
        b = storyboard.build_storyboard("vid-1", metadata(), spec(), scene_count=3)
        self.assertEqual(a["scenes"], b["scenes"])

    def test_digest_changes_when_the_prompt_changes(self):
        board_a = storyboard.build_storyboard("vid-1", metadata(), spec(), scene_count=2)
        board_b = storyboard.build_storyboard(
            "vid-1", metadata(visual_plan={"prompt": "a totally different prompt"}),
            spec(), scene_count=2)
        digest_a = board_a["scenes"][0]["generation"]["request_digest"]
        digest_b = board_b["scenes"][0]["generation"]["request_digest"]
        self.assertNotEqual(digest_a, digest_b)


class ApplySceneMotifsTest(unittest.TestCase):

    def test_motif_replaces_the_generic_category_and_changes_the_digest(self):
        board = storyboard.build_storyboard("vid-1", metadata(), spec(), scene_count=2)
        before_digest = board["scenes"][0]["generation"]["request_digest"]
        motifs = {s["scene_id"]: f"a specific motif for {s['scene_id']}" for s in board["scenes"]}
        storyboard.apply_scene_motifs(board, motifs)
        self.assertIn("a specific motif for s01", board["scenes"][0]["image_prompt"])
        self.assertNotEqual(board["scenes"][0]["generation"]["request_digest"], before_digest)

    def test_scenes_missing_from_the_motif_map_are_left_untouched(self):
        board = storyboard.build_storyboard("vid-1", metadata(), spec(), scene_count=2)
        original_prompt = board["scenes"][1]["image_prompt"]
        storyboard.apply_scene_motifs(board, {"s01": "only the first scene"})
        self.assertEqual(board["scenes"][1]["image_prompt"], original_prompt)


class ValidateTest(unittest.TestCase):

    def test_freshly_built_storyboard_only_complains_about_missing_images(self):
        board = storyboard.build_storyboard("vid-1", metadata(), spec(), scene_count=2)
        problems = storyboard.validate(board)
        self.assertTrue(all("no image assigned" in p for p in problems))

    def test_wrong_version_is_rejected(self):
        board = storyboard.build_storyboard("vid-1", metadata(), spec(), scene_count=2)
        board["storyboard_version"] = 999
        problems = storyboard.validate(board)
        self.assertTrue(any("storyboard_version" in p for p in problems))


class SaveLoadTest(unittest.TestCase):

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self._prev = storyboard.PROJECTS_DIR
        storyboard.PROJECTS_DIR = self.tmp

    def tearDown(self):
        storyboard.PROJECTS_DIR = self._prev
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_round_trips_through_disk(self):
        board = storyboard.build_storyboard("vid-1", metadata(), spec(), scene_count=2)
        storyboard.save("vid-1", board)
        reloaded = storyboard.load("vid-1")
        self.assertEqual(reloaded["video_id"], "vid-1")
        self.assertEqual(len(reloaded["scenes"]), 2)

    def test_load_returns_none_when_nothing_saved(self):
        self.assertIsNone(storyboard.load("no-such-video"))


if __name__ == "__main__":
    unittest.main()
