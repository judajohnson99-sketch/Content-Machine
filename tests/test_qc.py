#!/usr/bin/env python3
"""Tests for scripts/qc.py: the visual-diversity signal and the per-image
assessment an unattended run retries on."""
import json
import tempfile
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import qc  # noqa: E402


def _make_image(path):
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "color=c=beige:s=16x16",
         "-frames:v", "1", str(path)],
        check=True)


def _scene(scene_id, image_prompt, image):
    return {"scene_id": scene_id, "image": str(image), "image_prompt": image_prompt,
           "generation": {"request_digest": scene_id}}


def _storyboard(scenes, scene_motifs=None):
    board = {
        "scenes": scenes,
        "source_generation": {"width": 16, "height": 16},
        "target": {"width": 1920, "height": 1080, "fps": 30, "duration_seconds": 60.0},
        "timeline_seconds": 60.0,
    }
    if scene_motifs is not None:
        board["scene_motifs"] = scene_motifs
    return board


class VisualDiversityWarningsTest(unittest.TestCase):

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.pdir = Path(self._tmp.name)
        self.image = self.pdir / "scene.png"
        _make_image(self.image)

    def tearDown(self):
        self._tmp.cleanup()

    def _scenes(self, n, prompt_for):
        return [_scene(f"s{i:02d}", prompt_for(i), self.image) for i in range(n)]

    def test_identical_prompts_across_many_scenes_are_flagged(self):
        scenes = self._scenes(6, lambda i: "a beige living room")
        report = qc.qc_storyboard(_storyboard(scenes), self.pdir)
        self.assertTrue(report["visual_diversity_warnings"])
        self.assertIn("identical image prompt", report["visual_diversity_warnings"][0])

    def test_identical_setting_with_only_the_scene_index_suffix_varying_is_flagged(self):
        """storyboard.py::apply_scene_motifs always appends ", scene N,
        <section>" to every prompt, so the real unplanned-reuse bug never
        produces byte-identical image_prompt strings - only an identical
        setting differing solely by that bookkeeping suffix."""
        scenes = self._scenes(
            6, lambda i: f"a beige living room, scene {i + 1}, {'hook' if i == 0 else 'body'}")
        report = qc.qc_storyboard(_storyboard(scenes), self.pdir)
        self.assertTrue(report["visual_diversity_warnings"])
        self.assertIn("identical image prompt", report["visual_diversity_warnings"][0])

    def test_a_handful_of_distinct_prompts_is_not_flagged(self):
        scenes = self._scenes(8, lambda i: f"environment {i % 4}")
        report = qc.qc_storyboard(_storyboard(scenes), self.pdir)
        self.assertEqual(report["visual_diversity_warnings"], [])

    def test_few_scenes_are_never_flagged(self):
        scenes = self._scenes(3, lambda i: "same prompt")
        report = qc.qc_storyboard(_storyboard(scenes), self.pdir)
        self.assertEqual(report["visual_diversity_warnings"], [])

    def test_a_deliberate_single_environment_choice_is_not_flagged(self):
        """scene_motifs recorded means a scene-environment reasoning step
        actually ran and chose this - trusted, not second-guessed, even
        when every scene lands on the identical single environment (a
        legitimate slow-TV/ambient design)."""
        scenes = self._scenes(6, lambda i: "one held establishing shot")
        board = _storyboard(scenes, scene_motifs={s["scene_id"]: "x" for s in scenes})
        report = qc.qc_storyboard(board, self.pdir)
        self.assertEqual(report["visual_diversity_warnings"], [])

    def test_warning_never_fails_the_qc_status(self):
        """visual_diversity_warnings is advisory at the technical-QC level -
        identical repeated visuals must not flip a technically-valid
        storyboard to FAIL, because technical validity (this status) and
        production quality are different questions. The production-quality
        answer lives in scripts/project.py::gate_blockers, which does treat
        this same unplanned-repetition signal as a real blocker on
        READY_FOR_REVIEW (see tests.test_project)."""
        scenes = self._scenes(6, lambda i: "a beige living room")
        report = qc.qc_storyboard(_storyboard(scenes), self.pdir)
        self.assertEqual(report["status"], "PASS")
        self.assertTrue(report["visual_diversity_warnings"])


