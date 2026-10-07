"""Real probe/decode and catalog-to-render handoff, with no external services."""
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import media
import project
import render


@unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "ffmpeg required")
class MediaTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.source = self.base / "owner originals"
        (self.source / "nested").mkdir(parents=True)
        fixtures = sorted((ROOT / "tests/fixtures/images").glob("*.png"))
        self.image = self.source / "misleading-name.png"
        self.second = self.source / "nested" / "misleading-name.png"
        shutil.copy2(fixtures[0], self.image)
        shutil.copy2(fixtures[1], self.second)
        self.audio = self.source / "nested" / "audio.wav"
        shutil.copy2(ROOT / "tests/fixtures/audio/test_tone.wav", self.audio)
        self.catalog = self.base / "library/catalog.json"
        self.old_projects = project.PROJECTS_DIR
        project.PROJECTS_DIR = self.base / "projects"
        self.addCleanup(setattr, project, "PROJECTS_DIR", self.old_projects)
        self.old_root = project.ROOT
        project.ROOT = self.base
        self.addCleanup(setattr, project, "ROOT", self.old_root)

    def scan(self):
        return media.scan(self.source, self.catalog)

    def annotate_all(self):
        for i, asset in enumerate(media.search(catalog=self.catalog)):
            media.annotate(asset["id"], description="Inspected test fixture",
                           tags=["fixture", "calm"], origin="owner" if i == 0 else "generated",
                           source="tests/fixtures", rights={
                               "source": "tests/fixtures", "license": "Test fixture only",
                               "commercial_use": True, "evidence": "Unit test declaration",
                           }, catalog=self.catalog)

    def create_project(self, **kwargs):
        images = media.search(kind="image", catalog=self.catalog)
        audio = media.search(kind="audio", catalog=self.catalog)[0]
        return project.import_catalog_project(
            "catalog-proof", [a["id"] for a in images], audio["id"],
            title="Integration test", description="Fixture media, not a real Dreamdrip production",
            catalog=self.catalog, width=320, height=180, fps=12,
            duration=4, transition_seconds=0.5, **kwargs)

    def test_recursive_scan_is_non_destructive_and_does_not_infer_content(self):
        before = {p: (media.digest(p), p.stat().st_mtime_ns) for p in self.source.rglob("*") if p.is_file()}
        report = self.scan()
        self.assertEqual(report, {"indexed": 3, "issues": []})
        assets = media.search(catalog=self.catalog)
        self.assertEqual(len(assets), 3)
        self.assertEqual({a["technical"]["kind"] for a in assets}, {"image", "audio"})
        self.assertTrue(all(a["technical"]["inspection"] == "full_decode" for a in assets))
        self.assertTrue(all(not a["description"] and a["rights"] is None for a in assets))
        self.assertEqual(media.search(query="misleading", catalog=self.catalog), [])
        self.assertEqual(before, {p: (media.digest(p), p.stat().st_mtime_ns) for p in before})

    def test_duplicate_alias_missing_source_and_content_change(self):
        self.scan()
        self.annotate_all()
        alias = self.source / "duplicate.png"
        shutil.copy2(self.image, alias)
        self.scan()
        self.assertEqual(len(media.search(catalog=self.catalog)), 3)
        asset = media.load(self.catalog)["assets"][media.digest(self.image)]
        self.assertEqual(len(asset["locations"]), 2)
        self.image.unlink()
        self.assertEqual(media.resolve(asset), alias)
        alias.write_bytes(b"changed")
        with self.assertRaisesRegex(media.MediaError, "changed"):
            media.resolve(asset)
        self.assertTrue(any(r["status"] == "UNAVAILABLE" for r in media.check(self.catalog)))

    def test_corrupt_and_unsupported_sources_are_reported(self):
        (self.source / "corrupt.mp4").write_bytes(b"not a video")
        (self.source / "license.txt").write_text("source notes")
        report = self.scan()
        self.assertEqual(report["indexed"], 3)
        self.assertEqual(len(report["issues"]), 2)
        self.assertIn("probe failed", str(report))
        self.assertIn("unsupported extension", str(report))

    def test_video_stream_metadata_and_renderer_boundary(self):
        video = self.source / "clip.mp4"
        subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i",
                        "testsrc2=size=160x90:rate=12", "-t", "0.5", "-c:v", "libx264", str(video)], check=True)
        self.scan()
        asset = media.search(kind="video", catalog=self.catalog)[0]
        self.assertEqual(asset["technical"]["streams"][0]["width"], 160)
        self.assertAlmostEqual(float(asset["technical"]["duration_seconds"]), 0.5)
        with self.assertRaisesRegex(media.MediaError, "expected image"):
            media.select(asset["id"], kind="image", catalog=self.catalog)

    def test_remote_inventory_requires_selected_media_staging(self):
        self.scan()
        self.annotate_all()
        vps_catalog = self.base / "vps.json"
        with patch.object(media, "host_id", return_value="different-vps"):
            self.assertEqual(media.merge(self.catalog, vps_catalog)["media_transferred"], 0)
            self.assertTrue(all(r["status"] == "UNAVAILABLE" for r in media.check(vps_catalog)))
            # Even an identical path on the VPS is not silently treated as local.
            first = media.search(catalog=vps_catalog)[0]
            with self.assertRaisesRegex(media.MediaError, "remote source needs staging"):
                media.resolve(first)
            # Receiving and scanning selected bytes adds a local location, preserving annotations.
            media.scan(self.image, vps_catalog)
            asset = media.load(vps_catalog)["assets"][media.digest(self.image)]
            self.assertEqual(media.resolve(asset), self.image)
            self.assertEqual(asset["description"], "Inspected test fixture")
            self.assertEqual(len(asset["locations"]), 2)

    def test_rights_unknown_or_string_true_cannot_be_selected(self):
        self.scan()
        with self.assertRaisesRegex(project.ProjectError, "annotation"):
            self.create_project()
        self.annotate_all()
        data = media.load(self.catalog)
        for asset in data["assets"].values():
            asset["rights"]["commercial_use"] = "true"
        media.atomic_json(self.catalog, data)
        with self.assertRaisesRegex(project.ProjectError, "rights"):
            self.create_project()
        self.assertFalse((project.PROJECTS_DIR / "catalog-proof").exists())

    def test_catalog_lock_and_atomic_failure_preserve_previous_state(self):
        self.scan()
        before = self.catalog.read_bytes()
        with media.catalog_lock(self.catalog):
            with self.assertRaisesRegex(media.MediaError, "Another operation"):
                self.scan()
        with patch.object(media.os, "replace", side_effect=OSError("interrupted")):
            with self.assertRaises(OSError):
                media.atomic_json(self.catalog, {"new": True})
        self.assertEqual(self.catalog.read_bytes(), before)

    def test_import_preserves_existing_spec_and_retries_its_own_interruption(self):
        self.scan()
        self.annotate_all()
        pdir = project.project_dir("catalog-proof")
        pdir.mkdir(parents=True)
        spec_path = pdir / "video_spec.json"
        spec_path.write_text('{"existing": true}')
        with self.assertRaisesRegex(project.ProjectError, "Refusing to overwrite"):
            self.create_project()
        self.assertEqual(spec_path.read_text(), '{"existing": true}')
        spec_path.unlink()
        self.create_project()
        # Metadata is committed last; absence simulates interruption before commit.
        (pdir / "metadata.json").unlink()
        self.assertTrue(self.create_project().ok)

    def test_changed_imported_source_blocks_validation_and_review(self):
        self.scan()
        self.annotate_all()
        self.create_project()
        self.image.write_bytes(b"replaced")
        with self.assertRaisesRegex(project.ProjectError, "Imported source failed verification"):
            project.load_project("catalog-proof")
        pdir = project.project_dir("catalog-proof")
        metadata = json.loads((pdir / "metadata.json").read_text())
        blockers = project.gate_blockers(pdir, metadata, "PASS", [], True, None)
        self.assertTrue(any("Imported source failed verification" in b for b in blockers))

    def test_import_builds_frame_timed_render_without_copying_or_grading(self):
        self.scan()
        self.annotate_all()
        result = self.create_project()
        self.assertTrue(result.ok)
        pdir, spec, raw, metadata = project.load_project("catalog-proof")
        self.assertFalse(result.data["editable_kdenlive"])
        self.assertEqual(list((pdir / "images").iterdir()), [])
        self.assertEqual(spec["timeline_seconds"], 4)
        self.assertIsNone(metadata["provenance"]["images"]["production_grade"])
        self.assertEqual({a["origin"] for a in metadata["ingestion"]["inputs"]}, {"owner", "generated"})
        outcome = project.run_pipeline("catalog-proof")
        self.assertEqual(outcome.exit_code, 2, outcome)
        package = json.loads(Path(outcome.data["package_path"]).read_text())
        self.assertEqual(package["status"], "NEEDS_ATTENTION")
        self.assertEqual(len(package["source_assets"]), 3)
        self.assertEqual(package["qc"]["status"], "PASS")
        output = pdir / "output" / "catalog-proof.mp4"
        probe = subprocess.run(["ffprobe", "-v", "error", "-show_format", "-show_streams",
                                "-of", "json", str(output)], capture_output=True, text=True, check=True)
        actual = json.loads(probe.stdout)
        self.assertAlmostEqual(float(actual["format"]["duration"]), 4, delta=1/12)
        self.assertEqual({s["codec_type"] for s in actual["streams"]}, {"audio", "video"})
        with self.assertRaisesRegex(project.ProjectError, "already exists"):
            self.create_project()

    def test_imported_visuals_participate_in_placeholder_gate_and_digest(self):
        self.scan()
        self.annotate_all()
        self.create_project()
        pdir = project.project_dir("catalog-proof")
        self.assertEqual(set(project.scene_referenced_images(pdir)), {self.image, self.second})
        metadata = json.loads((pdir / "metadata.json").read_text())
        metadata["experiment"]["concept_id"] = "test-depicted"
        with patch.object(project, "_load_concept", return_value={"procedural_visuals_acceptable": False}), \
                patch.object(project.make_visuals, "classify", return_value=("procedural", "fixture")):
            blockers = project._visual_blockers(pdir, metadata, {"production_grade": True})
        self.assertTrue(any("not depicted" in b for b in blockers))
        before = project.gate_digest(pdir, metadata, pdir / "no-video.mp4")
        self.image.write_bytes(b"changed")
        self.assertNotEqual(before, project.gate_digest(pdir, metadata, pdir / "no-video.mp4"))

    def test_cli_reports_missing_source_and_searches_annotations(self):
        self.scan()
        self.annotate_all()
        cmd = [str(ROOT / "content-machine"), "media", "--catalog", str(self.catalog)]
        proc = subprocess.run(cmd + ["list", "--query", "calm", "--kind", "image"],
                              capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(len(json.loads(proc.stdout)), 2)
        proc = subprocess.run(cmd + ["scan", str(self.base / "missing")], capture_output=True, text=True)
        self.assertEqual(proc.returncode, 1)
        self.assertIn("not accessible", proc.stderr)

    def test_selected_source_transfer_checks_bytes_and_keeps_originals(self):
        self.scan()
        identity = media.digest(self.image)
        request = self.base / "request.json"
        media.atomic_json(request, media.transfer_request([identity], "source", catalog=self.catalog))
        bundle = self.base / "selected.tar.gz"
        media.export_bundle(request, bundle, catalog=self.catalog)
        before = self.image.stat().st_mtime_ns
        received = media.receive_bundle(bundle, request, catalog=self.catalog)
        self.assertEqual(received["received"], 1)
        target = Path(received["directory"]) / (identity + ".png")
        self.assertEqual(media.digest(target), identity)
        self.assertEqual(self.image.stat().st_mtime_ns, before)
        self.assertEqual(media.receive_bundle(bundle, request, catalog=self.catalog), received)
        self.assertEqual(len(media.load(self.catalog)["assets"][identity]["locations"]), 2)
        with self.assertRaisesRegex(media.MediaError, "outside"):
            media.export_bundle(request, self.source / "forbidden.tar.gz", catalog=self.catalog)

    def test_preview_transfer_is_traceable_but_not_an_original_source(self):
        self.scan()
        ids = [media.digest(self.image), media.digest(self.audio)]
        request = self.base / "request.json"
        media.atomic_json(request, media.transfer_request(ids, "preview", catalog=self.catalog))
        bundle = self.base / "previews.tar.gz"
        media.export_bundle(request, bundle, catalog=self.catalog)
        result = media.receive_bundle(bundle, request, catalog=self.catalog)
        self.assertEqual(result["received"], 2)
        self.assertTrue((Path(result["directory"]) / (ids[0] + ".jpg")).is_file())
        self.assertTrue(all(len(a["locations"]) == 1 for a in media.search(catalog=self.catalog)))
        media.atomic_json(request, media.transfer_request(ids, "source", catalog=self.catalog))
        with self.assertRaisesRegex(media.MediaError, "requested selection"):
            media.receive_bundle(bundle, request, catalog=self.catalog)

    def test_transfer_refuses_archive_traversal(self):
        import io
        import tarfile
        self.scan()
        request = self.base / "request.json"
        identity = media.digest(self.image)
        media.atomic_json(request, media.transfer_request([identity], "source", catalog=self.catalog))
        bundle = self.base / "bad.tar.gz"
        with tarfile.open(bundle, "w:gz") as archive:
            for name in ["manifest.json", "../escape"]:
                item = tarfile.TarInfo(name)
                item.size = 2
                archive.addfile(item, io.BytesIO(b"{}"))
        with self.assertRaisesRegex(media.MediaError, "unsafe paths"):
            media.receive_bundle(bundle, request, catalog=self.catalog)


if __name__ == "__main__":
    unittest.main()
