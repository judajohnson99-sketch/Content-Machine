#!/usr/bin/env python3
"""End-to-end tests for the project/asset layer and orchestration.

Drives ./content-machine (init / validate / run) against the checked-in
fixtures inside a temporary projects/ entry, then verifies the rendered
MP4, QC report, and publication package. Standard library only.
"""
import json
import hashlib
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
import unittest.mock
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CLI = ROOT / "content-machine"
IMAGES = ROOT / "tests" / "fixtures" / "images"
AUDIO = ROOT / "tests" / "fixtures" / "audio" / "test_tone.wav"

sys.path.insert(0, str(ROOT / "scripts"))
import project  # noqa: E402 - direct import for the typed domain functions
import research  # noqa: E402
import subject_research  # noqa: E402

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

    def test_no_facts_to_source_still_runs_competitor_research(self):
        self.addCleanup(self._cleanup_subject_research)
        self.addCleanup(lambda: [p.unlink(missing_ok=True) for p in (
            research.brief_path(self.video_id), research.findings_path(self.video_id),
            research.directives_path(self.video_id))])
        self.init_project()
        self.write_metadata({
            "experiment": {"concept_id": "sleep-brown-noise-dark",
                          "generation_cost_usd": 0.0, "generation_seconds": None, "variables": {}},
        })
        proc = self.cm("research", self.video_id)
        self.assertEqual(proc.returncode, EXIT_OK, proc.stderr)
        status = self.metadata().get("status", {})
        self.assertEqual(status["subject_research"], "NOT_APPLICABLE")
        self.assertEqual(status["competitor_research"], "OK")
        self.assertIsNone(subject_research.load_subject_research(self.video_id))

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

    def test_storyboard_assigns_environment_motifs_when_no_research_is_required_but_depicted_imagery_is(self):
        """sleep-rain-window-static needs real depicted imagery
        (procedural_visuals_acceptable=false) but has no subject to
        research: it must still get scene-diversifying motifs, not one
        fixed base prompt reused for every scene."""
        self.init_project("--duration", "20")
        self.write_metadata({
            "visual_plan": {"prompt": "rain on a window", "negative_prompt": "text",
                           "style": "deep-night"},
            "experiment": {"concept_id": "sleep-rain-window-static",
                          "generation_cost_usd": 0.0, "generation_seconds": None, "variables": {}},
        })
        proc = self.cm("storyboard", self.video_id, "--scenes", "4")
        self.assertEqual(proc.returncode, EXIT_OK, proc.stdout + proc.stderr)
        board = json.loads((self.pdir / "storyboard.json").read_text())
        self.assertEqual(len(board["scene_motifs"]), 4)
        for scene in board["scenes"]:
            self.assertEqual(scene["visual_intent"], board["scene_motifs"][scene["scene_id"]])

        # Rebuilding without --force reuses the cached motifs rather than
        # calling the (batched) environment generator again.
        second = self.cm("storyboard", self.video_id, "--scenes", "4")
        self.assertEqual(second.returncode, EXIT_OK, second.stderr)
        second_board = json.loads((self.pdir / "storyboard.json").read_text())
        self.assertEqual(second_board["scene_motifs"], board["scene_motifs"])


class SceneAssetReuseTest(ProduceTestCase):
    """Two scenes that were deliberately given the same environment are one
    picture, not two renders of the same thing - and `images/` is not the
    same list as "the images this video uses"."""

    def test_identical_scene_prompts_are_rendered_once_and_shared(self):
        self.init_project("--duration", "20")
        self.write_metadata({
            "visual_plan": {"prompt": "a dark still", "negative_prompt": "text",
                           "style": "deep-night"},
            "experiment": {"concept_id": "sleep-brown-noise-dark",
                          "generation_cost_usd": 0.0, "generation_seconds": None,
                          "variables": {}},
        })
        self.assertEqual(self.cm("storyboard", self.video_id, "--scenes", "3").returncode,
                         EXIT_OK)
        board = json.loads((self.pdir / "storyboard.json").read_text())
        digests = {s["generation"]["request_digest"] for s in board["scenes"]}
        self.assertEqual(len(digests), 1,
                         "scenes with the same environment must share one digest")

        proc = self.cm("scenes", self.video_id)
        self.assertEqual(proc.returncode, EXIT_OK, proc.stdout + proc.stderr)
        board = json.loads((self.pdir / "storyboard.json").read_text())
        images = {s["image"] for s in board["scenes"]}
        self.assertEqual(len(images), 1, f"expected one shared render, got {images}")
        self.assertEqual(
            self.metadata()["provenance"]["images"]["scene_images"], sorted(images))

    def test_a_stray_image_is_not_part_of_the_deliverable(self):
        self.init_project("--duration", "20")
        self.write_metadata({
            "visual_plan": {"prompt": "a dark still", "negative_prompt": "text",
                           "style": "deep-night"},
            "experiment": {"concept_id": "sleep-brown-noise-dark",
                          "generation_cost_usd": 0.0, "generation_seconds": None,
                          "variables": {}},
        })
        self.assertEqual(self.cm("storyboard", self.video_id, "--scenes", "2").returncode,
                         EXIT_OK)
        self.assertEqual(self.cm("scenes", self.video_id).returncode, EXIT_OK)

        referenced = project.scene_referenced_images(self.pdir)
        self.assertTrue(referenced)
        # init ingested source images that no scene points at; they are
        # already residue by this definition.
        before = project.unreferenced_images(self.pdir)
        for path in referenced:
            self.assertNotIn(path, before)

        # A retry that wrote a file and was then abandoned: residue, not an asset.
        stray = self.pdir / "images" / "zz_abandoned_retry.png"
        shutil.copyfile(referenced[0], stray)
        self.assertEqual(project.scene_referenced_images(self.pdir), referenced)
        self.assertEqual(project.unreferenced_images(self.pdir), sorted(before + [stray]))

    def test_without_a_storyboard_there_is_nothing_to_say(self):
        """No storyboard means no opinion about which images are the
        deliverable - not "none of them are"."""
        self.init_project()
        self.assertIsNone(project.scene_referenced_images(self.pdir))
        self.assertEqual(project.unreferenced_images(self.pdir), [])