if __name__ == "__main__":
    unittest.main()


class AssessImageTest(unittest.TestCase):
    """The narrow question an unattended run may act on: is this picture
    obviously broken? Never 'is it good' - that stays a human's claim."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)

    def _render(self, name, source, size="128x128"):
        path = self.tmp / name
        subprocess.run(
            ["ffmpeg", "-y", "-v", "error", "-f", "lavfi",
             "-i", f"{source}=s={size}" if "=" not in source
             else f"{source}:s={size}", "-frames:v", "1", str(path)],
            check=True, capture_output=True)
        return path

    def test_a_flat_fill_is_blocked_as_having_no_structure(self):
        result = qc.assess_image(self._render("flat.png", "color=c=0x606060"))
        self.assertEqual(result["verdict"], "BLOCKED")
        self.assertIn("blank", [f["code"] for f in result["findings"]])

    def test_a_black_frame_is_blocked(self):
        result = qc.assess_image(self._render("black.png", "color=c=black"))
        self.assertEqual(result["verdict"], "BLOCKED")
        codes = [f["code"] for f in result["findings"]]
        self.assertTrue({"black_frame", "blank"} & set(codes))

    def test_a_white_frame_is_blocked(self):
        result = qc.assess_image(self._render("white.png", "color=c=white"))
        self.assertEqual(result["verdict"], "BLOCKED")
        codes = [f["code"] for f in result["findings"]]
        self.assertTrue({"blown_out", "blank"} & set(codes))

    def test_a_picture_with_real_structure_passes(self):
        result = qc.assess_image(self._render("structured.png", "testsrc"))
        self.assertNotEqual(result["verdict"], "BLOCKED")
        self.assertGreater(result["measurement"]["luma_stddev"],
                           qc.MIN_LUMA_STDDEV)

    def test_a_missing_file_is_blocked_rather_than_raising(self):
        result = qc.assess_image(self.tmp / "nope.png")
        self.assertEqual(result["verdict"], "BLOCKED")

    def test_passing_never_claims_the_image_is_good(self):
        result = qc.assess_image(self._render("t.png", "testsrc"))
        self.assertNotIn("production_grade", result)
        self.assertNotIn("good", json.dumps(result))


class ImageHashTest(unittest.TestCase):

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)

    def _render(self, name, source):
        path = self.tmp / name
        subprocess.run(
            ["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", source,
             "-frames:v", "1", str(path)], check=True, capture_output=True)
        return path

    def test_the_same_picture_hashes_to_zero_distance(self):
        a = self._render("a.png", "testsrc=s=128x128")
        b = self._render("b.png", "testsrc=s=128x128")
        self.assertEqual(qc.hash_distance(qc.image_hash(a), qc.image_hash(b)), 0)

    def test_different_pictures_are_far_apart(self):
        a = self._render("a.png", "testsrc=s=128x128")
        b = self._render("b.png", "testsrc2=s=128x128")
        distance = qc.hash_distance(qc.image_hash(a), qc.image_hash(b))
        self.assertGreater(distance, qc.DUPLICATE_HASH_DISTANCE)

    def test_deliberate_reuse_is_not_reported_as_a_duplicate(self):
        """Two scenes sharing a request digest are one picture on purpose -
        the picture-identity rule - and must never be flagged."""
        image = self._render("shared.png", "testsrc=s=128x128")
        scenes = [
            {"scene_id": "s01", "image": image.name,
             "generation": {"request_digest": "same"}},
            {"scene_id": "s02", "image": image.name,
             "generation": {"request_digest": "same"}},
        ]
        self.assertEqual(qc.duplicate_scene_findings(scenes, self.tmp), [])

    def test_different_requests_that_came_back_identical_are_reported(self):
        a = self._render("a.png", "testsrc=s=128x128")
        b = self._render("b.png", "testsrc=s=128x128")
        scenes = [
            {"scene_id": "s01", "image": a.name,
             "generation": {"request_digest": "one"}},
            {"scene_id": "s02", "image": b.name,
             "generation": {"request_digest": "two"}},
        ]
        findings = qc.duplicate_scene_findings(scenes, self.tmp)
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0]["code"], "near_duplicate_scenes")
        self.assertEqual(findings[0]["severity"], "warn")
