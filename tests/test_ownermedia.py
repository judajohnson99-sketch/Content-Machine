#!/usr/bin/env python3
"""The owner's own media, from the catalog to a reviewable deliverable.

Real files, real ffmpeg, no external service: a small library is scanned and
annotated, assigned to a production's visuals and audio roles, and then put
through the ordinary scenes -> audio -> render -> QC -> gate path. What these
tests are really defending is that selecting your own media does not weaken
anything: no provider is called behind your back, no rights are assumed, the
staged bytes are re-verified, and nothing acquires a production-grade claim
that a person did not make.
"""
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import kdenlive as kdenlive_mod  # noqa: E402
import media  # noqa: E402
import ownermedia  # noqa: E402
import project  # noqa: E402
import storyboard as storyboard_mod  # noqa: E402

FFMPEG = shutil.which("ffmpeg") and shutil.which("ffprobe")

RIGHTS = {
    "source": "Shot by the owner",
    "license": "Owner's own work",
    "commercial_use": True,
    "evidence": "Created by the owner; unit-test declaration",
}


class AudioPlanTests(unittest.TestCase):
    """Plan surgery only - no composition, so no ffmpeg needed."""

    def plan(self):
        return {
            "target_seconds": 60.0,
            "layers": [
                {"id": "bed", "provider": "pad", "gain_db": -3.0,
                 "fade_in_seconds": 4.0, "params": {"root_hz": 110}},
                {"id": "room", "provider": "ambience", "gain_db": -9.0, "params": {}},
                {"id": "voice", "provider": "tts", "gain_db": 0.0,
                 "duck_under": "bed", "params": {"text": "hello"}},
            ],
        }

    def metadata(self, **roles):
        selection = {"version": 1, "actor": "owner@example.com"}
        for role, ids in roles.items():
            selection[role] = [
                {"asset_id": identity, "role": role, "kind": "audio",
                 "staged_path": f"owner-media/{role}-{identity}.wav",
                 "description": "a track", "source": "owner", "rights": dict(RIGHTS),
                 "technical": {}, "selected_utc": "2026-10-07T00:00:00Z"}
                for identity in ids
            ]
        return {"owner_media": selection}

    def test_owner_music_replaces_the_synthesised_bed_and_inherits_its_mix(self):
        plan, applied = ownermedia.apply_audio_plan(
            self.plan(), self.metadata(music=["a" * 64]), Path("/tmp/project"))
        providers = [layer["provider"] for layer in plan["layers"]]
        self.assertNotIn("pad", providers, "the synthesised bed is gone")
        self.assertIn("ambience", providers, "an unselected role is untouched")
        owner = next(l for l in plan["layers"] if l["id"] == "owner-music")
        self.assertEqual(owner["provider"], "file")
        self.assertEqual(owner["gain_db"], -3.0, "the replaced layer's level is kept")
        self.assertEqual(owner["fade_in_seconds"], 4.0)
        self.assertEqual(owner["params"]["license"]["commercial_use"], True)
        self.assertEqual(applied["music"]["replaced"], ["bed"])
        self.assertEqual(applied["music"]["added"], ["owner-music"])

    def test_a_ducking_layer_follows_the_bed_it_ducks_under(self):
        plan, _ = ownermedia.apply_audio_plan(
            self.plan(), self.metadata(music=["b" * 64]), Path("/tmp/project"))
        voice = next(l for l in plan["layers"] if l["id"] == "voice")
        self.assertEqual(voice["duck_under"], "owner-music",
                         "a plan may never name a layer that no longer exists")

    def test_no_selection_leaves_the_plan_exactly_as_it_was(self):
        original = self.plan()
        plan, applied = ownermedia.apply_audio_plan(original, {}, Path("/tmp/project"))
        self.assertEqual(plan, original)
        self.assertEqual(applied, {})


