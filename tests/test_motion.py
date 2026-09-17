#!/usr/bin/env python3
"""Tests for scripts/motion.py: pure ffmpeg-filter arithmetic.

No ffmpeg process is ever started here - build_scene_filter and
build_transition_chain only assemble filter *text*, which is exactly what
these tests pin.
"""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import motion  # noqa: E402

TARGET = {"width": 100, "height": 100, "fps": 30}


def scene(**overrides):
    base = {
        "scene_id": "s01",
        "duration_seconds": 2.0,
        "motion": {"kind": "static", "fit": "cover"},
        "transition": {"kind": "crossfade", "duration_seconds": 0.5},
    }
    base.update(overrides)
    return base


class ValidateMotionTest(unittest.TestCase):

    def test_known_motion_fit_transition_is_clean(self):
        self.assertEqual(motion.validate_motion("zoom_in", fit="cover", transition="cut"), [])

    def test_unknown_motion_is_reported(self):
        problems = motion.validate_motion("teleport")
        self.assertTrue(any("teleport" in p for p in problems))

    def test_unknown_fit_is_reported(self):
        problems = motion.validate_motion("static", fit="wobble")
        self.assertTrue(any("wobble" in p for p in problems))

    def test_unknown_transition_is_reported(self):
        problems = motion.validate_motion("static", transition="wipe")
        self.assertTrue(any("wipe" in p for p in problems))


class FramesForTest(unittest.TestCase):

    def test_rounds_to_nearest_frame(self):
        self.assertEqual(motion.frames_for(2.0, 30), 60)

    def test_never_returns_zero(self):
        self.assertEqual(motion.frames_for(0.0, 30), 1)


class BuildSceneFilterTest(unittest.TestCase):

    def test_returns_a_single_output_pad_named_label(self):
        out = motion.build_scene_filter(0, "v0", scene(), TARGET)
        self.assertTrue(out.endswith("[v0]"))
        self.assertTrue(out.startswith("[0:v]"))

    def test_static_and_zoom_in_produce_different_filters(self):
        static_out = motion.build_scene_filter(0, "v0", scene(motion={"kind": "static"}), TARGET)
        zoom_out = motion.build_scene_filter(0, "v0", scene(motion={"kind": "zoom_in"}), TARGET)
        self.assertNotEqual(static_out, zoom_out)

    def test_is_a_pure_function_of_its_inputs(self):
        a = motion.build_scene_filter(2, "vx", scene(), TARGET)
        b = motion.build_scene_filter(2, "vx", scene(), TARGET)
        self.assertEqual(a, b)

    def test_unknown_motion_raises(self):
        with self.assertRaises(motion.MotionError):
            motion.build_scene_filter(0, "v0", scene(motion={"kind": "teleport"}), TARGET)

    def test_negative_amount_raises(self):
        with self.assertRaises(motion.MotionError):
            motion.build_scene_filter(
                0, "v0", scene(motion={"kind": "zoom_in", "amount": -1}), TARGET)


class BuildTransitionChainTest(unittest.TestCase):

    def test_single_scene_has_no_transition_parts(self):
        parts, label, elapsed = motion.build_transition_chain(["v0"], [scene()])
        self.assertEqual(parts, [])
        self.assertEqual(label, "v0")
        self.assertEqual(elapsed, 2.0)

    def test_crossfade_shortens_the_timeline_by_the_overlap(self):
        scenes = [scene(duration_seconds=2.0), scene(duration_seconds=2.0)]
        parts, label, elapsed = motion.build_transition_chain(["v0", "v1"], scenes)
        self.assertEqual(elapsed, 2.0 - 0.5 + 2.0)
        self.assertIn("xfade", parts[0])

    def test_cut_uses_concat_not_xfade(self):
        scenes = [scene(transition={"kind": "cut", "duration_seconds": 0.0}),
                  scene(duration_seconds=2.0)]
        parts, label, elapsed = motion.build_transition_chain(["v0", "v1"], scenes)
        self.assertIn("concat", parts[0])
        self.assertEqual(elapsed, 4.0)

    def test_transition_not_shorter_than_scene_raises(self):
        scenes = [scene(duration_seconds=1.0,
                        transition={"kind": "crossfade", "duration_seconds": 1.0}),
                  scene(duration_seconds=2.0)]
        with self.assertRaises(motion.MotionError):
            motion.build_transition_chain(["v0", "v1"], scenes)


class TimelineSecondsTest(unittest.TestCase):

    def test_empty_scene_list_is_zero(self):
        self.assertEqual(motion.timeline_seconds([]), 0.0)

    def test_accounts_for_crossfade_overlap(self):
        scenes = [scene(duration_seconds=3.0), scene(duration_seconds=3.0)]
        self.assertEqual(motion.timeline_seconds(scenes), 3.0 - 0.5 + 3.0)

    def test_cut_has_no_overlap(self):
        scenes = [scene(duration_seconds=3.0, transition={"kind": "cut", "duration_seconds": 0.0}),
                  scene(duration_seconds=3.0)]
        self.assertEqual(motion.timeline_seconds(scenes), 6.0)


if __name__ == "__main__":
    unittest.main()
