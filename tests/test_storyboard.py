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


class OneSeedPerPictureTest(unittest.TestCase):
    """Reuse is keyed on the picture asked for, never on the scene index."""

    def test_scenes_asking_for_the_same_picture_share_a_seed_and_a_digest(self):
        board = storyboard.build_storyboard("vid-1", metadata(), spec(), scene_count=4)
        prompts = {s["image_prompt"] for s in board["scenes"]}
        self.assertEqual(len(prompts), 1, "no category hints: one picture asked for")
        self.assertEqual(len({s["generation"]["seed"] for s in board["scenes"]}), 1)
        self.assertEqual(
            len({s["generation"]["request_digest"] for s in board["scenes"]}), 1)

    def test_the_prompt_carries_no_scene_bookkeeping(self):
        board = storyboard.build_storyboard("vid-1", metadata(), spec(), scene_count=3)
        for scene in board["scenes"]:
            self.assertNotIn("scene ", scene["image_prompt"])
            self.assertNotIn(scene["section"], scene["image_prompt"])

    def test_distinct_categories_still_get_their_own_seed_and_render(self):
        profile = {"niche": "test_niche", "observation_count": 2, "confidence": "INFERRED",
                   "visual_categories": [{"value": "interior"}, {"value": "landscape"}]}
        board = storyboard.build_storyboard(
            "vid-1", metadata(), spec(), profile=profile, scene_count=4)
        seeds = [s["generation"]["seed"] for s in board["scenes"]]
        digests = [s["generation"]["request_digest"] for s in board["scenes"]]
        self.assertEqual(len(set(seeds)), 2)
        self.assertEqual(len(set(digests)), 2)
        self.assertEqual(seeds[0], seeds[2])
        self.assertEqual(digests[1], digests[3])


class ApplySceneMotifsTest(unittest.TestCase):

    def test_motif_replaces_the_generic_category_and_changes_the_digest(self):
        board = storyboard.build_storyboard("vid-1", metadata(), spec(), scene_count=2)
        before_digest = board["scenes"][0]["generation"]["request_digest"]
        motifs = {s["scene_id"]: f"a specific motif for {s['scene_id']}" for s in board["scenes"]}
        storyboard.apply_scene_motifs(board, motifs)
        self.assertIn("a specific motif for s01", board["scenes"][0]["image_prompt"])
        self.assertNotEqual(board["scenes"][0]["generation"]["request_digest"], before_digest)

    def test_two_scenes_given_the_same_environment_become_one_render(self):
        """A deliberately repeated environment is a reuse, not a second
        near-identical generation."""
        board = storyboard.build_storyboard("vid-1", metadata(), spec(), scene_count=3)
        storyboard.apply_scene_motifs(board, {
            "s01": "the same held living room", "s02": "the same held living room",
            "s03": "the garden at dusk"})
        digests = [s["generation"]["request_digest"] for s in board["scenes"]]
        self.assertEqual(digests[0], digests[1])
        self.assertNotEqual(digests[0], digests[2])
        self.assertEqual(board["scenes"][0]["generation"]["seed"],
                         board["scenes"][1]["generation"]["seed"])
        self.assertNotEqual(board["scenes"][0]["generation"]["seed"],
                            board["scenes"][2]["generation"]["seed"])

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

    def test_a_looped_board_keeps_its_full_length_through_save(self):
        board = storyboard.build_storyboard(
            "vid-1", SLEEP_METADATA, spec(duration_seconds=10800.0))
        storyboard.save("vid-1", board)
        reloaded = storyboard.load("vid-1")
        self.assertEqual(reloaded["timeline_seconds"], 10800.0)
        self.assertEqual(reloaded["loop"]["unique_scenes"], len(reloaded["scenes"]))


# A silent sleep video: no script, a calm concept.
SLEEP_METADATA = {
    "concept": "rain on a window for sleep",
    "script": "",
    "visual_plan": {"prompt": "misty forest lake at night", "style": "deep-night"},
}


