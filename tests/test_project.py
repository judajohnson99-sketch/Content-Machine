#!/usr/bin/env python3
"""End-to-end tests for the project/asset layer and orchestration.

Drives ./content-machine (init / validate / run) against the checked-in
fixtures inside a temporary projects/ entry, then verifies the rendered
MP4, QC report, and publication package. Standard library only.
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
CLI = ROOT / "content-machine"
IMAGES = ROOT / "tests" / "fixtures" / "images"
AUDIO = ROOT / "tests" / "fixtures" / "audio" / "test_tone.wav"

sys.path.insert(0, str(ROOT / "scripts"))
import project  # noqa: E402 - direct import for the typed domain functions

# Exit codes from scripts/project.py run.
EXIT_OK = 0
EXIT_ERROR = 1
EXIT_NEEDS_ATTENTION = 2


class ProjectTestCase(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        for tool in ("ffmpeg", "ffprobe"):
            if not shutil.which(tool):
                raise unittest.SkipTest(f"{tool} not found on PATH")
        if not CLI.is_file():
            raise unittest.SkipTest(f"CLI not found: {CLI}")
        if not IMAGES.is_dir() or not AUDIO.is_file():
            raise unittest.SkipTest("fixtures missing; run scripts/make_test_fixtures.py")

    def setUp(self):
        # Unique id per test so projects/ never collides between runs.
        self.video_id = f"pytest-{uuid.uuid4().hex[:8]}"
        self.pdir = ROOT / "projects" / self.video_id
        self.addCleanup(lambda: shutil.rmtree(self.pdir, ignore_errors=True))

    def cm(self, *args):
        return subprocess.run(
            [str(CLI), *args], capture_output=True, text=True, cwd=str(ROOT))

    def init_project(self, *extra, images=None, audio=None):
        proc = self.cm(
            "init", self.video_id,
            "--images", str(images or IMAGES),
            "--audio", str(audio or AUDIO),
            "--width", "480", "--height", "270", "--fps", "24",
            "--duration", "5", "--seconds-per-image", "2",
            *extra,
        )
        return proc

    def metadata(self):
        return json.loads((self.pdir / "metadata.json").read_text())

    def write_metadata(self, patch):
        meta = self.metadata()
        meta.update(patch)
        (self.pdir / "metadata.json").write_text(json.dumps(meta, indent=2) + "\n")


class TestProjectInit(ProjectTestCase):

    def test_creates_full_project_structure(self):
        proc = self.init_project()
        self.assertEqual(proc.returncode, 0, f"init failed:\n{proc.stderr}")
        for sub in ("images", "audio", "thumbnail", "output", "logs"):
            self.assertTrue((self.pdir / sub).is_dir(), f"missing {sub}/ directory")
        self.assertTrue((self.pdir / "video_spec.json").is_file())
        self.assertTrue((self.pdir / "metadata.json").is_file())

    def test_ingests_assets_without_duplicating_bytes(self):
        """Assets should be hardlinked, not copied, when on one filesystem."""
        self.init_project()
        ingested = self.pdir / "images" / sorted(p.name for p in IMAGES.iterdir())[0]
        self.assertTrue(ingested.exists(), "image was not ingested")
        self.assertGreater(
            os.stat(ingested).st_nlink, 1,
            "expected a hardlink (nlink > 1); asset bytes were duplicated",
        )

    def test_copy_flag_forces_real_copy(self):
        self.init_project("--copy")
        ingested = self.pdir / "images" / sorted(p.name for p in IMAGES.iterdir())[0]
        self.assertEqual(os.stat(ingested).st_nlink, 1, "--copy should not hardlink")

    def test_duration_defaults_to_audio_length(self):
        proc = self.cm(
            "init", self.video_id, "--images", str(IMAGES), "--audio", str(AUDIO),
            "--width", "480", "--height", "270", "--fps", "24",
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        spec = json.loads((self.pdir / "video_spec.json").read_text())
        self.assertAlmostEqual(spec["duration_seconds"], 12.0, delta=0.2,
                               msg="duration should default to the audio track length")

    def test_rejects_missing_image_directory(self):
        proc = self.init_project(images=ROOT / "does-not-exist")
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("--images is not a directory", proc.stderr)

    def test_rejects_unsupported_audio_format(self):
        bogus = self.pdir.parent / f"{self.video_id}.xyz"
        bogus.parent.mkdir(parents=True, exist_ok=True)
        bogus.write_bytes(b"not audio")
        self.addCleanup(bogus.unlink, missing_ok=True)
        proc = self.init_project(audio=bogus)
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("unsupported audio format", proc.stderr)


class TestProjectValidate(ProjectTestCase):

    def test_validates_a_healthy_project(self):
        self.init_project()
        proc = self.cm("validate", self.video_id)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("Validation passed", proc.stderr)

    def test_reports_missing_project(self):
        proc = self.cm("validate", "no-such-project-xyz")
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("project directory not found", proc.stderr)

    def test_detects_corrupt_image(self):
        self.init_project()
        (self.pdir / "images" / "corrupt.png").write_bytes(b"\x89PNG not really an image")
        proc = self.cm("validate", self.video_id)
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("unreadable/corrupt image", proc.stderr)

    def test_detects_image_too_dark_for_qc(self):
        """Reject black-ish frames at validation, not after a long render."""
        self.init_project()
        subprocess.run([
            "ffmpeg", "-y", "-v", "error", "-f", "lavfi",
            "-i", "color=c=0x090909:s=320x180:d=1", "-frames:v", "1",
            str(self.pdir / "images" / "too_dark.png")], check=True, capture_output=True)
        proc = self.cm("validate", self.video_id)
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("too dark", proc.stderr)
        self.assertIn("too_dark.png", proc.stderr)

    def test_detects_deleted_audio(self):
        self.init_project()
        for f in (self.pdir / "audio").iterdir():
            f.unlink()
        proc = self.cm("validate", self.video_id)
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("audio.file", proc.stderr)


class TestProjectRun(ProjectTestCase):

    def test_full_pipeline_reaches_ready_for_review(self):
        """project assets -> validate -> render -> QC -> package."""
        self.init_project("--title", "Test Video", "--production-grade-visuals")
        self.write_metadata({"description": "A description, required before review."})

        proc = self.cm("run", self.video_id)
        self.assertEqual(
            proc.returncode, EXIT_OK,
            f"pipeline did not reach READY_FOR_REVIEW.\nstderr:\n{proc.stderr}",
        )

        mp4 = self.pdir / "output" / f"{self.video_id}.mp4"
        self.assertTrue(mp4.is_file(), "no MP4 produced")

        package = json.loads((self.pdir / "output" / "publication_package.json").read_text())
        self.assertEqual(package["status"], "READY_FOR_REVIEW")
        self.assertEqual(package["blocking_issues"], [])
        self.assertEqual(package["qc"]["status"], "PASS")
        self.assertFalse(package["publish"]["published"],
                         "publishing must stay behind the human gate")
        self.assertFalse(package["publish"]["approved_by_human"])

        # The package must describe the file that actually exists.
        probe = subprocess.run(
            ["ffprobe", "-v", "error", "-print_format", "json",
             "-show_format", "-show_streams", str(mp4)],
            capture_output=True, text=True)
        self.assertEqual(probe.returncode, 0, "ffprobe could not read the output")
        probed = json.loads(probe.stdout)
        video = next(s for s in probed["streams"] if s["codec_type"] == "video")
        self.assertEqual(video["width"], package["video"]["width"])
        self.assertEqual(video["height"], package["video"]["height"])
        self.assertEqual(mp4.stat().st_size, package["video"]["size_bytes"])

        qc_report = json.loads((self.pdir / "output" / "qc_report.json").read_text())
        self.assertEqual(qc_report["status"], "PASS")
        self.assertEqual(qc_report["checks_failed"], 0)

        self.assertTrue(any((self.pdir / "logs").iterdir()), "no run log written")
        thumbs = list((self.pdir / "thumbnail").glob("candidate_*.jpg"))
        self.assertTrue(thumbs, "no thumbnail candidates extracted")

        self.assertEqual(self.metadata()["status"]["overall"], "READY_FOR_REVIEW")

    def test_withholds_review_status_when_metadata_incomplete(self):
        """QC can pass while the package is still not publishable."""
        self.init_project("--title", "Has Title But No Description")
        proc = self.cm("run", self.video_id)

        self.assertEqual(proc.returncode, EXIT_NEEDS_ATTENTION,
                         "missing description should yield NEEDS_ATTENTION (exit 2)")
        package = json.loads((self.pdir / "output" / "publication_package.json").read_text())
        self.assertEqual(package["status"], "NEEDS_ATTENTION")
        self.assertEqual(package["qc"]["status"], "PASS",
                         "QC itself should still pass; only metadata is missing")
        self.assertTrue(any("description" in issue for issue in package["blocking_issues"]))

    def test_placeholder_visuals_block_ready_for_review(self):
        """Placeholder imagery must never pass as publishable."""
        self.init_project("--title", "Placeholder Visuals")
        self.write_metadata({
            "description": "Description present so only the visual grade can block.",
            "provenance": {
                "images": {"provider": "procedural/ffmpeg", "model": None,
                           "production_grade": False,
                           "notes": "stand-in plates pending real imagery"},
                "audio": {"provider": "local", "model": None, "notes": "test"},
                "text": {"provider": None, "model": None},
            },
        })
        proc = self.cm("run", self.video_id)
        self.assertEqual(proc.returncode, EXIT_NEEDS_ATTENTION,
                         "placeholder visuals should yield NEEDS_ATTENTION")
        package = json.loads((self.pdir / "output" / "publication_package.json").read_text())
        self.assertEqual(package["status"], "NEEDS_ATTENTION")
        self.assertTrue(any("not production-grade" in issue
                            for issue in package["blocking_issues"]))
        self.assertFalse(package["generation"]["image_production_grade"])

    def test_review_checklist_travels_with_the_package(self):
        """READY_FOR_REVIEW means assembled for checking, so review items must ride along."""
        self.init_project("--title", "Checklist Test", "--production-grade-visuals")
        self.write_metadata({
            "description": "A description.",
            "review_checklist": ["FACT-CHECK the narration before publishing."],
        })
        proc = self.cm("run", self.video_id)
        self.assertEqual(proc.returncode, EXIT_OK, proc.stderr)
        package = json.loads((self.pdir / "output" / "publication_package.json").read_text())
        self.assertEqual(package["status"], "READY_FOR_REVIEW")
        self.assertIn("FACT-CHECK the narration before publishing.",
                      package["review_checklist"])


    def test_undeclared_visual_grade_blocks_review(self):
        """An unmade production-grade claim is not a passing claim.

        The gate previously tested `production_grade is False`, so a project
        that never declared it at all sailed through to READY_FOR_REVIEW.
        """
        self.init_project("--title", "Undeclared Visuals")
        self.write_metadata({"description": "A description."})
        proc = self.cm("run", self.video_id)

        self.assertEqual(proc.returncode, EXIT_NEEDS_ATTENTION,
                         "undeclared visual grade must block review")
        package = json.loads((self.pdir / "output" / "publication_package.json").read_text())
        self.assertTrue(
            any("production_grade is not set" in i for i in package["blocking_issues"]),
            f"expected an undeclared-grade blocker, got {package['blocking_issues']}")

    def test_init_records_the_visual_grade_claim_explicitly(self):
        """The field must exist even when undeclared, so its absence is visible."""
        self.init_project()
        images = self.metadata()["provenance"]["images"]
        self.assertIn("production_grade", images,
                      "production_grade must be written explicitly, not omitted")
        self.assertIsNone(images["production_grade"])

        self.cm("init", self.video_id, "--images", str(IMAGES), "--audio", str(AUDIO),
                "--duration", "5", "--force", "--production-grade-visuals")
        self.assertTrue(self.metadata()["provenance"]["images"]["production_grade"])

    def test_status_reports_a_stale_recorded_verdict(self):
        """Editing metadata after a run must not leave a false READY_FOR_REVIEW.

        This is the real defect found in exp-c-narrated-sleep: the verdict
        outlived the inputs it was computed from.
        """
        self.init_project("--title", "Stale Test", "--production-grade-visuals")
        self.write_metadata({"description": "A description."})
        self.assertEqual(self.cm("run", self.video_id).returncode, EXIT_OK)

        status = self.cm("status", self.video_id)
        self.assertEqual(status.returncode, EXIT_OK)
        self.assertIn("READY_FOR_REVIEW", status.stdout)
        self.assertIn("digest:   matches", status.stdout)

        # Now retract the visual claim without re-running, exactly as happened
        # in the real project.
        meta = self.metadata()
        meta["provenance"]["images"]["production_grade"] = False
        meta["provenance"]["images"]["notes"] = "placeholders after all"
        (self.pdir / "metadata.json").write_text(json.dumps(meta, indent=2) + "\n")

        status = self.cm("status", self.video_id)
        self.assertEqual(status.returncode, EXIT_NEEDS_ATTENTION,
                         "a retracted visual claim must not still read as ready")
        self.assertIn("STALE", status.stdout)
        self.assertIn("CHANGED", status.stdout)
        self.assertTrue(any("not production-grade" in l for l in status.stdout.splitlines()))

    def test_procedural_plate_cannot_be_relabelled_as_production_grade(self):
        """Artefact evidence overrules the metadata claim.

        A stamped procedural plate must block a concept that demands depicted
        imagery, no matter what provenance.images says.
        """
        plates = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, plates, True)
        gen = subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "make_visuals.py"),
             "--style", "dust-archive", "--out", str(plates), "--count", "2"],
            capture_output=True, text=True)
        self.assertEqual(gen.returncode, 0, gen.stderr)

        self.init_project("--title", "Masquerade", "--production-grade-visuals",
                          images=plates)
        self.write_metadata({
            "description": "A description.",
            # Claim a non-procedural provider AND production grade: the old
            # drift check string-matched the provider name, so this evaded it.
            "provenance": {
                "images": {"provider": "local", "model": None,
                           "production_grade": True, "notes": "real photos, honest"},
                "audio": {"provider": "local", "model": None},
                "text": {"provider": None, "model": None},
            },
            "experiment": {"generation_cost_usd": 0.0, "generation_seconds": None,
                           "variables": {},
                           "concept_id": "story-sleepy-history-adult"},
        })
        proc = self.cm("run", self.video_id)
        self.assertEqual(proc.returncode, EXIT_NEEDS_ATTENTION,
                         "a stamped procedural plate must block a depicted-imagery concept")
        package = json.loads((self.pdir / "output" / "publication_package.json").read_text())
        self.assertTrue(
            any("not depicted imagery" in i for i in package["blocking_issues"]),
            f"expected an artefact-evidence blocker, got {package['blocking_issues']}")

    def test_run_fails_clearly_on_invalid_project(self):
        self.init_project()
        (self.pdir / "video_spec.json").write_text('{"width": 480}')
        proc = self.cm("run", self.video_id)
        self.assertEqual(proc.returncode, EXIT_ERROR)
        self.assertIn("validation failed", proc.stderr.lower())
        self.assertFalse((self.pdir / "output" / f"{self.video_id}.mp4").exists(),
                         "no MP4 should be produced for an invalid project")

    def test_concept_drift_blocks_procedural_masquerade(self):
        """Regression: the exp-c pattern.

        A project scaffolded from a concept whose visuals require depicted
        imagery (procedural_visuals_acceptable=false) must not reach
        READY_FOR_REVIEW while its image provider is procedural and its
        production_grade is set to true, even if visual_style/thumbnail_concept
        in metadata have been edited to disclaim the concept.
        """
        self.init_project("--title", "Drift Guard")
        self.write_metadata({
            "description": "Description is present so only the drift check can block.",
            "visual_style": "Abstract plates - deliberately not archival photography.",
            "thumbnail_concept": "Abstract plates - deliberately not archival photography.",
            "provenance": {
                "images": {
                    "provider": "procedural/ffmpeg",
                    "model": None,
                    "production_grade": True,  # the masquerade
                    "notes": "abstract plates presented as production-grade",
                },
                "audio": {"provider": "local", "model": None, "notes": "test"},
                "text": {"provider": None, "model": None},
            },
            "experiment": {
                # Real concept id whose procedural_visuals_acceptable is false.
                "concept_id": "story-sleepy-history-adult",
                "generation_cost_usd": 0.0,
                "generation_seconds": None,
                "variables": {},
            },
        })
        proc = self.cm("run", self.video_id)
        self.assertEqual(proc.returncode, EXIT_NEEDS_ATTENTION,
                         "drift check should block READY_FOR_REVIEW")
        package = json.loads((self.pdir / "output" / "publication_package.json").read_text())
        self.assertEqual(package["status"], "NEEDS_ATTENTION")
        self.assertTrue(
            any("procedural_visuals_acceptable" in issue
                for issue in package["blocking_issues"]),
            f"expected the concept drift blocker; got: {package['blocking_issues']}",
        )

    def test_concept_allowing_procedural_visuals_reaches_review(self):
        """Complement to the drift check: when the concept explicitly allows
        procedural plates, production_grade=true is legitimate and must not
        be blocked."""
        self.init_project("--title", "Procedural Legit")
        self.write_metadata({
            "description": "A description.",
            "provenance": {
                "images": {
                    "provider": "procedural/ffmpeg",
                    "model": None,
                    "production_grade": True,
                    "notes": "a dark plate is the intended deliverable here",
                },
                "audio": {"provider": "local", "model": None, "notes": "test"},
                "text": {"provider": None, "model": None},
            },
            "experiment": {
                # sleep-brown-noise-dark has procedural_visuals_acceptable=true.
                "concept_id": "sleep-brown-noise-dark",
                "generation_cost_usd": 0.0,
                "generation_seconds": None,
                "variables": {},
            },
        })
        proc = self.cm("run", self.video_id)
        self.assertEqual(proc.returncode, EXIT_OK,
                         f"legitimate procedural deliverable should pass:\n{proc.stderr}")
        package = json.loads((self.pdir / "output" / "publication_package.json").read_text())
        self.assertEqual(package["status"], "READY_FOR_REVIEW")

    def test_concept_id_missing_from_concepts_json_is_blocking(self):
        """A concept_id that no longer exists in concepts.json must fail loudly
        rather than silently degrade to no-concept behaviour."""
        self.init_project("--title", "Stale Concept Ref")
        self.write_metadata({
            "description": "A description.",
            "experiment": {
                "concept_id": "no-such-concept-anywhere-xyz",
                "generation_cost_usd": 0.0,
                "generation_seconds": None,
                "variables": {},
            },
        })
        proc = self.cm("run", self.video_id)
        self.assertEqual(proc.returncode, EXIT_NEEDS_ATTENTION)
        package = json.loads((self.pdir / "output" / "publication_package.json").read_text())
        self.assertTrue(
            any("no-such-concept-anywhere-xyz" in issue
                for issue in package["blocking_issues"]),
            f"expected the missing-concept blocker; got: {package['blocking_issues']}",
        )

    def test_rerun_is_idempotent(self):
        """Re-running must overwrite deterministically, not corrupt the project."""
        self.init_project("--title", "Rerun Test", "--production-grade-visuals")
        self.write_metadata({"description": "Rerun determinism check."})
        first = self.cm("run", self.video_id)
        self.assertEqual(first.returncode, EXIT_OK, first.stderr)
        mp4 = self.pdir / "output" / f"{self.video_id}.mp4"
        digest_before = mp4.read_bytes()

        second = self.cm("run", self.video_id)
        self.assertEqual(second.returncode, EXIT_OK, second.stderr)
        self.assertEqual(digest_before, mp4.read_bytes(),
                         "re-running the same project produced a different video")


class ProduceTestCase(ProjectTestCase):
    """TEST_MODE=1 for every subprocess: the creative LLM call must never
    reach the network in this suite, exactly like generate.py's own tests."""

    def cm(self, *args):
        env = dict(os.environ, TEST_MODE="1")
        return subprocess.run(
            [str(CLI), *args], capture_output=True, text=True, cwd=str(ROOT), env=env)


