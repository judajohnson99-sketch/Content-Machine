#!/usr/bin/env python3
"""Tests for scripts/motion.py: pure ffmpeg-filter arithmetic.

build_scene_filter and build_transition_chain only assemble filter *text*,
which is exactly what these tests pin. One test (NewMotionsRenderTest) hands
the layered/drift/fog filters to ffmpeg at a tiny size, because those
multi-chain graphs are only proven valid by ffmpeg accepting them.
"""
import re
import shutil
import subprocess
import sys
import tempfile
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


class LayeredAndDriftMotionTest(unittest.TestCase):
    """The sleep-content moves: filter text that is pure of its inputs and
    registered so validation accepts them."""

    TARGET = {"width": 320, "height": 180, "fps": 24}

    def _filter(self, kind, **motion_cfg):
        return motion.build_scene_filter(
            0, "m", scene(duration_seconds=2.0, motion={"kind": kind, **motion_cfg}),
            self.TARGET)

    def test_new_moves_are_registered_and_described(self):
        for kind in ("drift", "parallax", "parallax_in"):
            self.assertIn(kind, motion.MOTIONS)
            self.assertEqual(motion.validate_motion(kind), [])
            self.assertNotEqual(motion.describe(kind), "unknown motion")

    def test_parallax_composites_a_feathered_sharp_layer_over_a_blurred_one(self):
        text = self._filter("parallax")
        self.assertIn("gblur", text)
        self.assertIn("format=yuva420p", text)
        self.assertIn("overlay=0:0", text)
        self.assertEqual(text.count("zoompan="), 2)
        self.assertTrue(text.endswith("[m]"))

    def test_parallax_layers_travel_in_opposite_directions(self):
        text = self._filter("parallax")
        self.assertIn("(0.5+0.30*", text)
        self.assertIn("(0.5-0.80*", text)

    def test_parallax_is_a_pure_function_of_its_inputs(self):
        self.assertEqual(self._filter("parallax_in"), self._filter("parallax_in"))
        self.assertNotEqual(self._filter("parallax_in"), self._filter("parallax"))

    def test_drift_rotates_inside_a_margin_then_crops_to_the_frame(self):
        text = self._filter("drift", direction="ne")
        self.assertIn("rotate=a=", text)
        self.assertIn("s=334x188", text)   # 4% margin, rounded up to even
        self.assertIn("crop=320:180", text)

    def test_drift_direction_defaults_from_the_scene_id_deterministically(self):
        a = motion.build_scene_filter(
            0, "m", scene(scene_id="s07", motion={"kind": "drift"}), self.TARGET)
        b = motion.build_scene_filter(
            0, "m", scene(scene_id="s07", motion={"kind": "drift"}), self.TARGET)
        self.assertEqual(a, b)
        directions = {motion._direction_for({"scene_id": f"s{i:02d}"}, {})
                      for i in range(40)}
        self.assertGreater(len(directions), 1)

    def test_unknown_drift_direction_raises(self):
        with self.assertRaises(motion.MotionError):
            self._filter("drift", direction="up")

    def test_fog_ambient_overlays_a_generated_haze(self):
        plain = self._filter("zoom_in")
        fog = self._filter("zoom_in", ambient="fog")
        self.assertNotIn("alphamerge", plain)
        self.assertIn("alphamerge", fog)
        self.assertIn("geq=lum=", fog)
        self.assertEqual(fog, self._filter("zoom_in", ambient="fog"))

    def test_unknown_ambient_is_reported(self):
        self.assertTrue(motion.validate_motion("drift", ambient="lasers"))
        with self.assertRaises(motion.MotionError):
            self._filter("drift", ambient="lasers")

    def test_every_intermediate_pad_is_namespaced_by_the_output_label(self):
        """Two layered scenes must be able to share one filter graph."""
        cfg = {"kind": "parallax", "ambient": "fog"}
        a = motion.build_scene_filter(0, "v0", scene(motion=cfg), self.TARGET)
        b = motion.build_scene_filter(1, "v1", scene(motion=cfg), self.TARGET)

        def pads(text):
            return set(re.findall(r"\[([A-Za-z_][A-Za-z0-9_]*)\]", text))
        self.assertFalse(pads(a) & pads(b))


class CycleSecondsTest(unittest.TestCase):

    def test_a_cycle_drops_the_closing_dissolve(self):
        scenes = [scene(duration_seconds=10.0), scene(duration_seconds=10.0)]
        self.assertAlmostEqual(motion.timeline_seconds(scenes), 19.5)
        self.assertAlmostEqual(motion.cycle_seconds(scenes), 19.0)

    def test_a_closing_cut_has_no_overlap(self):
        scenes = [scene(duration_seconds=10.0),
                  scene(duration_seconds=10.0, transition={"kind": "cut"})]
        self.assertAlmostEqual(motion.cycle_seconds(scenes), 19.5)


@unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"),
                     "ffmpeg/ffprobe not on PATH")
class NewMotionsRenderTest(unittest.TestCase):
    """ffmpeg itself accepts each new filter and emits exactly the frames
    the scene owns - text that merely looks right is not enough. The one
    test in this file that starts a process."""

    def test_each_new_move_renders_the_exact_frame_count(self):
        with tempfile.TemporaryDirectory() as tmp:
            image = Path(tmp) / "i.png"
            subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i",
                            "testsrc2=s=128x128", "-frames:v", "1", str(image)],
                           check=True)
            target = {"width": 64, "height": 36, "fps": 12}
            for kind, ambient in (("drift", None), ("parallax", None),
                                  ("parallax_in", None), ("zoom_in", "fog"),
                                  ("parallax", "fog")):
                text = motion.build_scene_filter(
                    0, "m", scene(duration_seconds=1.0,
                                  motion={"kind": kind, "ambient": ambient}), target)
                out = Path(tmp) / f"{kind}-{ambient}.nut"
                result = subprocess.run(
                    ["ffmpeg", "-y", "-v", "error", "-i", str(image),
                     "-filter_complex", text, "-map", "[m]", "-c:v", "rawvideo",
                     str(out)], capture_output=True, text=True)
                self.assertEqual(result.returncode, 0, f"{kind}/{ambient}: {result.stderr}")
                frames = subprocess.run(
                    ["ffprobe", "-v", "error", "-count_frames", "-select_streams", "v",
                     "-show_entries", "stream=nb_read_frames", "-of", "csv=p=0",
                     str(out)], capture_output=True, text=True).stdout.strip()
                self.assertEqual(frames, "12", f"{kind}/{ambient}")


if __name__ == "__main__":
    unittest.main()