class SceneAssignmentTests(unittest.TestCase):
    def entries(self, count):
        return [{"asset_id": f"{i}" * 64, "role": "visuals", "kind": "image",
                 "staged_path": f"owner-media/visuals-{i}.png", "description": f"shot {i}",
                 "source": "owner", "rights": dict(RIGHTS), "technical": {},
                 "selected_utc": "2026-10-07T00:00:00Z"}
                for i in range(1, count + 1)]

    def scenes(self, count):
        return [{"scene_id": f"s{i:02d}"} for i in range(1, count + 1)]

    def test_a_small_pool_is_dealt_in_order_and_then_cycles(self):
        assignment = ownermedia.assignment_for_scenes(
            {"owner_media": {"visuals": self.entries(2)}}, self.scenes(5))
        self.assertEqual(
            [assignment[f"s{i:02d}"]["description"] for i in range(1, 6)],
            ["shot 1", "shot 2", "shot 1", "shot 2", "shot 1"])

    def test_an_entry_pinned_to_a_scene_goes_to_that_scene(self):
        entries = self.entries(2)
        entries[1]["scene_id"] = "s01"
        assignment = ownermedia.assignment_for_scenes(
            {"owner_media": {"visuals": entries}}, self.scenes(3))
        self.assertEqual(assignment["s01"]["description"], "shot 2")
        self.assertEqual(assignment["s02"]["description"], "shot 1")

    def test_nothing_selected_assigns_nothing(self):
        self.assertEqual(ownermedia.assignment_for_scenes({}, self.scenes(3)), {})