class TestCreativeCommand(ProduceTestCase):

    def test_fills_in_title_description_visual_and_audio_plan(self):
        self.init_project()
        self.write_metadata({
            "experiment": {"concept_id": "sleep-brown-noise-dark",
                           "generation_cost_usd": 0.0, "generation_seconds": None,
                           "variables": {}},
        })
        proc = self.cm("creative", self.video_id)
        self.assertEqual(proc.returncode, EXIT_OK, proc.stderr)

        meta = self.metadata()
        self.assertNotEqual(meta["selected_title"], "")
        self.assertTrue(meta["selected_title"].startswith("[MOCK]"))
        self.assertTrue(meta["description"])
        self.assertEqual(meta["visual_plan"]["style"], "deep-night")
        self.assertTrue(meta["visual_plan"]["prompt"])
        layers = meta["audio_plan"]["composition"]["layers"]
        self.assertEqual(layers[0]["provider"], "noise")
        self.assertEqual(layers[0]["params"]["color"], "brown")

    def test_without_force_does_not_overwrite_an_existing_title(self):
        self.init_project("--title", "Hand-picked Title")
        self.write_metadata({
            "experiment": {"concept_id": "sleep-brown-noise-dark",
                           "generation_cost_usd": 0.0, "generation_seconds": None,
                           "variables": {}},
        })
        proc = self.cm("creative", self.video_id)
        self.assertEqual(proc.returncode, EXIT_OK, proc.stderr)
        self.assertEqual(self.metadata()["selected_title"], "Hand-picked Title")

    def test_force_overwrites_an_existing_title(self):
        self.init_project("--title", "Hand-picked Title")
        self.write_metadata({
            "experiment": {"concept_id": "sleep-brown-noise-dark",
                           "generation_cost_usd": 0.0, "generation_seconds": None,
                           "variables": {}},
        })
        proc = self.cm("creative", self.video_id, "--force")
        self.assertEqual(proc.returncode, EXIT_OK, proc.stderr)
        self.assertNotEqual(self.metadata()["selected_title"], "Hand-picked Title")

    def test_never_sets_production_grade(self):
        """The creative step must not touch the one claim only a human may
        make - regardless of what the concept allows."""
        self.init_project()
        self.write_metadata({
            "experiment": {"concept_id": "sleep-brown-noise-dark",
                           "generation_cost_usd": 0.0, "generation_seconds": None,
                           "variables": {}},
        })
        proc = self.cm("creative", self.video_id)
        self.assertEqual(proc.returncode, EXIT_OK, proc.stderr)
        self.assertIsNone(
            self.metadata()["provenance"]["images"].get("production_grade"))

    def test_refuses_without_a_linked_concept(self):
        self.init_project()
        proc = self.cm("creative", self.video_id)
        self.assertEqual(proc.returncode, EXIT_ERROR)

    def test_tts_required_concept_without_narration_warns_and_leaves_composition_unset(self):
        self.init_project()
        self.write_metadata({
            "experiment": {"concept_id": "explainer-idea-summary-narrated",
                           "generation_cost_usd": 0.0, "generation_seconds": None,
                           "variables": {}},
        })
        proc = self.cm("creative", self.video_id)
        self.assertEqual(proc.returncode, EXIT_OK, proc.stderr)
        meta = self.metadata()
        self.assertTrue(meta["script"])  # the mock narration was written...
        # ...and consumed into a real tts layer, since TEST_MODE's mock
        # narration is non-empty for a tts_required concept.
        self.assertEqual(meta["audio_plan"]["composition"]["layers"][0]["provider"], "tts")