class AudioGradeTest(ProjectTestCase):
    """The audio counterpart of the visual production-grade claim. A source
    that cannot be production-grade (synthesised music standing in for
    music) holds review until a human has actually listened."""

    def _synthesised_music_project(self):
        self.init_project("--title", "Audio Grade", "--production-grade-visuals")
        meta = self.metadata()
        meta["description"] = "A description, required before review."
        meta["provenance"]["audio"].update({
            "kind": "music", "source": "generative-music",
            "production_grade_capable": False, "plan_digest": "abc123",
        })
        (self.pdir / "metadata.json").write_text(json.dumps(meta, indent=2) + "\n")

    def _blockers(self):
        """Everything the gate would hold review for, with every non-audio
        input already satisfying it."""
        metadata = self.metadata()
        manifest_path = self.pdir / "audio" / "audio_manifest.json"
        manifest = json.loads(manifest_path.read_text()) if manifest_path.is_file() else {
            "commercial_use_cleared": True, "attributions_required": [],
            "quality": {"warnings": []}}
        return project.gate_blockers(self.pdir, metadata, "PASS", [], True, manifest)

    def test_an_ungraded_synthesised_music_track_blocks_review(self):
        self._synthesised_music_project()
        blockers = self._blockers()
        self.assertTrue(any("audio-grade" in b for b in blockers), blockers)

    def test_a_human_pass_clears_the_blocker(self):
        self._synthesised_music_project()
        project.record_audio_grade(self.video_id, "a human", True, notes="listened")
        self.assertFalse(any("audio" in b for b in self._blockers()), self._blockers())
        prov = self.metadata()["provenance"]["audio"]
        self.assertEqual(prov["graded_by"], "a human")
        self.assertTrue(prov["graded_utc"])

    def test_a_human_fail_blocks_with_their_own_reason(self):
        self._synthesised_music_project()
        project.record_audio_grade(self.video_id, "a human", False,
                                   notes="the bells clash")
        self.assertTrue(any("the bells clash" in b for b in self._blockers()))

    def test_a_grade_requires_a_named_human(self):
        self._synthesised_music_project()
        with self.assertRaises(project.ReviewDecisionError):
            project.record_audio_grade(self.video_id, "", True)
        with self.assertRaises(project.ReviewDecisionError):
            project.record_audio_grade(self.video_id, "a human", "yes")

    def test_a_capable_source_needs_no_ceremonial_claim(self):
        """Where a synthesised texture *is* the product, the artefact is
        exactly what was specified; demanding a claim there would teach
        people to click through one."""
        self.init_project("--title", "Texture", "--production-grade-visuals")
        meta = self.metadata()
        meta["description"] = "A description."
        meta["provenance"]["audio"].update({
            "kind": "texture", "source": "procedural-texture",
            "production_grade_capable": True})
        (self.pdir / "metadata.json").write_text(json.dumps(meta, indent=2) + "\n")
        self.assertFalse(any("audio-grade" in b for b in self._blockers()))

    def test_the_verdict_does_not_survive_a_changed_audio_plan(self):
        """A human listened to a specific track. Re-rendering the same plan
        reproduces it; a different plan does not."""
        self.init_project("--title", "Regraded")
        self.write_metadata({"audio_plan": {"composition": {
            "target_seconds": 3,
            "layers": [{"id": "bed", "provider": "noise",
                       "params": {"color": "brown"}, "gain_db": 0.0}]}}})
        self.assertEqual(self.cm("audio", self.video_id, "--duration", "3").returncode,
                         EXIT_OK)
        project.record_audio_grade(self.video_id, "a human", True)
        self.assertEqual(self.cm("audio", self.video_id, "--duration", "3").returncode,
                         EXIT_OK)
        self.assertTrue(self.metadata()["provenance"]["audio"]["production_grade"],
                        "the same plan must keep the verdict")

        self.write_metadata({"audio_plan": {"composition": {
            "target_seconds": 3,
            "layers": [{"id": "bed", "provider": "pad",
                       "params": {"chord": "warm"}, "gain_db": 0.0}]}}})
        self.assertEqual(self.cm("audio", self.video_id, "--duration", "3").returncode,
                         EXIT_OK)
        self.assertIsNone(self.metadata()["provenance"]["audio"]["production_grade"],
                          "a different plan must drop the verdict")