class CalmMotionTest(unittest.TestCase):
    """Sleep/relaxation content gets the drift and parallax moves, mixed
    with push-ins, never the same move twice in a row."""

    def _kinds(self, board):
        return [s["motion"]["kind"] for s in board["scenes"]]

    def test_calm_content_uses_drift_and_parallax(self):
        board = storyboard.build_storyboard(
            "vid-sleep", SLEEP_METADATA, spec(duration_seconds=1200.0))
        kinds = set(self._kinds(board))
        self.assertEqual(board["motion_profile"], "calm")
        self.assertIn("drift", kinds)
        self.assertTrue(kinds & {"parallax", "parallax_in"})
        self.assertTrue(kinds & {"zoom_in", "zoom_out", "pan_zoom"})

    def test_narrated_non_calm_content_keeps_the_default_cycle(self):
        board = storyboard.build_storyboard(
            "vid-1", metadata(), spec(duration_seconds=60.0))
        self.assertEqual(board["motion_profile"], "default")
        self.assertTrue(set(self._kinds(board)) <= set(storyboard.MOTION_CYCLE))

    def test_no_move_follows_itself(self):
        for duration in (600.0, 1200.0, 3600.0, 10800.0):
            for video_id in ("a", "b", "c", "vid-sleep"):
                kinds = self._kinds(storyboard.build_storyboard(
                    video_id, SLEEP_METADATA, spec(duration_seconds=duration)))
                for i in range(1, len(kinds)):
                    self.assertNotEqual(kinds[i], kinds[i - 1], (duration, video_id, i))

    def test_every_style_cycle_avoids_repeats_across_its_wrap(self):
        for cycle in list(storyboard.MOTION_STYLE_CYCLES.values()) + [storyboard.MOTION_CYCLE]:
            for i in range(len(cycle)):
                self.assertNotEqual(cycle[i], cycle[i - 1], cycle)

    def test_a_board_using_the_new_moves_validates(self):
        board = storyboard.build_storyboard(
            "vid-sleep", SLEEP_METADATA, spec(duration_seconds=1200.0))
        problems = storyboard.validate(board)
        self.assertTrue(problems)
        self.assertTrue(all("no image assigned" in p for p in problems), problems)

    def test_hazy_calm_concepts_get_the_fog_overlay_on_some_scenes(self):
        board = storyboard.build_storyboard(
            "vid-sleep", SLEEP_METADATA, spec(duration_seconds=1200.0))
        fogged = [s for s in board["scenes"] if s["motion"].get("ambient") == "fog"]
        self.assertTrue(fogged)
        self.assertLess(len(fogged), len(board["scenes"]))
        clear = dict(SLEEP_METADATA, visual_plan={"prompt": "a quiet beach at dusk"})
        board = storyboard.build_storyboard("vid-sleep", clear, spec(duration_seconds=1200.0))
        self.assertFalse(any(s["motion"].get("ambient") for s in board["scenes"]))


class LongFormLoopTest(unittest.TestCase):
    """A three-hour silent video plans one unique cycle and says so."""

    def test_three_hours_plans_a_twenty_minute_cycle_repeated_nine_times(self):
        board = storyboard.build_storyboard(
            "vid-sleep", SLEEP_METADATA, spec(duration_seconds=10800.0))
        loop = board["loop"]
        self.assertEqual(loop["mode"], "looped_cycle")
        self.assertEqual(loop["full_seconds"], 10800.0)
        self.assertAlmostEqual(loop["cycle_seconds"], 1200.0, delta=0.1)
        self.assertAlmostEqual(loop["repeats"], 9.0, delta=0.01)
        self.assertEqual(loop["unique_scenes"], len(board["scenes"]))
        self.assertEqual(board["timeline_seconds"], 10800.0)

    def test_the_last_scene_dissolves_back_into_the_first(self):
        board = storyboard.build_storyboard(
            "vid-sleep", SLEEP_METADATA, spec(duration_seconds=10800.0))
        self.assertEqual(board["scenes"][-1]["transition"]["kind"], "crossfade")
        self.assertGreater(board["scenes"][-1]["transition"]["duration_seconds"], 0)
        kinds = [s["motion"]["kind"] for s in board["scenes"]]
        self.assertNotEqual(kinds[-1], kinds[0])

    def test_the_cycle_length_is_configurable_and_zero_disables_it(self):
        board = storyboard.build_storyboard(
            "vid-sleep", SLEEP_METADATA,
            spec(duration_seconds=9000.0, unique_cycle_seconds=1800))
        self.assertAlmostEqual(board["loop"]["cycle_seconds"], 1800.0, delta=0.1)
        board = storyboard.build_storyboard(
            "vid-sleep", SLEEP_METADATA,
            spec(duration_seconds=3600.0, unique_cycle_seconds=0))
        self.assertNotIn("loop", board)
        self.assertAlmostEqual(board["timeline_seconds"], 3600.0, delta=0.5)

    def test_a_video_shorter_than_one_and_a_half_cycles_is_rendered_unique(self):
        board = storyboard.build_storyboard(
            "vid-sleep", SLEEP_METADATA, spec(duration_seconds=1500.0))
        self.assertNotIn("loop", board)

    def test_narrated_video_never_loops(self):
        board = storyboard.build_storyboard(
            "vid-1", metadata(), spec(duration_seconds=10800.0))
        self.assertNotIn("loop", board)
        self.assertEqual(board["scenes"][-1]["transition"]["kind"], "cut")

    def test_three_hours_is_not_capped(self):
        board = storyboard.build_storyboard(
            "vid-sleep", SLEEP_METADATA, spec(duration_seconds=4 * 3600.0))
        self.assertEqual(board["timeline_seconds"], 4 * 3600.0)


if __name__ == "__main__":
    unittest.main()