class TestProduceCommand(ProduceTestCase):

    def test_end_to_end_from_a_fresh_concept_reaches_needs_attention(self):
        """No images, no audio file supplied by hand: concept -> MP4."""
        video_id = f"pytest-produce-{self.video_id}"
        self.addCleanup(lambda: shutil.rmtree(ROOT / "projects" / video_id, ignore_errors=True))

        proc = self.cm("produce", video_id, "--concept-id", "sleep-brown-noise-dark",
                       "--duration", "3")
        self.assertEqual(proc.returncode, EXIT_NEEDS_ATTENTION, proc.stderr + proc.stdout)

        pdir = ROOT / "projects" / video_id
        mp4 = pdir / "output" / f"{video_id}.mp4"
        self.assertTrue(mp4.is_file())
        self.assertGreater(mp4.stat().st_size, 1024)

        report = json.loads((pdir / "output" / "qc_report.json").read_text())
        self.assertEqual(report["status"], "PASS")

        package = json.loads((pdir / "output" / "publication_package.json").read_text())
        self.assertEqual(package["status"], "NEEDS_ATTENTION")
        self.assertTrue(
            any("production-grade" in issue for issue in package["blocking_issues"]),
            f"expected only the human production-grade claim to block; got {package['blocking_issues']}",
        )

    def test_explicit_production_grade_flag_reaches_ready_for_review(self):
        """The one human decision point still works when produce is the
        caller - proof the gate was reused, not weakened, by orchestration."""
        video_id = f"pytest-produce-{self.video_id}"
        self.addCleanup(lambda: shutil.rmtree(ROOT / "projects" / video_id, ignore_errors=True))

        proc = self.cm("produce", video_id, "--concept-id", "sleep-brown-noise-dark",
                       "--duration", "3", "--production-grade-visuals")
        self.assertEqual(proc.returncode, EXIT_OK, proc.stderr + proc.stdout)
        package = json.loads(
            (ROOT / "projects" / video_id / "output" / "publication_package.json").read_text())
        self.assertEqual(package["status"], "READY_FOR_REVIEW")

    def test_refuses_a_missing_project_without_a_concept_id(self):
        proc = self.cm("produce", "pytest-does-not-exist-anywhere")
        self.assertEqual(proc.returncode, EXIT_ERROR)

    def test_unknown_concept_id_fails_at_the_scaffold_stage(self):
        video_id = f"pytest-produce-{self.video_id}"
        proc = self.cm("produce", video_id, "--concept-id", "no-such-concept-xyz")
        self.assertNotEqual(proc.returncode, EXIT_OK)
        self.assertFalse((ROOT / "projects" / video_id).exists())


