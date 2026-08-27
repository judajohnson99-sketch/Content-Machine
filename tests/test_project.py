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


if __name__ == "__main__":
    unittest.main(verbosity=2)