class ProjectErrorMessageTest(unittest.TestCase):
    """One string is one problem, never a list of characters."""

    def test_a_single_string_is_not_split_into_characters(self):
        e = project.ProjectError("no such project: abc")
        self.assertEqual(str(e), "no such project: abc")
        self.assertEqual(e.problems, ["no such project: abc"])

    def test_a_list_still_joins_its_problems(self):
        e = project.ProjectError(["first", "second"])
        self.assertEqual(str(e), "first; second")


class ThumbnailSamplingTest(unittest.TestCase):
    """Candidates must be different pictures, not different clock times.

    A board that reuses one environment for three scenes is the normal case
    now that one distinct picture means one render; sampling fixed fractions
    of the runtime would offer the reviewer the same frame three times.
    """

    @staticmethod
    def _scene(scene_id, image, seconds, transition=2.0):
        return {"scene_id": scene_id, "image": image,
                "duration_seconds": seconds,
                "transition": {"kind": "crossfade" if transition else "cut",
                               "duration_seconds": transition}}

    def test_one_timestamp_per_distinct_picture_in_scene_order(self):
        board = {"scenes": [
            self._scene("s01", "images/a.png", 10.0),
            self._scene("s02", "images/a.png", 10.0),
            self._scene("s03", "images/b.png", 10.0, transition=0.0),
        ]}
        # Starts are 0, 8, 16 with a 2s crossfade; midpoints 5 and 21.
        self.assertEqual(project.thumbnail_timestamps(board, 26.0), [5.0, 21.0])

    def test_no_storyboard_falls_back_to_runtime_fractions(self):
        self.assertEqual(project.thumbnail_timestamps(None, 100.0),
                         [25.0, 50.0, 75.0])

    def test_scenes_without_a_rendered_image_fall_back_too(self):
        board = {"scenes": [self._scene("s01", None, 10.0)]}
        self.assertEqual(project.thumbnail_timestamps(board, 100.0),
                         [25.0, 50.0, 75.0])

    def test_a_long_board_is_capped_rather_than_seeking_once_per_scene(self):
        board = {"scenes": [self._scene(f"s{i:02d}", f"images/{i}.png", 10.0)
                            for i in range(40)]}
        stamps = project.thumbnail_timestamps(board, 400.0)
        self.assertEqual(len(stamps), project.MAX_THUMBNAIL_CANDIDATES)
        self.assertEqual(stamps, sorted(stamps))

    def test_a_timestamp_past_the_finished_runtime_is_dropped(self):
        board = {"scenes": [self._scene("s01", "images/a.png", 10.0),
                            self._scene("s02", "images/b.png", 10.0, transition=0.0)]}
        # A truncated render: only the first scene's midpoint is inside it.
        self.assertEqual(project.thumbnail_timestamps(board, 9.0), [5.0])


class ListProjectsTest(ProjectTestCase):
    """Direct unit tests on the typed domain function - no subprocess, no
    CLI - per the web-architecture plan's shared-domain-boundary design."""

    def test_a_freshly_initialized_project_appears_in_the_list(self):
        self.init_project()
        summaries = project.list_projects()
        ids = [s["video_id"] for s in summaries]
        self.assertIn(self.video_id, ids)

    def test_archiving_hides_without_deleting_and_is_attributed(self):
        self.init_project()
        # Every mutating domain function takes the project's flock, and the
        # lock file it opens is a permanent fixture of the directory - not an
        # asset. What this test is about is that nothing of the project's
        # content moves when it is archived.
        def contents():
            return sorted(str(p) for p in self.pdir.rglob("*")
                          if p.name != ".lock")

        before = contents()
        with self.assertRaises(project.ProjectError):
            project.set_archived(self.video_id, True, "")
        summary = project.set_archived(self.video_id, True, "owner@example.com",
                                       reason="pytest residue")
        self.assertTrue(summary["archived"])
        meta = json.loads((self.pdir / "metadata.json").read_text())
        self.assertEqual(meta["archived"]["by"], "owner@example.com")
        self.assertEqual(meta["archived"]["reason"], "pytest residue")
        self.assertEqual(meta["history"][-1]["event"], "archived")
        self.assertEqual(contents(), before,
                         "archiving must not remove or add any file")
        listed = next(s for s in project.list_projects() if s["video_id"] == self.video_id)
        self.assertTrue(listed["archived"], "still listed - views filter, the domain does not hide")
        restored = project.set_archived(self.video_id, False, "owner@example.com")
        self.assertFalse(restored["archived"])
        self.assertNotIn("archived", json.loads((self.pdir / "metadata.json").read_text()))
        with self.assertRaises(project.ProjectError):
            project.set_archived("no-such-project", True, "owner@example.com")

    def test_summary_reflects_recorded_overall_status(self):
        self.init_project()
        self.write_metadata({"status": {"overall": "NEEDS_ATTENTION"}})
        summary = next(s for s in project.list_projects() if s["video_id"] == self.video_id)
        self.assertEqual(summary["overall_status"], "NEEDS_ATTENTION")

    def test_summary_offers_a_preview_image_and_never_invents_one(self):
        """The dashboard browses productions by eye, so a summary carries one
        servable still - but "no picture yet" stays a true answer."""
        self.init_project()
        summary = project.project_summary(self.video_id)
        self.assertIsNotNone(summary["preview_image"])
        self.assertTrue(summary["preview_image"].startswith("images/"),
                        summary["preview_image"])
        # Resolvable through the one rule for servable paths.
        project.project_file_path(self.video_id, summary["preview_image"])

        # A thumbnail from the finished render wins over a source image.
        thumbs = self.pdir / "thumbnail"
        thumbs.mkdir(exist_ok=True)
        (thumbs / "candidate_1.jpg").write_bytes(b"not really a jpeg")
        self.assertEqual(project.project_summary(self.video_id)["preview_image"],
                         "thumbnail/candidate_1.jpg")

        # Nothing on disk means None, not a placeholder.
        for path in list((self.pdir / "images").iterdir()) + list(thumbs.iterdir()):
            path.unlink()
        self.assertIsNone(project.project_summary(self.video_id)["preview_image"])

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