class TestResearchCommand(ProduceTestCase):
    """TEST_MODE=1 also selects the fixture SearchProvider (see
    subject_research._select_provider), so this never touches a network."""

    def _cleanup_subject_research(self):
        (ROOT / "research" / "subjects" / f"{self.video_id}.json").unlink(missing_ok=True)

    def test_no_op_for_a_concept_that_does_not_require_it(self):
        self.init_project()
        self.write_metadata({
            "experiment": {"concept_id": "sleep-brown-noise-dark",
                          "generation_cost_usd": 0.0, "generation_seconds": None, "variables": {}},
        })
        proc = self.cm("research", self.video_id)
        self.assertEqual(proc.returncode, EXIT_OK, proc.stderr)
        self.assertNotIn("subject_research", self.metadata().get("status", {}))

    def test_caches_sourced_facts_for_a_concept_that_requires_it(self):
        self.addCleanup(self._cleanup_subject_research)
        self.init_project()
        self.write_metadata({
            "experiment": {"concept_id": "story-sleepy-history-adult",
                          "generation_cost_usd": 0.0, "generation_seconds": None, "variables": {}},
        })
        proc = self.cm("research", self.video_id)
        self.assertEqual(proc.returncode, EXIT_OK, proc.stderr)
        self.assertEqual(self.metadata()["status"]["subject_research"], "OK")
        artifact = json.loads(
            (ROOT / "research" / "subjects" / f"{self.video_id}.json").read_text())
        self.assertGreaterEqual(len(artifact["facts"]), 2)
        for fact in artifact["facts"]:
            self.assertIn("source_url", fact)

    def test_a_second_run_reuses_the_cache_rather_than_researching_again(self):
        self.addCleanup(self._cleanup_subject_research)
        self.init_project()
        self.write_metadata({
            "experiment": {"concept_id": "story-sleepy-history-adult",
                          "generation_cost_usd": 0.0, "generation_seconds": None, "variables": {}},
        })
        first = self.cm("research", self.video_id)
        self.assertEqual(first.returncode, EXIT_OK, first.stderr)
        first_artifact = (ROOT / "research" / "subjects" / f"{self.video_id}.json").read_text()

        second = self.cm("research", self.video_id)
        self.assertEqual(second.returncode, EXIT_OK, second.stderr)
        second_artifact = (ROOT / "research" / "subjects" / f"{self.video_id}.json").read_text()
        self.assertEqual(first_artifact, second_artifact)