@unittest.skipUnless(FFMPEG, "ffmpeg required")
class OwnerMediaProductionTests(unittest.TestCase):
    """Catalog -> selection -> scenes -> audio -> render -> gate, for real."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.library = self.base / "my footage"
        self.library.mkdir(parents=True)
        self.catalog = self.base / "catalog.json"

        fixtures = sorted((ROOT / "tests/fixtures/images").glob("*.png"))
        self.images = []
        for index, fixture in enumerate(fixtures[:2], start=1):
            target = self.library / f"still-{index}.png"
            # Deliberately not the storyboard's generation size: the owner's
            # picture is whatever they shot, and the renderer fits it.
            subprocess.run(
                ["ffmpeg", "-y", "-v", "error", "-i", str(fixture),
                 "-vf", f"scale={640 + index * 80}:{360 + index * 40}", str(target)],
                check=True)
            self.images.append(target)
        self.clip = self.library / "handheld.mp4"
        subprocess.run(
            ["ffmpeg", "-y", "-v", "error", "-f", "lavfi",
             "-i", "testsrc=size=320x180:rate=12:duration=2",
             "-pix_fmt", "yuv420p", str(self.clip)], check=True)
        self.music = self.library / "my-track.wav"
        shutil.copy2(ROOT / "tests/fixtures/audio/test_tone.wav", self.music)

        for module, attribute in ((project, "PROJECTS_DIR"), (project, "ROOT"),
                                  (storyboard_mod, "PROJECTS_DIR")):
            previous = getattr(module, attribute)
            setattr(module, attribute,
                    self.base if attribute == "ROOT" else self.base / "projects")
            self.addCleanup(setattr, module, attribute, previous)

        report = media.scan(self.library, self.catalog)
        self.assertEqual(report["issues"], [], report)
        self.video_id = "owner-media-proof"

    def annotate_all(self, rights=RIGHTS):
        for asset in media.search(catalog=self.catalog):
            media.annotate(asset["id"], description=f"Inspected {asset['technical']['kind']}",
                           tags=["fixture"], origin="owner",
                           source="tests/fixtures", rights=dict(rights) if rights else None,
                           catalog=self.catalog)

    def identity(self, path):
        return media.digest(path)

    def scaffold(self, scene_count=2, duration=4.0, fps=12):
        pdir = project.project_dir(self.video_id)
        for sub in project.SUBDIRS:
            (pdir / sub).mkdir(parents=True, exist_ok=True)
        metadata = project.build_metadata(
            self.video_id, "Owner media proof", "a test concept", "testers",
            duration, "320x180", fps)
        metadata["description"] = "Fixture media, not a real production"
        metadata["script"] = "One sentence. Another sentence."
        metadata["visual_plan"] = {"prompt": "a dim still", "negative_prompt": "text",
                                   "style": "deep-night"}
        metadata["audio_plan"] = {
            "direction": {"kind": "music", "chosen": {"source": "synthesised pad",
                                                      "production_grade_capable": False}},
            "composition": {"layers": [
                {"id": "bed", "provider": "pad", "gain_db": -6.0,
                 "params": {"root_hz": 110}},
                {"id": "room", "provider": "ambience", "gain_db": -12.0, "params": {}},
            ]},
        }
        spec = {"width": 320, "height": 180, "fps": fps, "duration_seconds": duration,
                "images": {"source_dir": "images"}, "audio": {"file": "audio/track.wav"}}
        (pdir / "video_spec.json").write_text(json.dumps(spec, indent=2) + "\n")
        (pdir / "metadata.json").write_text(json.dumps(metadata, indent=2) + "\n")
        board = storyboard_mod.build_storyboard(
            self.video_id, metadata, spec, scene_count=scene_count)
        storyboard_mod.save(self.video_id, board)
        return pdir

    def select(self, **roles):
        return project.set_owner_media(self.video_id, roles, "owner@example.com",
                                       catalog=self.catalog)

    def metadata(self):
        return json.loads((project.project_dir(self.video_id) / "metadata.json").read_text())

    # --- browsing -------------------------------------------------------

    def test_an_unannotated_asset_says_what_it_needs_before_it_can_be_used(self):
        entries = ownermedia.browse(catalog=self.catalog)
        self.assertEqual(len(entries), 4)
        self.assertTrue(all(not e["selectable"] for e in entries))
        first = entries[0]
        self.assertTrue(any("description" in p for p in first["problems"]))
        self.assertTrue(any("rights" in p for p in first["problems"]))
        self.assertTrue(first["available"], "the bytes are here; the claims are not")

        self.annotate_all()
        entries = ownermedia.browse(catalog=self.catalog)
        self.assertTrue(all(e["selectable"] for e in entries), entries)
        kinds = {e["kind"] for e in entries}
        self.assertEqual(kinds, {"image", "video", "audio"})
        clip = next(e for e in entries if e["kind"] == "video")
        self.assertEqual(clip["roles"], ["visuals"])
        self.assertAlmostEqual(clip["technical"]["duration_seconds"], 2.0, delta=0.3)

    def test_audio_without_cleared_rights_cannot_be_selected(self):
        self.annotate_all(rights={**RIGHTS, "commercial_use": False})
        self.scaffold()
        with self.assertRaisesRegex(project.ProjectError, "rights"):
            self.select(music=[self.identity(self.music)])
        self.assertEqual(project.owner_media_view(self.video_id)["roles"]["music"]["count"], 0)

    def test_a_role_refuses_the_wrong_kind_of_media(self):
        self.annotate_all()
        self.scaffold()
        with self.assertRaisesRegex(project.ProjectError, "Music takes audio"):
            self.select(music=[self.identity(self.images[0])])

    # --- selection ------------------------------------------------------

    def test_selecting_stages_the_bytes_and_marks_the_stages_stale(self):
        self.annotate_all()
        self.scaffold()
        result = self.select(visuals=[self.identity(i) for i in self.images],
                             music=[self.identity(self.music)])
        self.assertEqual(sorted(result["changed_roles"]), ["music", "visuals"])
        self.assertEqual(result["stale_stages"], ["audio", "scenes"])
        selection = ownermedia.selection_of(self.metadata())
        staged = [project.project_dir(self.video_id) / e["staged_path"]
                  for e in selection["visuals"] + selection["music"]]
        self.assertTrue(all(p.is_file() for p in staged))
        for entry, path in zip(selection["visuals"] + selection["music"], staged):
            self.assertEqual(media.digest(path), entry["asset_id"],
                             "the staged copy is the asset, by content")
        self.assertEqual(self.metadata()["status"]["scenes"], "PENDING")

        # The owner's originals are never touched by any of this.
        self.assertTrue(all(p.is_file() for p in [*self.images, self.music, self.clip]))

    def test_selecting_the_same_asset_twice_in_one_role_is_refused(self):
        self.annotate_all()
        self.scaffold()
        identity = self.identity(self.images[0])
        with self.assertRaisesRegex(project.ProjectError, "twice"):
            self.select(visuals=[identity, identity])

    # --- the whole path -------------------------------------------------

    def test_owner_stills_and_music_reach_the_rendered_deliverable(self):
        self.annotate_all()
        pdir = self.scaffold()
        self.select(visuals=[self.identity(i) for i in self.images],
                    music=[self.identity(self.music)])

        # No image provider is configured in this environment, so a scene
        # the owner did not fill would fail here. Every scene is theirs.
        scenes = project.run_scenes(self.video_id)
        self.assertTrue(scenes.ok, scenes.message)
        self.assertEqual(scenes.data["generated"], 0, "nothing was generated")
        board = storyboard_mod.load(self.video_id)
        for scene in board["scenes"]:
            self.assertTrue(ownermedia.is_owner_scene(scene))
            self.assertTrue(scene["image"].startswith("owner-media/"))
            self.assertTrue((pdir / scene["image"]).is_file())
            self.assertEqual(scene["generation"]["provider"], "owner-media")
        images_prov = self.metadata()["provenance"]["images"]
        self.assertIn("owner-media", images_prov["provider"])
        self.assertIsNone(images_prov["production_grade"],
                          "no machine ever affirms the claim")

        audio = project.run_audio(self.video_id)
        self.assertTrue(audio.ok, audio.message)
        manifest = json.loads((pdir / "audio" / "audio_manifest.json").read_text())
        providers = {layer["provider"] for layer in manifest["layers"]}
        self.assertIn("file", providers, "the owner's track is a layer")
        self.assertNotIn("pad", providers, "the synthesised bed was replaced")
        self.assertIn("ambience", providers, "the unselected role still generates")
        self.assertTrue(manifest["commercial_use_cleared"])
        self.assertEqual(
            [l["layer_id"] for l in manifest["layers"] if l["provider"] == "file"],
            ["owner-music"])

        rendered = project.run_pipeline(self.video_id)
        video = pdir / "output" / f"{self.video_id}.mp4"
        self.assertTrue(video.is_file(), rendered.message)
        report = json.loads((pdir / "output" / "qc_report.json").read_text())
        self.assertEqual(report["status"], "PASS", report["failures"])

        # The gate still fails closed: the owner chose the media, which is
        # not the same as a person saying the result is production-grade.
        status = project.status_report(self.video_id)
        self.assertTrue(any("production_grade is not set" in b for b in status["blocking"]),
                        status["blocking"])
        # The audio blocker describes what is actually in the file - the
        # owner's own track - rather than the synthesised bed it replaced.
        audio_blocker = next(b for b in status["blocking"] if "audio" in b)
        self.assertIn("your own music", audio_blocker)
        self.assertNotIn("synthesised", audio_blocker)

        # And the reviewer can see what was used, as files they can open.
        assets = project.project_assets(self.video_id)
        visuals = assets["owner_media"]["roles"]["visuals"]
        self.assertEqual(visuals["count"], 2)
        self.assertTrue(all(e["file"] for e in visuals["entries"]))
        self.assertEqual(assets["owner_media"]["problems"], [])

    def test_owner_footage_renders_as_a_clip_and_exports_an_editable_edit(self):
        self.annotate_all()
        pdir = self.scaffold(scene_count=2, duration=6.0)
        self.select(visuals=[self.identity(self.clip), self.identity(self.images[0])],
                    music=[self.identity(self.music)])
        self.assertTrue(project.run_scenes(self.video_id).ok)
        self.assertTrue(project.run_audio(self.video_id).ok)

        board = storyboard_mod.load(self.video_id)
        footage = board["scenes"][0]
        self.assertEqual(footage["source"]["media_kind"], "video")
        self.assertEqual(footage["motion"]["kind"], "static",
                         "footage carries its own movement")

        result = project.run_pipeline(self.video_id)
        video = pdir / "output" / f"{self.video_id}.mp4"
        self.assertTrue(video.is_file(), result.message)
        report = json.loads((pdir / "output" / "qc_report.json").read_text())
        self.assertEqual(report["status"], "PASS", report["failures"])

        editable = project.build_kdenlive_project(self.video_id, render_output=False)
        project_file = pdir / "output" / f"{self.video_id}.kdenlive"
        self.assertTrue(project_file.is_file(), editable.message)
        xml = project_file.read_text()
        self.assertIn("avformat", xml, "footage is a decoded clip, not a still")
        self.assertIn("pixbuf", xml, "the still is still a still")
        # A 2s clip under a longer shot is laid down as repeats, not as one
        # entry claiming footage that does not exist.
        self.assertGreater(editable.data["timeline"]["clip_count"],
                           len(board["scenes"]) + 1)
        kdenlive_mod.verify_project(project_file)

    def test_bytes_that_changed_after_selection_block_review(self):
        self.annotate_all()
        pdir = self.scaffold()
        self.select(visuals=[self.identity(i) for i in self.images])
        staged = pdir / ownermedia.selection_of(self.metadata())["visuals"][0]["staged_path"]
        staged.unlink()
        shutil.copy2(self.images[1], staged)      # a different picture, same name

        problems = ownermedia.verification_problems(self.metadata(), pdir)
        self.assertEqual(len(problems), 1, problems)
        self.assertIn("no longer matches", problems[0])
        blocking = project.gate_blockers(pdir, self.metadata(), "PASS", [], True, {})
        self.assertTrue(any("no longer matches" in b for b in blocking))

    def test_deselecting_hands_the_scene_back_to_the_generators(self):
        self.annotate_all()
        self.scaffold()
        self.select(visuals=[self.identity(i) for i in self.images])
        self.assertTrue(project.run_scenes(self.video_id).ok)

        self.select(visuals=[])
        self.assertEqual(project.owner_media_view(self.video_id)["roles"]["visuals"]["count"], 0)
        # The point: they are generated work again, produced by whatever the
        # routing policy allows, not quietly left pointing at media the
        # owner removed.
        again = project.run_scenes(self.video_id)
        board = storyboard_mod.load(self.video_id)
        for scene in board["scenes"]:
            self.assertFalse(ownermedia.is_owner_scene(scene))
            self.assertNotEqual(scene["generation"].get("provider"), "owner-media")
        self.assertTrue(all(scene["image"] for scene in board["scenes"]))
        self.assertGreaterEqual(again.data["generated"], 1,
                                "the scenes are generated work again")
        self.assertIs(self.metadata()["provenance"]["images"]["production_grade"], False,
                      "a procedural fallback is decisively not production-grade")

    def test_without_a_storyboard_the_stills_become_the_cycled_image_set(self):
        self.annotate_all()
        pdir = self.scaffold()
        (pdir / "storyboard.json").unlink()
        result = self.select(visuals=[self.identity(i) for i in self.images])
        self.assertEqual(result["stale_stages"], ["visuals"],
                         "a project with no scene plan is told the stage that will run")

        visuals = project.run_visuals(self.video_id)
        self.assertTrue(visuals.ok, visuals.message)
        self.assertEqual(visuals.data["generated"], 0)
        on_disk = sorted(p.name for p in (pdir / "images").iterdir())
        self.assertEqual(len(on_disk), 2)
        images_prov = self.metadata()["provenance"]["images"]
        self.assertEqual(images_prov["provider"], "owner-media")
        self.assertIsNone(images_prov["production_grade"])

        self.assertTrue(project.run_audio(self.video_id).ok)
        rendered = project.run_pipeline(self.video_id)
        self.assertTrue((pdir / "output" / f"{self.video_id}.mp4").is_file(), rendered.message)

    def test_footage_is_refused_where_there_is_no_timeline_to_put_it_in(self):
        self.annotate_all()
        pdir = self.scaffold()
        (pdir / "storyboard.json").unlink()
        self.select(visuals=[self.identity(self.clip)])
        result = project.run_visuals(self.video_id)
        self.assertFalse(result.ok)
        self.assertIn("timeline", result.message)

    def test_an_audio_only_selection_leaves_the_visuals_generated(self):
        self.annotate_all()
        self.scaffold()
        result = self.select(music=[self.identity(self.music)])
        self.assertEqual(result["stale_stages"], ["audio"])
        self.assertEqual(
            ownermedia.assignment_for_scenes(self.metadata(),
                                             storyboard_mod.load(self.video_id)["scenes"]),
            {})


if __name__ == "__main__":
    unittest.main()