class ResearchBriefWiringTest(ProjectTestCase):
    """run_research picks up a project's research brief (if any) and runs
    brief-driven competitor research alongside subject research, without
    ever making the stage fail over an unconfigured/absent brief."""

    def setUp(self):
        super().setUp()
        self._prev_test_mode = os.environ.get("TEST_MODE")
        os.environ["TEST_MODE"] = "1"
        self.addCleanup(self._restore_test_mode)
        self.addCleanup(self._clean_research_files)

    def _restore_test_mode(self):
        if self._prev_test_mode is None:
            os.environ.pop("TEST_MODE", None)
        else:
            os.environ["TEST_MODE"] = self._prev_test_mode

    def _clean_research_files(self):
        research.brief_path(self.video_id).unlink(missing_ok=True)
        research.findings_path(self.video_id).unlink(missing_ok=True)
        research.directives_path(self.video_id).unlink(missing_ok=True)
        subject_research.subject_path(self.video_id).unlink(missing_ok=True)

    def _link(self, concept_id):
        self.write_metadata({
            "experiment": {"concept_id": concept_id,
                           "generation_cost_usd": 0.0, "generation_seconds": None,
                           "variables": {}},
        })

    def test_no_brief_and_no_facts_still_gets_competitor_research(self):
        """The old exemption - a non-factual concept with no brief was a
        research no-op - is gone: a brief is derived from the concept and
        competitor research runs."""
        self.init_project()
        self._link("sleep-brown-noise-dark")
        self.assertIsNone(research.load_brief(self.video_id))
        result = project.run_research(self.video_id)
        self.assertTrue(result.ok, result.message)
        self.assertTrue(result.data["brief_derived"])
        brief = research.load_brief(self.video_id)
        self.assertEqual(brief["niche"], "adult_sleep")
        self.assertIn("Brown Noise for Deep Sleep", brief["creative_intent"])
        findings = result.data["findings"]
        self.assertIn("competitors", findings["topics_covered"])
        self.assertTrue(findings["competitor_analysis"]["videos"])
        self.assertEqual(findings["competitor_analysis"]["queries"][0],
                         "Brown Noise for Deep Sleep")
        status = self.metadata()["status"]
        self.assertEqual(status["competitor_research"], "OK")
        self.assertEqual(status["subject_research"], "NOT_APPLICABLE")
        self.assertEqual(status["research_brief"], "DERIVED")
        self.assertIsNotNone(research.load_directives(self.video_id))
        self.assertEqual(project._research_blockers(self.pdir), [])

    def test_no_concept_in_the_catalogue_is_exempt_from_research(self):
        import experiment
        _, concepts = experiment.load_concepts()
        self.assertTrue(concepts)
        self.init_project()
        for concept in concepts:
            with self.subTest(concept=concept["id"]):
                self._clean_research_files()
                self._link(concept["id"])
                result = project.run_research(self.video_id, force=True)
                self.assertTrue(result.ok, result.message)
                self.assertIsNotNone(research.load_brief(self.video_id))
                findings = research.load_findings(self.video_id)
                self.assertIsNotNone(findings, f"{concept['id']} was not researched")
                self.assertIn("competitors", findings["topics_covered"])
                if concept.get("requires_subject_research"):
                    self.assertIsNotNone(subject_research.load_subject_research(self.video_id))

    def test_research_that_cannot_run_is_recorded_and_blocks_the_gate(self):
        self.init_project()
        self._link("sleep-brown-noise-dark")
        with unittest.mock.patch.object(
                research, "research_project",
                side_effect=research.ResearchError("every source failed: duckduckgo bot challenge")):
            result = project.run_research(self.video_id)
        self.assertTrue(result.ok, result.message)
        status = self.metadata()["status"]
        self.assertEqual(status["competitor_research"], "SKIPPED")
        self.assertIn("bot challenge", status["competitor_research_reason"])
        self.assertTrue(project._research_blockers(self.pdir))

    def test_a_saved_brief_produces_findings_under_the_fixture_provider(self):
        self.init_project()
        self.write_metadata({
            "experiment": {"concept_id": "sleep-brown-noise-dark",
                           "generation_cost_usd": 0.0, "generation_seconds": None,
                           "variables": {}},
        })
        research.save_brief(self.video_id, {
            "niche": "sleep ambience", "creative_intent": "cozy rainy night"})

        result = project.run_research(self.video_id)
        self.assertTrue(result.ok, result.message)
        self.assertIsNotNone(result.data["findings"])
        self.assertTrue(result.data["findings"]["findings"])
        self.assertEqual(
            self.metadata()["status"]["competitor_research"], "OK")

        loaded = research.load_findings(self.video_id)
        self.assertEqual(loaded, result.data["findings"])

    def test_run_creative_passes_research_context_through_without_failing(self):
        """No LLM is reached under TEST_MODE, so this proves the plumbing
        (brief/findings loaded and handed to generate_brief/
        build_audio_composition) does not break the stage - the prompt
        content itself is covered in tests/test_creative.py."""
        self.init_project()
        self.write_metadata({
            "experiment": {"concept_id": "sleep-brown-noise-dark",
                           "generation_cost_usd": 0.0, "generation_seconds": None,
                           "variables": {}},
        })
        research.save_brief(self.video_id, {"niche": "sleep ambience"})
        project.run_research(self.video_id)

        result = project.run_creative(self.video_id)
        self.assertTrue(result.ok, result.message)
        layers = self.metadata()["audio_plan"]["composition"]["layers"]
        self.assertEqual(layers[1]["provider"], "pad")


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