class TestCreativeRequiresResearchCommand(ProduceTestCase):

    def _cleanup_subject_research(self):
        (ROOT / "research" / "subjects" / f"{self.video_id}.json").unlink(missing_ok=True)

    def test_creative_refuses_without_cached_research(self):
        self.init_project()
        self.write_metadata({
            "experiment": {"concept_id": "story-sleepy-history-adult",
                          "generation_cost_usd": 0.0, "generation_seconds": None, "variables": {}},
        })
        proc = self.cm("creative", self.video_id)
        self.assertEqual(proc.returncode, EXIT_ERROR, proc.stdout + proc.stderr)

    def test_creative_succeeds_once_research_is_cached(self):
        self.addCleanup(self._cleanup_subject_research)
        self.init_project()
        self.write_metadata({
            "experiment": {"concept_id": "story-sleepy-history-adult",
                          "generation_cost_usd": 0.0, "generation_seconds": None, "variables": {}},
        })
        research_proc = self.cm("research", self.video_id)
        self.assertEqual(research_proc.returncode, EXIT_OK, research_proc.stderr)

        proc = self.cm("creative", self.video_id)
        self.assertEqual(proc.returncode, EXIT_OK, proc.stderr)
        self.assertTrue(self.metadata()["script"])


class TestStoryboardAndScenesCommands(ProduceTestCase):

    def _cleanup_subject_research(self):
        (ROOT / "research" / "subjects" / f"{self.video_id}.json").unlink(missing_ok=True)

    def test_storyboard_and_scenes_for_a_concept_with_no_research_requirement(self):
        """The no-research path must be unaffected: no scene_motifs key, and
        the existing scenes/reuse-by-digest behaviour still generates images."""
        self.init_project("--duration", "20")
        self.write_metadata({
            "visual_plan": {"prompt": "a dark still", "negative_prompt": "text",
                           "style": "deep-night"},
            "experiment": {"concept_id": "sleep-brown-noise-dark",
                          "generation_cost_usd": 0.0, "generation_seconds": None, "variables": {}},
        })
        proc = self.cm("storyboard", self.video_id, "--scenes", "2")
        self.assertEqual(proc.returncode, EXIT_OK, proc.stderr)
        board = json.loads((self.pdir / "storyboard.json").read_text())
        self.assertEqual(len(board["scenes"]), 2)
        self.assertNotIn("scene_motifs", board)

        proc = self.cm("scenes", self.video_id)
        self.assertEqual(proc.returncode, EXIT_OK, proc.stderr)
        board = json.loads((self.pdir / "storyboard.json").read_text())
        self.assertTrue(all(s["image"] for s in board["scenes"]))

    def test_storyboard_fails_closed_for_a_research_required_concept_with_no_cache(self):
        self.init_project("--duration", "20")
        self.write_metadata({
            "experiment": {"concept_id": "story-sleepy-history-adult",
                          "generation_cost_usd": 0.0, "generation_seconds": None, "variables": {}},
        })
        proc = self.cm("storyboard", self.video_id, "--scenes", "2")
        self.assertEqual(proc.returncode, EXIT_ERROR, proc.stdout + proc.stderr)

    def test_storyboard_assigns_a_grounded_motif_per_scene_once_research_is_cached(self):
        self.addCleanup(self._cleanup_subject_research)
        self.init_project("--duration", "20")
        self.write_metadata({
            "script": "The canal opened in 1869. It has no locks. Ships still use it today.",
            "visual_plan": {"prompt": "dim archival stills", "negative_prompt": "text",
                           "style": "deep-night"},
            "experiment": {"concept_id": "story-sleepy-history-adult",
                          "generation_cost_usd": 0.0, "generation_seconds": None, "variables": {}},
        })
        research_proc = self.cm("research", self.video_id)
        self.assertEqual(research_proc.returncode, EXIT_OK, research_proc.stderr)

        proc = self.cm("storyboard", self.video_id, "--scenes", "3")
        self.assertEqual(proc.returncode, EXIT_OK, proc.stdout + proc.stderr)
        board = json.loads((self.pdir / "storyboard.json").read_text())
        self.assertEqual(len(board["scene_motifs"]), 3)
        for scene in board["scenes"]:
            self.assertEqual(scene["visual_intent"], board["scene_motifs"][scene["scene_id"]])

        # Rebuilding without --force reuses the cached motifs rather than
        # calling the (batched) motif generator again.
        second = self.cm("storyboard", self.video_id, "--scenes", "3")
        self.assertEqual(second.returncode, EXIT_OK, second.stderr)
        second_board = json.loads((self.pdir / "storyboard.json").read_text())
        self.assertEqual(second_board["scene_motifs"], board["scene_motifs"])


