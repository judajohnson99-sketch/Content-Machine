#!/usr/bin/env python3
"""Tests for the content experiment framework.

Checks the concept batch is well-formed and that ranking is reproducible,
then proves a concept can be scaffolded into a project the existing
pipeline accepts.
"""
import json
import shutil
import subprocess
import sys
import unittest
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import experiment  # noqa: E402

CLI = ROOT / "content-machine"
EXPERIMENT_CLI = ROOT / "scripts" / "experiment.py"
IMAGES = ROOT / "tests" / "fixtures" / "images"
AUDIO = ROOT / "tests" / "fixtures" / "audio" / "test_tone.wav"

REQUIRED_NICHES = {
    "adult_sleep", "kids_sleep", "bedtime_stories",
    "focus_ambience", "non_sleep_alternative",
}


class TestConceptBatch(unittest.TestCase):

    def setUp(self):
        self.data, self.concepts = experiment.load_concepts()

    def test_batch_is_valid(self):
        proc = subprocess.run(
            [sys.executable, str(EXPERIMENT_CLI), "validate"],
            capture_output=True, text=True, cwd=str(ROOT))
        self.assertEqual(proc.returncode, 0, f"concept validation failed:\n{proc.stderr}")

    def test_batch_has_twenty_differentiated_concepts(self):
        self.assertEqual(len(self.concepts), 20, "batch should hold 20 concepts")
        ids = [c["id"] for c in self.concepts]
        self.assertEqual(len(ids), len(set(ids)), "concept ids must be unique")

    def test_required_niches_are_covered(self):
        present = {c["niche"] for c in self.concepts}
        missing = REQUIRED_NICHES - present
        self.assertFalse(missing, f"missing required niches: {missing}")

    def test_every_concept_declares_continue_and_kill_evidence(self):
        """A hypothesis without a kill condition is not an experiment."""
        for c in self.concepts:
            self.assertTrue(c["continue_evidence"], f"{c['id']}: no continue_evidence")
            self.assertTrue(c["kill_evidence"], f"{c['id']}: no kill_evidence")

    def test_monetization_claims_are_marked_unverified(self):
        """Guard against a claim hardening into an assumed fact over time."""
        for c in self.concepts:
            self.assertIn(
                "UNVERIFIED", c["monetization_hypothesis"],
                f"{c['id']}: monetization hypothesis must be marked UNVERIFIED",
            )

    def test_every_concept_names_what_must_be_researched(self):
        for c in self.concepts:
            self.assertTrue(c["assumptions_requiring_research"],
                            f"{c['id']}: must name its open questions")


class TestRanking(unittest.TestCase):

    def setUp(self):
        _, self.concepts = experiment.load_concepts()

    def test_ranking_is_deterministic(self):
        first = [c["id"] for c in experiment.ranked(self.concepts)]
        second = [c["id"] for c in experiment.ranked(self.concepts)]
        self.assertEqual(first, second, "ranking must be reproducible")

    def test_scores_are_normalised_and_ordered(self):
        rows = experiment.ranked(self.concepts)
        self.assertAlmostEqual(rows[0]["eiv"], 100.0, delta=0.1,
                               msg="top concept should normalise to 100")
        values = [r["eiv"] for r in rows]
        self.assertEqual(values, sorted(values, reverse=True), "rows must be descending")

    def test_cheaper_test_outranks_costlier_identical_one(self):
        """EIV must reward learning per unit of cost, not raw appeal."""
        cheap = {"scores": {"uncertainty_reduction": 4, "generalizability": 4,
                            "repeatability": 4, "monetization_clarity": 4, "policy_risk": 1},
                 "production_complexity": 1, "generation_dependence": 1}
        costly = dict(cheap, production_complexity=5, generation_dependence=5)
        self.assertGreater(experiment.score(cheap), experiment.score(costly))

    def test_policy_risk_discounts_but_does_not_zero(self):
        base = {"scores": {"uncertainty_reduction": 4, "generalizability": 4,
                           "repeatability": 4, "monetization_clarity": 4, "policy_risk": 1},
                "production_complexity": 2, "generation_dependence": 2}
        risky = dict(base, scores=dict(base["scores"], policy_risk=5))
        self.assertLess(experiment.score(risky), experiment.score(base))
        self.assertGreater(experiment.score(risky), 0,
                           "a risky-but-cheap test should still be rankable")


class TestScaffold(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        for tool in ("ffmpeg", "ffprobe"):
            if not shutil.which(tool):
                raise unittest.SkipTest(f"{tool} not found on PATH")

    def setUp(self):
        self.video_id = f"pytest-exp-{uuid.uuid4().hex[:8]}"
        self.pdir = ROOT / "projects" / self.video_id
        self.addCleanup(lambda: shutil.rmtree(self.pdir, ignore_errors=True))

    def scaffold(self, concept_id, *extra):
        return subprocess.run(
            [sys.executable, str(EXPERIMENT_CLI), "scaffold", concept_id, self.video_id, *extra],
            capture_output=True, text=True, cwd=str(ROOT))

    def test_scaffold_with_assets_produces_a_validatable_project(self):
        proc = self.scaffold("sleep-brown-noise-dark",
                             "--images", str(IMAGES), "--audio", str(AUDIO), "--duration", "5")
        self.assertEqual(proc.returncode, 0, proc.stderr)

        validate = subprocess.run([str(CLI), "validate", self.video_id],
                                  capture_output=True, text=True, cwd=str(ROOT))
        self.assertEqual(validate.returncode, 0,
                         f"scaffolded project did not validate:\n{validate.stderr}")

    def test_scaffold_carries_the_hypothesis_into_the_project(self):
        """The experiment record must survive into the project, or results are unattributable."""
        self.scaffold("story-sleepy-history-adult", "--duration", "5")
        meta = json.loads((self.pdir / "metadata.json").read_text())
        self.assertEqual(meta["experiment"]["concept_id"], "story-sleepy-history-adult")
        self.assertEqual(meta["experiment"]["batch_id"], "batch-001")
        self.assertTrue(meta["experiment"]["kill_evidence"])
        self.assertTrue(meta["experiment"]["continue_evidence"])
        self.assertIn("UNVERIFIED", meta["hypothesis"])

    def test_scaffold_without_assets_marks_them_pending(self):
        proc = self.scaffold("sleep-brown-noise-dark", "--duration", "5")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        meta = json.loads((self.pdir / "metadata.json").read_text())
        self.assertEqual(meta["status"]["assets"], "PENDING")
        # Its audio requirement should be recorded so the blocker is legible.
        self.assertIn("capability", meta["audio_plan"])

        validate = subprocess.run([str(CLI), "validate", self.video_id],
                                  capture_output=True, text=True, cwd=str(ROOT))
        self.assertNotEqual(validate.returncode, 0,
                            "a project with no assets must not validate")

    def test_scaffolded_spec_is_renderable_by_the_existing_renderer(self):
        """The template must satisfy render.py's own spec validation."""
        self.scaffold("sleep-brown-noise-dark",
                      "--images", str(IMAGES), "--audio", str(AUDIO), "--duration", "5")
        sys.path.insert(0, str(ROOT / "scripts"))
        import render
        raw = json.loads((self.pdir / "video_spec.json").read_text())
        spec = render.validate_and_normalize(raw, base_dir=self.pdir)
        self.assertEqual(spec["duration_seconds"], 5.0)
        self.assertGreater(len(spec["image_paths"]), 0)

    def test_unknown_concept_is_rejected(self):
        proc = self.scaffold("no-such-concept")
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("No such concept", proc.stderr)


if __name__ == "__main__":
    unittest.main(verbosity=2)