class ProjectAssetsTest(ProjectTestCase):
    """project_assets() is a read model over what is on disk: it lists, it
    summarises the artefacts' own JSON, and it never computes a verdict."""

    def test_unknown_project_is_none(self):
        self.assertIsNone(project.project_assets("pytest-no-such-project"))

    def test_fresh_project_lists_images_and_audio_but_no_deliverable(self):
        self.assertEqual(self.init_project().returncode, EXIT_OK)
        assets = project.project_assets(self.video_id)
        self.assertEqual(assets["video_id"], self.video_id)
        self.assertIsNone(assets["video"])
        self.assertIsNone(assets["qc"])
        self.assertIsNone(assets["package"])
        self.assertIsNone(assets["storyboard"])
        self.assertEqual(assets["thumbnails"], [])
        self.assertTrue(assets["images"])
        for image in assets["images"]:
            self.assertTrue(image["path"].startswith("images/"))
            self.assertGreater(image["bytes"], 0)
            self.assertIsNone(image["scene_id"])
        self.assertTrue(assets["audio"]["path"].startswith("audio/"))
        # No manifest yet - the composed-audio summary fields are absent/None.
        self.assertIsNone(assets["audio"]["seconds"])
        self.assertEqual(assets["audio"]["layers"], [])

    def test_rendered_project_exposes_video_thumbnails_qc_and_package(self):
        self.assertEqual(self.init_project().returncode, EXIT_OK)
        proc = self.cm("run", self.video_id)
        self.assertIn(proc.returncode, (EXIT_OK, EXIT_NEEDS_ATTENTION), proc.stderr)
        assets = project.project_assets(self.video_id)
        self.assertEqual(assets["video"]["path"], f"output/{self.video_id}.mp4")
        self.assertGreater(assets["video"]["bytes"], 1024)
        self.assertTrue(assets["thumbnails"])
        self.assertEqual(assets["qc"]["status"], "PASS")
        self.assertTrue(all("check" in c for c in assets["qc"]["checks"]))
        self.assertIn(assets["package"]["status"], ("READY_FOR_REVIEW", "NEEDS_ATTENTION"))
        self.assertEqual(assets["package"]["path"], "output/publication_package.json")
        self.assertTrue(assets["logs"])
        # Every listed path must be servable through the one path rule.
        for entry in [assets["video"], assets["audio"], *assets["thumbnails"],
                      *assets["images"], *assets["logs"]]:
            self.assertTrue(project.project_file_path(self.video_id, entry["path"]).is_file())


class ProjectFilePathTest(ProjectTestCase):
    """The single rule for which files may leave a project directory."""

    def setUp(self):
        super().setUp()
        self.assertEqual(self.init_project().returncode, EXIT_OK)
        self.image = project.project_assets(self.video_id)["images"][0]["path"]

    def test_a_listed_asset_resolves(self):
        resolved = project.project_file_path(self.video_id, self.image)
        self.assertEqual(resolved, (self.pdir / self.image).resolve())

    def assert_refused(self, relative, fragment):
        with self.assertRaises(project.ProjectError) as ctx:
            project.project_file_path(self.video_id, relative)
        self.assertIn(fragment, ctx.exception.problems[0])

    def test_traversal_and_absolute_paths_are_refused(self):
        self.assert_refused("../metadata.json", "not a servable asset path")
        self.assert_refused("images/../metadata.json", "not a servable asset path")
        self.assert_refused("images/./x.png", "not a servable asset path")
        self.assert_refused("/etc/passwd", "not a servable asset path")
        self.assert_refused("images\\x.png", "not a servable asset path")
        self.assert_refused("", "not a servable asset path")

    def test_project_root_files_are_never_served(self):
        self.assert_refused("metadata.json", "not a servable asset directory")
        self.assert_refused("video_spec.json", "not a servable asset directory")
        self.assert_refused("storyboard.json", "not a servable asset directory")

    def test_missing_file_and_unknown_project_are_refused(self):
        self.assert_refused("images/does-not-exist.png", "no such asset")
        with self.assertRaises(project.ProjectError):
            project.project_file_path("pytest-no-such-project", self.image)

    def test_a_symlink_escaping_the_project_is_refused(self):
        link = self.pdir / "images" / "escape.png"
        link.symlink_to(Path("/etc/hostname"))
        self.assert_refused("images/escape.png", "resolves outside the project")

    def test_a_directory_is_not_a_file(self):
        self.assert_refused("images", "no such asset")