class ListProjectsTest(ProjectTestCase):
    """Direct unit tests on the typed domain function - no subprocess, no
    CLI - per the web-architecture plan's shared-domain-boundary design."""

    def test_a_freshly_initialized_project_appears_in_the_list(self):
        self.init_project()
        summaries = project.list_projects()
        ids = [s["video_id"] for s in summaries]
        self.assertIn(self.video_id, ids)

    def test_summary_reflects_recorded_overall_status(self):
        self.init_project()
        self.write_metadata({"status": {"overall": "NEEDS_ATTENTION"}})
        summary = next(s for s in project.list_projects() if s["video_id"] == self.video_id)
        self.assertEqual(summary["overall_status"], "NEEDS_ATTENTION")

    def test_a_corrupt_metadata_json_is_skipped_not_raised(self):
        self.init_project()
        (self.pdir / "metadata.json").write_text("{not valid json")
        # Must not raise, and every other project must still be listed.
        summaries = project.list_projects()
        self.assertNotIn(self.video_id, [s["video_id"] for s in summaries])

    def test_no_projects_directory_returns_empty_list_not_an_error(self):
        prev = project.PROJECTS_DIR
        project.PROJECTS_DIR = prev / "no-such-subdir"
        try:
            self.assertEqual(project.list_projects(), [])
        finally:
            project.PROJECTS_DIR = prev


