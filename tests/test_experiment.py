#!/usr/bin/env python3
"""Tests for the content experiment framework.

Checks the concept batch is well-formed and that ranking is reproducible,
then proves a concept can be scaffolded into a project the existing
pipeline accepts.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import experiment  # noqa: E402
import generation  # noqa: E402
import worker  # noqa: E402

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
        # The ranked information-value batch is still 20 experiment concepts;
        # the curated reference catalogue sits beside it under its own kind.
        kinds = {}
        for c in self.concepts:
            kinds.setdefault(experiment.concept_kind(c), []).append(c["id"])
        self.assertEqual(len(kinds.get("experiment", [])) + len(kinds.get("technical", [])), 20,
                         "batch should hold 20 experiment/technical concepts")
        self.assertEqual(len(kinds.get("reference", [])), 8, "8 curated reference concepts")
        ids = [c["id"] for c in self.concepts]
        self.assertEqual(len(ids), len(set(ids)), "concept ids must be unique")

    def test_reference_catalogue_is_depicted_only_with_a_visual_direction(self):
        for c in self.concepts:
            if experiment.concept_kind(c) != "reference":
                continue
            self.assertFalse(c["procedural_visuals_acceptable"], c["id"])
            direction = c["visual_direction"]
            for key in ("palette", "motifs", "avoid", "prompt_core", "negative"):
                self.assertTrue(direction.get(key), f"{c['id']} lacks visual_direction.{key}")
            self.assertEqual(c["audio_source_requirement"], "synthesisable_now", c["id"])
            self.assertTrue(c.get("preview_seconds"), c["id"])
        technical = [c["id"] for c in self.concepts if experiment.concept_kind(c) == "technical"]
        self.assertIn("sleep-brown-noise-dark", technical)

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


class TestScaffoldProjectFunction(unittest.TestCase):
    """The typed function behind `scaffold`, POST /projects/ and produce."""

    def setUp(self):
        self.video_id = f"pytest-exp-{uuid.uuid4().hex[:8]}"
        self.pdir = ROOT / "projects" / self.video_id
        self.addCleanup(lambda: shutil.rmtree(self.pdir, ignore_errors=True))

    def test_scaffolds_a_project_and_reports_it(self):
        result = experiment.scaffold_project("sleep-brown-noise-dark", self.video_id, duration=12)
        self.assertEqual(result["video_id"], self.video_id)
        self.assertEqual(result["duration_seconds"], 12.0)
        self.assertFalse(result["assets_ready"])
        metadata = json.loads((self.pdir / "metadata.json").read_text())
        self.assertEqual(metadata["experiment"]["concept_id"], "sleep-brown-noise-dark")
        self.assertEqual(metadata["status"]["assets"], "PENDING")
        spec = json.loads((self.pdir / "video_spec.json").read_text())
        self.assertEqual(spec["duration_seconds"], 12.0)

    def test_refuses_an_unknown_concept_and_creates_nothing(self):
        with self.assertRaises(experiment.ScaffoldError) as ctx:
            experiment.scaffold_project("no-such-concept", self.video_id)
        self.assertEqual(ctx.exception.code, "unknown_concept")
        self.assertFalse(self.pdir.exists())

    def test_refuses_to_overwrite_an_existing_project(self):
        experiment.scaffold_project("sleep-brown-noise-dark", self.video_id, duration=5)
        with self.assertRaises(experiment.ScaffoldError) as ctx:
            experiment.scaffold_project("sleep-brown-noise-dark", self.video_id, duration=5)
        self.assertEqual(ctx.exception.code, "exists")

    def test_refuses_an_unsafe_or_malformed_project_id(self):
        for bad in ("../escape", "Has Spaces", "ab", "UPPER", "a/b", ""):
            with self.assertRaises(experiment.ScaffoldError, msg=bad) as ctx:
                experiment.scaffold_project("sleep-brown-noise-dark", bad)
            self.assertEqual(ctx.exception.code, "invalid")

    def test_refuses_a_non_positive_duration(self):
        with self.assertRaises(experiment.ScaffoldError) as ctx:
            experiment.scaffold_project("sleep-brown-noise-dark", self.video_id, duration=0)
        self.assertEqual(ctx.exception.code, "invalid")
        self.assertFalse(self.pdir.exists())


class TestConceptCatalog(unittest.TestCase):
    """The read model behind "start a new production": every concept, its
    declared requirements, and an honest host-readiness verdict derived
    from configuration alone."""

    def setUp(self):
        self._env = {k: os.environ.get(k) for k in ("TEST_MODE", "SEARCH_PROVIDER", "COMFYUI_URL", "SEARCH_KEYLESS")}
        for key in self._env:
            os.environ.pop(key, None)
        # The real worker registry (jobs/workers) belongs to this host; the
        # catalogue's readiness must be judged against an empty one here.
        self.tmp = Path(tempfile.mkdtemp(prefix="cm-catalog-"))
        self._jobs_dir = generation.JOBS_DIR
        generation.JOBS_DIR = self.tmp / "jobs"

    def tearDown(self):
        generation.JOBS_DIR = self._jobs_dir
        shutil.rmtree(self.tmp, ignore_errors=True)
        for key, value in self._env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    def test_depicted_concept_waits_for_an_enrolled_but_offline_gpu_worker(self):
        worker.enroll("home-gpu-01", ("comfyui",))
        catalog = experiment.concept_catalog()
        self.assertEqual(catalog["capabilities"]["depicted_imagery"]["state"], "worker_offline")
        entry = next(c for c in catalog["concepts"] if c["id"] == "ref-moonlit-victorian-glasshouse")
        self.assertEqual(entry["kind"], "reference")
        self.assertEqual(entry["readiness"]["images"], "partial")
        self.assertTrue(entry["readiness"]["waits_for_gpu"])
        self.assertFalse(entry["readiness"]["runnable_now"])
        self.assertTrue(any("queue for the GPU worker" in n for n in entry["readiness"]["notes"]))

    def test_depicted_concept_is_runnable_when_the_gpu_worker_is_ready(self):
        worker.enroll("home-gpu-01", ("comfyui",))
        worker.heartbeat("home-gpu-01", {"comfyui": "reachable at http://127.0.0.1:8188",
                                         "comfyui_reachable": True,
                                         "checkpoints": ["a.safetensors"], "model": "a.safetensors"})
        catalog = experiment.concept_catalog()
        self.assertEqual(catalog["capabilities"]["depicted_imagery"]["state"], "worker_ready")
        self.assertTrue(catalog["capabilities"]["depicted_imagery"]["starts_now"])
        entry = next(c for c in catalog["concepts"] if c["id"] == "ref-moonlit-victorian-glasshouse")
        self.assertEqual(entry["readiness"]["images"], "ok")
        self.assertEqual(entry["readiness"]["images_via"], "gpu-worker")
        self.assertTrue(entry["readiness"]["runnable_now"])
        self.assertEqual(entry["narration"], "none")
        self.assertEqual(entry["visual_direction"]["palette"][0], "deep indigo")

    def test_lists_every_concept_with_its_requirements(self):
        catalog = experiment.concept_catalog()
        _, concepts = experiment.load_concepts()
        self.assertEqual({c["id"] for c in catalog["concepts"]}, {c["id"] for c in concepts})
        entry = next(c for c in catalog["concepts"] if c["id"] == "sleep-brown-noise-dark")
        self.assertEqual(entry["audio_requirement"], "synthesisable_now")
        self.assertTrue(entry["procedural_visuals_acceptable"])
        self.assertIn("readiness", entry)

    def test_procedural_concept_is_runnable_on_a_bare_host(self):
        catalog = experiment.concept_catalog()
        self.assertEqual(catalog["capabilities"]["depicted_image_providers"], [])
        entry = next(c for c in catalog["concepts"] if c["id"] == "sleep-brown-noise-dark")
        self.assertTrue(entry["readiness"]["runnable_now"])
        self.assertEqual(entry["readiness"]["notes"], [])

    def test_depicted_concept_is_blocked_without_an_image_provider(self):
        catalog = experiment.concept_catalog()
        entry = next(c for c in catalog["concepts"] if c["id"] == "meditation-guided-short")
        self.assertEqual(entry["readiness"]["images"], "blocked")
        self.assertFalse(entry["readiness"]["runnable_now"])
        self.assertTrue(any("depicted" in n for n in entry["readiness"]["notes"]))

    def test_research_required_concept_is_blocked_without_a_search_provider(self):
        os.environ["SEARCH_KEYLESS"] = "0"   # no keys and keyless fallbacks off
        catalog = experiment.concept_catalog()
        self.assertFalse(catalog["capabilities"]["search_available"])
        entry = next(c for c in catalog["concepts"] if c["id"] == "story-sleepy-history-adult")
        self.assertEqual(entry["readiness"]["research"], "blocked")
        self.assertTrue(any("SEARCH_PROVIDER" in n for n in entry["readiness"]["notes"]))

    def test_a_bare_host_researches_through_the_keyless_fallbacks(self):
        catalog = experiment.concept_catalog()
        self.assertTrue(catalog["capabilities"]["search_available"])
        self.assertEqual(catalog["capabilities"]["search_provider"], "duckduckgo,wikipedia")
        entry = next(c for c in catalog["concepts"] if c["id"] == "story-sleepy-history-adult")
        self.assertEqual(entry["readiness"]["research"], "ok")

    def test_research_readiness_follows_the_configured_provider(self):
        os.environ["TEST_MODE"] = "1"   # selects the fixture provider
        catalog = experiment.concept_catalog()
        self.assertTrue(catalog["capabilities"]["search_available"])
        entry = next(c for c in catalog["concepts"] if c["id"] == "story-sleepy-history-adult")
        self.assertEqual(entry["readiness"]["research"], "ok")
        os.environ.pop("TEST_MODE")
        os.environ["SEARCH_PROVIDER"] = "not-a-real-vendor"
        os.environ["SEARCH_KEYLESS"] = "0"
        catalog = experiment.concept_catalog()
        self.assertEqual(catalog["capabilities"]["search_provider"], "not-a-real-vendor")
        self.assertFalse(catalog["capabilities"]["search_available"])