class TestProduceSceneSelection(ProduceTestCase):
    """produce picks storyboard->scenes or single-plate visuals per project;
    the rule is produce_uses_scenes() and the override is --scenes/--no-scenes."""

    def test_forced_scenes_builds_a_storyboard_and_renders_it(self):
        video_id = f"pytest-produce-{self.video_id}"
        self.addCleanup(lambda: shutil.rmtree(ROOT / "projects" / video_id, ignore_errors=True))
        proc = self.cm("produce", video_id, "--concept-id", "sleep-brown-noise-dark",
                       "--duration", "6", "--scenes")
        self.assertEqual(proc.returncode, EXIT_NEEDS_ATTENTION, proc.stderr + proc.stdout)
        board = json.loads((ROOT / "projects" / video_id / "storyboard.json").read_text())
        self.assertGreaterEqual(len(board["scenes"]), 1)
        self.assertTrue(all(s["image"] for s in board["scenes"]))
        assets = project.project_assets(video_id)
        self.assertEqual(assets["storyboard"]["scene_count"], len(board["scenes"]))
        self.assertTrue(assets["video"])
        self.assertTrue(any(i["scene_id"] for i in assets["images"]))

    def test_a_narrated_project_uses_scenes_automatically(self):
        self.init_project("--duration", "6")
        self.write_metadata({
            "script": "Breathe in slowly. Hold for a moment. Let it go.",
            "experiment": {"concept_id": "sleep-brown-noise-dark",
                          "generation_cost_usd": 0.0, "generation_seconds": None, "variables": {}},
        })
        self.assertTrue(project.produce_uses_scenes(self.video_id, self.metadata()))
        proc = self.cm("produce", self.video_id)
        self.assertIn(proc.returncode, (EXIT_OK, EXIT_NEEDS_ATTENTION), proc.stderr + proc.stdout)
        self.assertTrue((self.pdir / "storyboard.json").is_file())
        self.assertEqual(self.metadata()["status"]["storyboard"], "OK")

    def test_an_ambient_project_keeps_the_single_plate_path(self):
        self.init_project("--duration", "6")
        self.write_metadata({
            "experiment": {"concept_id": "sleep-brown-noise-dark",
                          "generation_cost_usd": 0.0, "generation_seconds": None, "variables": {}},
        })
        self.assertFalse(project.produce_uses_scenes(self.video_id, self.metadata()))
        proc = self.cm("produce", self.video_id)
        self.assertIn(proc.returncode, (EXIT_OK, EXIT_NEEDS_ATTENTION), proc.stderr + proc.stdout)
        self.assertFalse((self.pdir / "storyboard.json").exists())

    def test_no_scenes_overrides_narration(self):
        self.init_project("--duration", "6")
        self.write_metadata({
            "script": "A sentence of narration.",
            "experiment": {"concept_id": "sleep-brown-noise-dark",
                          "generation_cost_usd": 0.0, "generation_seconds": None, "variables": {}},
        })
        proc = self.cm("produce", self.video_id, "--no-scenes")
        self.assertIn(proc.returncode, (EXIT_OK, EXIT_NEEDS_ATTENTION), proc.stderr + proc.stdout)
        self.assertFalse((self.pdir / "storyboard.json").exists())

    def test_an_existing_storyboard_is_kept_on_rerun(self):
        self.init_project("--duration", "6")
        self.write_metadata({
            "visual_plan": {"prompt": "a dark still", "negative_prompt": "text",
                           "style": "deep-night"},
            "experiment": {"concept_id": "sleep-brown-noise-dark",
                          "generation_cost_usd": 0.0, "generation_seconds": None, "variables": {}},
        })
        self.assertEqual(self.cm("storyboard", self.video_id, "--scenes", "2").returncode, EXIT_OK)
        self.assertTrue(project.produce_uses_scenes(self.video_id, self.metadata()))