class StatusReportTest(ProjectTestCase):
    """Direct unit tests on status_report - the pure function cmd_status
    is now a thin formatter over (see scripts/project.py)."""

    def test_unknown_project_returns_none(self):
        self.assertIsNone(project.status_report("pytest-does-not-exist-anywhere"))

    def test_not_yet_rendered_project_reports_not_rendered(self):
        self.init_project()
        report = project.status_report(self.video_id)
        self.assertEqual(report["verdict"], "NOT_RENDERED")
        self.assertEqual(report["blocking"], [])

    def test_matches_cmd_status_cli_output_after_a_run(self):
        self.init_project("--title", "Parity Check", "--production-grade-visuals")
        self.write_metadata({"description": "A description."})
        self.assertEqual(self.cm("run", self.video_id).returncode, EXIT_OK)

        report = project.status_report(self.video_id)
        self.assertEqual(report["verdict"], "READY_FOR_REVIEW")
        self.assertEqual(report["digest_state"], "MATCHES")
        self.assertFalse(report["stale"])

        cli = self.cm("status", self.video_id)
        self.assertIn("READY_FOR_REVIEW", cli.stdout)
        self.assertIn("digest:   matches", cli.stdout)


class ProjectLockTest(ProjectTestCase):
    """The one concurrency primitive shared by every mutating domain
    function (and, transitively, the CLI and any future web caller)."""

    def test_a_second_acquisition_on_the_same_project_fails_fast(self):
        self.init_project()
        with project.project_lock(self.video_id):
            with self.assertRaises(project.ProjectBusyError):
                with project.project_lock(self.video_id):
                    pass  # pragma: no cover - must never be reached

    def test_lock_is_released_on_exit_so_a_later_acquisition_succeeds(self):
        self.init_project()
        with project.project_lock(self.video_id):
            pass
        with project.project_lock(self.video_id):
            pass  # must not raise

    def test_different_projects_do_not_contend(self):
        self.init_project()
        other_id = f"{self.video_id}-other"
        self.addCleanup(lambda: shutil.rmtree(
            ROOT / "projects" / other_id, ignore_errors=True))
        with project.project_lock(self.video_id):
            with project.project_lock(other_id):
                pass  # must not raise


class TypedDomainFunctionsTest(ProjectTestCase):
    """Direct, non-argparse calls into the run_* functions the web layer
    also calls (architecture plan §1/§17) - convenience checks on top of
    the subprocess-driven suite above, which already proves every cmd_*
    shim delegates to these unchanged."""

    def setUp(self):
        super().setUp()
        self._prev_test_mode = os.environ.get("TEST_MODE")
        os.environ["TEST_MODE"] = "1"
        self.addCleanup(self._restore_test_mode)

    def _restore_test_mode(self):
        if self._prev_test_mode is None:
            os.environ.pop("TEST_MODE", None)
        else:
            os.environ["TEST_MODE"] = self._prev_test_mode

    def test_run_creative_returns_a_stage_result(self):
        self.init_project()
        self.write_metadata({
            "experiment": {"concept_id": "sleep-brown-noise-dark",
                           "generation_cost_usd": 0.0, "generation_seconds": None,
                           "variables": {}},
        })
        result = project.run_creative(self.video_id)
        self.assertIsInstance(result, project.StageResult)
        self.assertTrue(result.ok)
        self.assertEqual(result.exit_code, 0)
        self.assertEqual(result.data["selected_title"], self.metadata()["selected_title"])

    def test_a_failed_stage_returns_ok_false_with_a_matching_exit_code(self):
        self.init_project()
        result = project.run_audio(self.video_id)
        self.assertIsInstance(result, project.StageResult)
        self.assertFalse(result.ok)
        self.assertEqual(result.exit_code, 1)

    def test_cmd_shim_exit_code_matches_the_typed_functions_own(self):
        self.init_project()
        self.write_metadata({
            "experiment": {"concept_id": "sleep-brown-noise-dark",
                           "generation_cost_usd": 0.0, "generation_seconds": None,
                           "variables": {}},
        })
        result = project.run_creative(self.video_id, force=True)
        env = dict(os.environ, TEST_MODE="1")
        proc = subprocess.run(
            [str(CLI), "creative", self.video_id, "--force"],
            capture_output=True, text=True, cwd=str(ROOT), env=env)
        self.assertEqual(proc.returncode, result.exit_code)