class TestGpuWorkerDeferral(unittest.TestCase):
    """A depicted-imagery stage that no synchronous provider can serve is
    queued for the GPU worker (exit 2, never FAILED) when one is enrolled,
    and the next run resumes from the completed job through the digest seam."""

    PNG = b"\x89PNG\r\n\x1a\nFAKE-IMAGE-BYTES"

    def setUp(self):
        import experiment
        import generation
        import worker
        self.experiment, self.generation, self.worker = experiment, generation, worker
        self.video_id = f"pytest-gpu-{uuid.uuid4().hex[:8]}"
        self.pdir = ROOT / "projects" / self.video_id
        self.addCleanup(lambda: shutil.rmtree(self.pdir, ignore_errors=True))
        self.tmp = Path(tempfile.mkdtemp(prefix="cm-deferral-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self._jobs_dir = generation.JOBS_DIR
        self._provider_state = generation.PROVIDER_STATE_PATH
        generation.JOBS_DIR = self.tmp / "jobs"
        generation.PROVIDER_STATE_PATH = generation.JOBS_DIR / "_provider_state.json"
        self._env = {k: os.environ.pop(k, None) for k in (
            "COMFYUI_URL", "GEMINI_IMAGE_ENABLED", "IMAGE_API_URL", "IMAGE_API_KEY",
            "GENERATION_ORDER", "TEST_MODE")}
        os.environ["TEST_MODE"] = "1"

    def tearDown(self):
        self.generation.JOBS_DIR = self._jobs_dir
        self.generation.PROVIDER_STATE_PATH = self._provider_state
        for key, value in self._env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    def scaffold(self):
        self.experiment.scaffold_project("ref-moonlit-victorian-glasshouse", self.video_id,
                                         duration=20)
        meta = json.loads((self.pdir / "metadata.json").read_text())
        meta["visual_plan"] = {"prompt": "moonlit glasshouse", "negative_prompt": "text",
                               "style": "deep-night"}
        (self.pdir / "metadata.json").write_text(json.dumps(meta, indent=2) + "\n")
        return meta

    def render_remotely(self, job_id):
        """What the PC agent would do, minus the GPU: claim, upload, complete."""
        worker = self.worker
        worker.heartbeat("home-gpu-01", {"comfyui": "reachable", "comfyui_reachable": True})
        job, reason = worker.claim("home-gpu-01")
        self.assertEqual(reason, "CLAIMED")
        self.assertEqual(job["job_id"], job_id)
        lease = job["lease"]["lease_id"]
        worker.report_progress(job_id, "home-gpu-01", lease, worker.SUBMITTED)
        worker.report_progress(job_id, "home-gpu-01", lease, worker.RUNNING)
        worker.report_progress(job_id, "home-gpu-01", lease, worker.UPLOADING)
        digest = hashlib.sha256(self.PNG).hexdigest()
        worker.stage_asset(job_id, "home-gpu-01", lease, "content-machine_0001.png",
                           self.PNG, digest)
        return worker.complete(job_id, "home-gpu-01", lease,
                               [{"filename": "content-machine_0001.png", "sha256": digest,
                                 "bytes": len(self.PNG)}],
                               provider_job_id="prompt-1", model="test.safetensors")

    def test_visuals_fail_when_no_worker_is_enrolled(self):
        self.scaffold()
        result = project.run_visuals(self.video_id)
        self.assertFalse(result.ok)
        self.assertEqual(result.exit_code, 1)
        self.assertEqual(self.worker.load_jobs(), [])

    def test_visuals_queue_for_the_gpu_worker_and_resume_once_it_lands(self):
        self.scaffold()
        self.worker.enroll("home-gpu-01", ("comfyui",))
        result = project.run_visuals(self.video_id)
        self.assertFalse(result.ok)
        self.assertEqual(result.exit_code, EXIT_NEEDS_ATTENTION, result.message)
        self.assertTrue(result.data["waiting_for_gpu"])
        self.assertIn("waiting for GPU worker", result.message)
        self.assertEqual(result.data["gpu_state"], self.worker.GPU_OFFLINE)
        jobs = self.worker.jobs_for_project(self.video_id)
        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0]["wait_reason"], self.worker.WAITING_FOR_CAPABLE_WORKER)
        self.assertEqual(jobs[0]["attempt"], 0)
        meta = json.loads((self.pdir / "metadata.json").read_text())
        self.assertEqual(meta["status"]["visuals"], project.VISUALS_WAITING_FOR_GPU)
        self.assertIsNone(meta["provenance"]["images"].get("production_grade"))

        # Re-running while it is still queued queues nothing new.
        again = project.run_visuals(self.video_id)
        self.assertEqual(again.exit_code, EXIT_NEEDS_ATTENTION)
        self.assertEqual(len(self.worker.load_jobs()), 1)

        # The PC renders it; the next run reuses the completed job.
        self.render_remotely(jobs[0]["job_id"])
        resumed = project.run_visuals(self.video_id)
        self.assertTrue(resumed.ok, resumed.message)
        meta = json.loads((self.pdir / "metadata.json").read_text())
        images = meta["provenance"]["images"]
        self.assertEqual(images["provider"], "comfyui")
        self.assertEqual(images["job_id"], jobs[0]["job_id"])
        self.assertIsNone(images["production_grade"], "a machine never affirms the claim")
        self.assertEqual(meta["status"]["visuals"], "OK")
        assets = project.project_assets(self.video_id)
        self.assertEqual(len(assets["images"]), 1)
        lineage = assets["images"][0]["generation"]
        self.assertEqual(lineage["provider"], "comfyui")
        self.assertEqual(lineage["worker_id"], "home-gpu-01")
        self.assertEqual(lineage["prompt"], "moonlit glasshouse")
        self.assertEqual(assets["visual_plan"]["prompt"], "moonlit glasshouse")
        self.assertIsNone(assets["images_provenance"]["production_grade"])

    def test_scenes_queue_one_job_per_unresolved_scene(self):
        self.scaffold()
        self.worker.enroll("home-gpu-01", ("comfyui",))
        board = project.run_storyboard(self.video_id, scene_count=2)
        self.assertTrue(board.ok, board.message)
        result = project.run_scenes(self.video_id)
        self.assertEqual(result.exit_code, EXIT_NEEDS_ATTENTION, result.message)
        self.assertEqual(len(result.data["remote_jobs"]), 2)
        storyboard = json.loads((self.pdir / "storyboard.json").read_text())
        for scene in storyboard["scenes"]:
            self.assertIn(scene["generation"]["job_id"], result.data["remote_jobs"])
            self.assertEqual(scene["generation"]["remote_state"], self.worker.QUEUED)
            self.assertIsNone(scene.get("image"))
        summary = project.project_assets(self.video_id)["storyboard"]["scenes"]
        self.assertTrue(all(s["job_id"] for s in summary))

    def test_a_failed_remote_scene_job_blocks_rather_than_waits(self):
        self.scaffold()
        self.worker.enroll("home-gpu-01", ("comfyui",))
        board = project.run_storyboard(self.video_id, scene_count=2)
        self.assertTrue(board.ok, board.message)
        first = project.run_scenes(self.video_id)
        self.assertEqual(first.exit_code, EXIT_NEEDS_ATTENTION, first.message)
        job_ids = first.data["remote_jobs"]

        # The worker takes one and fails it for good (a VRAM ceiling).
        self.worker.heartbeat("home-gpu-01", {"comfyui": "reachable"})
        job, _ = self.worker.claim("home-gpu-01")
        self.worker.report_failure(job["job_id"], "home-gpu-01",
                                   job["lease"]["lease_id"],
                                   "torch.OutOfMemoryError: CUDA out of memory",
                                   permanent=True)

        # Re-running does not pretend the failed scene is on its way.
        again = project.run_scenes(self.video_id)
        self.assertFalse(again.ok)
        self.assertEqual(again.exit_code, 1, again.message)
        self.assertEqual(again.data["failed"], 1)
        self.assertEqual(again.data["queued"], 1)
        self.assertEqual(len(self.worker.load_jobs()), 2, "nothing new was queued")
        storyboard = json.loads((self.pdir / "storyboard.json").read_text())
        states = {s["generation"]["job_id"]: s["generation"]["remote_state"]
                  for s in storyboard["scenes"]}
        self.assertEqual(states[job["job_id"]], self.worker.FAILED)

        # An operator's requeue puts it back, and the stage waits again.
        self.worker.requeue(job["job_id"], reason="capacity fixed")
        resumed = project.run_scenes(self.video_id)
        self.assertEqual(resumed.exit_code, EXIT_NEEDS_ATTENTION, resumed.message)
        self.assertEqual(sorted(resumed.data["remote_jobs"]), sorted(job_ids))

    def test_visual_grade_is_an_explicit_human_claim(self):
        self.scaffold()
        with self.assertRaises(project.ReviewDecisionError):
            project.record_visual_grade(self.video_id, "", True)
        with self.assertRaises(project.ReviewDecisionError):
            project.record_visual_grade(self.video_id, "owner@example.com", True)   # no images
        entry = project.record_visual_grade(self.video_id, "owner@example.com", False,
                                            notes="plates only")
        self.assertFalse(entry["production_grade"])
        (self.pdir / "images").mkdir(exist_ok=True)
        (self.pdir / "images" / "gen_1.png").write_bytes(self.PNG)
        entry = project.record_visual_grade(self.video_id, "owner@example.com", True)
        meta = json.loads((self.pdir / "metadata.json").read_text())
        self.assertTrue(meta["provenance"]["images"]["production_grade"])
        self.assertEqual(meta["provenance"]["images"]["production_grade_claim"]["reviewer"],
                         "owner@example.com")
        self.assertEqual(entry["asset_count"], 1)