class ReviewDecisionTest(ProjectTestCase):
    """record_review_decision (architecture plan §5) - the four required
    proofs plus the surrounding behavior they depend on."""

    def _run_to_ready(self, title="Review Me", production_grade=True):
        extra = ["--title", title]
        if production_grade:
            extra.append("--production-grade-visuals")
        self.init_project(*extra)
        self.write_metadata({"description": "A description, required before review."})
        proc = self.cm("run", self.video_id)
        self.assertEqual(proc.returncode, EXIT_OK, proc.stderr)
        return self.metadata()["status"]["gate_digest"]

    # --- (1) static: no automated caller ---------------------------------

    def test_record_review_decision_is_never_called_automatically(self):
        """The only permitted callers anywhere in scripts/project.py are the
        CLI's own approve/reject shims - never a pipeline stage such as
        run_produce/run_pipeline, and never apps/engine/tasks.py's Celery
        dispatcher (checked separately in webapp's own test suite)."""
        import ast
        source = (ROOT / "scripts" / "project.py").read_text()
        tree = ast.parse(source, filename="scripts/project.py")
        callers = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef):
                for child in ast.walk(node):
                    if (isinstance(child, ast.Call)
                            and isinstance(child.func, ast.Name)
                            and child.func.id == "record_review_decision"):
                        callers.add(node.name)
        self.assertEqual(callers, {"cmd_approve", "cmd_reject"})

    # --- (2) approval refused whenever gate_blockers() is non-empty ------

    def test_approval_is_refused_when_gate_blockers_are_non_empty(self):
        self.init_project("--title", "Missing Description")
        proc = self.cm("run", self.video_id)
        self.assertEqual(proc.returncode, EXIT_NEEDS_ATTENTION, proc.stderr)
        digest = self.metadata()["status"]["gate_digest"]

        proc = self.cm("approve", self.video_id, "--reviewer", "alice@example.com",
                       "--expected-digest", digest)
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("cannot approve", proc.stderr)
        self.assertNotIn("review_history", self.metadata())
        self.assertNotIn("publish", self.metadata())

    def test_rejection_is_allowed_even_with_gate_blockers(self):
        """Flagging a broken project is always allowed - only approval is
        gated on a clean verdict."""
        self.init_project("--title", "Missing Description")
        proc = self.cm("run", self.video_id)
        self.assertEqual(proc.returncode, EXIT_NEEDS_ATTENTION, proc.stderr)
        digest = self.metadata()["status"]["gate_digest"]

        proc = self.cm("reject", self.video_id, "--reviewer", "alice@example.com",
                       "--notes", "not ready", "--expected-digest", digest)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        meta = self.metadata()
        self.assertEqual(meta["review_history"][-1]["decision"], "rejected")
        self.assertFalse(meta["publish"]["approved_by_human"])

    def test_stale_digest_is_refused(self):
        digest = self._run_to_ready()
        proc = self.cm("approve", self.video_id, "--reviewer", "alice@example.com",
                       "--expected-digest", digest + "stale")
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("stale", proc.stderr)
        self.assertNotIn("review_history", self.metadata())

    # --- (3) production_grade regression ----------------------------------

    def test_review_decision_never_touches_production_grade(self):
        digest = self._run_to_ready()
        before = self.metadata()["provenance"]["images"]["production_grade"]

        proc = self.cm("approve", self.video_id, "--reviewer", "alice@example.com",
                       "--expected-digest", digest)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        after = self.metadata()["provenance"]["images"]["production_grade"]
        self.assertEqual(before, after)
        self.assertIsInstance(after, bool, "production_grade must remain a human claim")

    # --- (4) CLI/API parity: one shared implementation ---------------------

    def test_cli_and_direct_call_produce_equivalent_metadata(self):
        """cmd_approve (CLI) and a direct record_review_decision() call (what
        the Django review view does) must write equivalent results - proof
        there is exactly one implementation, not a CLI-side copy a web
        caller could drift from."""
        digest_a = self._run_to_ready(title="Parity")
        proc = self.cm("approve", self.video_id, "--reviewer", "alice@example.com",
                       "--notes", "looks good", "--expected-digest", digest_a)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        meta_a = self.metadata()

        video_b = f"{self.video_id}-b"
        pdir_b = ROOT / "projects" / video_b
        self.addCleanup(lambda: shutil.rmtree(pdir_b, ignore_errors=True))
        proc = self.cm(
            "init", video_b, "--images", str(IMAGES), "--audio", str(AUDIO),
            "--width", "480", "--height", "270", "--fps", "24",
            "--duration", "5", "--seconds-per-image", "2",
            "--title", "Parity", "--production-grade-visuals")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        meta_b_patch = json.loads((pdir_b / "metadata.json").read_text())
        meta_b_patch["description"] = "A description, required before review."
        (pdir_b / "metadata.json").write_text(json.dumps(meta_b_patch, indent=2) + "\n")
        proc = self.cm("run", video_b)
        self.assertEqual(proc.returncode, EXIT_OK, proc.stderr)
        digest_b = json.loads((pdir_b / "metadata.json").read_text())["status"]["gate_digest"]

        project.record_review_decision(
            video_b, "alice@example.com", "approved",
            notes="looks good", expected_digest=digest_b)
        meta_b = json.loads((pdir_b / "metadata.json").read_text())

        entry_a = dict(meta_a["review_history"][-1])
        entry_b = dict(meta_b["review_history"][-1])
        for entry in (entry_a, entry_b):
            entry.pop("utc")
            entry.pop("gate_digest")
        self.assertEqual(entry_a, entry_b)
        self.assertEqual(meta_a["publish"], meta_b["publish"])

    # --- supporting behavior -------------------------------------------

    def test_refuses_an_empty_reviewer(self):
        with self.assertRaises(project.ReviewDecisionError):
            project.record_review_decision(
                self.video_id, "  ", "approved", expected_digest="anything")

    def test_refuses_an_unrendered_project(self):
        self.init_project()
        with self.assertRaises(project.ReviewDecisionError):
            project.record_review_decision(
                self.video_id, "alice@example.com", "approved", expected_digest="anything")

    def test_approval_persists_across_review_history(self):
        digest = self._run_to_ready()
        entry = project.record_review_decision(
            self.video_id, "alice@example.com", "approved",
            notes="ship it", expected_digest=digest)
        self.assertEqual(entry["decision"], "approved")
        self.assertEqual(entry["reviewer"], "alice@example.com")
        meta = self.metadata()
        self.assertEqual(len(meta["review_history"]), 1)
        self.assertTrue(meta["publish"]["approved_by_human"])
        self.assertFalse(json.loads(
            (self.pdir / "output" / "publication_package.json").read_text()
        )["publish"]["published"], "record_review_decision must never publish anything")


if __name__ == "__main__":
    unittest.main(verbosity=2)