class CreativeQualityBlockersTest(unittest.TestCase):
    """Technical validity (qc_status) and production quality are kept
    distinguishable: an obvious, unplanned creative-quality defect must
    still block READY_FOR_REVIEW even when qc_status is PASS - see
    project._creative_quality_blockers."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.pdir = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def _scenes(self, n, prompt):
        return [{"scene_id": f"s{i:02d}", "image_prompt": prompt} for i in range(n)]

    def test_unplanned_identical_scenes_block_review(self):
        storyboard = {"scenes": self._scenes(6, "a beige living room")}
        blocking = project._creative_quality_blockers(self.pdir, storyboard, {})
        self.assertTrue(any("identical image prompt" in b for b in blocking))

    def test_deliberate_single_environment_choice_does_not_block(self):
        scenes = self._scenes(6, "one held establishing shot")
        storyboard = {"scenes": scenes,
                     "scene_motifs": {s["scene_id"]: "x" for s in scenes}}
        blocking = project._creative_quality_blockers(self.pdir, storyboard, {})
        self.assertEqual(blocking, [])

    def test_flat_unvaried_audio_blocks_review(self):
        audio_manifest = {"quality": {"warnings": [
            "single static layer with no fades - likely to read as a "
            "monotonous tone rather than a designed soundscape"]}}
        blocking = project._creative_quality_blockers(self.pdir, None, audio_manifest)
        self.assertTrue(any("monotonous tone" in b for b in blocking))

    def test_healthy_storyboard_and_audio_produce_no_blockers(self):
        scenes = self._scenes(8, "")
        for i, scene in enumerate(scenes):
            scene["image_prompt"] = f"environment {i % 4}"
        storyboard = {"scenes": scenes}
        audio_manifest = {"quality": {"warnings": []}}
        blocking = project._creative_quality_blockers(self.pdir, storyboard, audio_manifest)
        self.assertEqual(blocking, [])

    def test_gate_blockers_surfaces_creative_quality_alongside_the_production_grade_claim(self):
        """Both the administrative "no decision yet" blocker and the
        substantive detected defect must be visible together, not one
        hiding the other."""
        metadata = {"selected_title": "T", "description": "D",
                    "provenance": {"images": {}}}
        storyboard = {"scenes": self._scenes(6, "a beige living room")}
        blocking = project.gate_blockers(
            self.pdir, metadata, "PASS", [], True, {}, storyboard=storyboard)
        self.assertTrue(any("production_grade is not set" in b for b in blocking))
        self.assertTrue(any("identical image prompt" in b for b in blocking))
