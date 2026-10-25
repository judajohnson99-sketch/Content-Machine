#!/usr/bin/env python3
"""Unified visual asset discovery, from a scene's needs to a rendered edit.

Real ffmpeg and a real project on disk; only the outside world is replaced.
Stock providers answer from a scripted transport with the vendors' documented
response shapes and "download" by copying a generated file, because tests never
touch the network. What these tests defend is the chain a production relies on:

    library searched first -> stock and generation ranked beside it ->
    first use of external media waits for a person -> the choice becomes a
    pinned catalog asset -> scenes/audio/render/QC -> the package, the
    Review Center read model and the Kdenlive handoff all still say whose it
    is and on what terms -> the gate is exactly as strict as before.

The GPU PC is off in this environment, so generation is only ever *planned*
and *deferred* here; nothing claims a real ComfyUI render happened.
"""
import contextlib
import json
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import asset_licensing as licensing  # noqa: E402
import kdenlive as kdenlive_mod  # noqa: E402
import media  # noqa: E402
import ownermedia  # noqa: E402
import project  # noqa: E402
import stock_providers as stock  # noqa: E402
import storyboard as storyboard_mod  # noqa: E402
import visual_assets  # noqa: E402

FFMPEG = shutil.which("ffmpeg") and shutil.which("ffprobe")

OWNER_RIGHTS = {
    "source": "Shot by the owner", "license": "Owner's own work",
    "commercial_use": True, "evidence": "Created by the owner; unit-test declaration",
}

LAKE_PHOTO = {
    "page": 1, "total_results": 1, "photos": [{
        "id": 501, "width": 4000, "height": 2250,
        "url": "https://www.pexels.com/photo/quiet-lake-501/",
        "photographer": "Ada Ng", "photographer_url": "https://www.pexels.com/@ada",
        "alt": "Quiet lake at night under stars",
        "src": {"original": "https://images.pexels.com/photos/501/original.jpg",
                "large2x": "https://images.pexels.com/photos/501/large2x.jpg",
                "medium": "https://images.pexels.com/photos/501/medium.jpg"}}]}
RIVER_VIDEO = {
    "page": 1, "total_results": 1, "videos": [{
        "id": 601, "width": 1920, "height": 1080, "duration": 3,
        "url": "https://www.pexels.com/video/river-stream-flowing-601/",
        "image": "https://images.pexels.com/videos/601/preview.jpg",
        "user": {"id": 9, "name": "Bo Lind", "url": "https://www.pexels.com/@bo"},
        "video_files": [{"id": 1, "quality": "hd", "file_type": "video/mp4", "width": 1920,
                         "height": 1080, "fps": 24, "link": "https://videos.pexels.com/601/hd.mp4"}]}]}


class Scripted:
    def __init__(self, routes):
        self.routes, self.calls = routes, []

    def __call__(self, url, headers=None, timeout=20):
        self.calls.append(url)
        for needle, answer in self.routes:
            if needle in url:
                status, body, hdrs = answer if isinstance(answer, tuple) else (200, answer, {})
                return stock.Response(status, hdrs, json.dumps(body).encode())
        return stock.Response(404, {}, b"{}")


class FakeHub(stock.StockHub):
    """The real hub (cache, cooldowns, parsing) with only the byte fetch faked."""

    def __init__(self, directory, routes, files, providers=None):
        super().__init__(providers=providers or [stock.Pexels(), stock.Pixabay()],
                         directory=directory, transport=Scripted(routes), sleep=lambda s: None)
        self.files, self.downloads = files, []

    def download(self, candidate, directory):
        self.downloads.append(candidate["candidate_id"])
        source = Path(self.files[candidate["external_id"]])
        Path(directory).mkdir(parents=True, exist_ok=True)
        target = Path(directory) / f"{candidate['provider']}-{candidate['external_id']}{source.suffix}"
        shutil.copy2(source, target)
        return target


def textured_image(path, hue=0, size="1920x1080"):
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", f"testsrc2=size={size}:rate=1",
                    "-vf", f"hue=h={hue}", "-frames:v", "1", str(path)], check=True)
    return Path(path)


def footage(path, seconds=3):
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi",
                    "-i", f"testsrc2=size=1280x720:rate=24:duration={seconds}",
                    "-pix_fmt", "yuv420p", str(path)], check=True)
    return Path(path)


GPU_OFF = {"state": "deferred", "where": None, "detail": "No GPU worker is enrolled"}
GPU_ON = {"state": "ready", "where": "remote", "detail": "worker-1 is online"}


@unittest.skipUnless(FFMPEG, "ffmpeg required")
class VisualAssetCase(unittest.TestCase):
    video_id = "visual-assets-proof"

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.catalog = self.base / "library" / "catalog.json"
        self.work = self.base / "work"
        self.work.mkdir()
        for module, attribute, value in ((project, "PROJECTS_DIR", self.base / "projects"),
                                         (project, "ROOT", self.base),
                                         (storyboard_mod, "PROJECTS_DIR", self.base / "projects")):
            previous = getattr(module, attribute)
            setattr(module, attribute, value)
            self.addCleanup(setattr, module, attribute, previous)
        patcher = mock.patch.dict(os.environ, {"PEXELS_API_KEY": "pk", "PIXABAY_API_KEY": ""})
        patcher.start()
        self.addCleanup(patcher.stop)
        gpu = mock.patch.object(visual_assets, "gpu_state", return_value=GPU_OFF)
        self.gpu = gpu.start()
        self.addCleanup(gpu.stop)
        self.files = {
            "501": textured_image(self.work / "lake.jpg", hue=200),
            "601": footage(self.work / "river.mp4"),
        }
        self.routes = [("api.pexels.com/videos", RIVER_VIDEO), ("api.pexels.com/v1", LAKE_PHOTO)]

    # -- fixtures --------------------------------------------------------

    def hub(self, routes=None, providers=None):
        return FakeHub(self.base / "stock", routes if routes is not None else self.routes,
                       self.files, providers=providers)

    def own_still(self, name="forest.png", hue=90, description="misty pine forest at dawn with fog",
                  tags=("forest", "fog", "dawn"), rights=OWNER_RIGHTS, size="1920x1080"):
        library = self.base / "my footage"
        library.mkdir(exist_ok=True)
        path = textured_image(library / name, hue=hue, size=size)
        media.scan(library, self.catalog)
        identity = media.digest(path)
        media.annotate(identity, description=description, tags=list(tags), origin="owner",
                       source="the owner's camera", rights=dict(rights) if rights else None,
                       catalog=self.catalog)
        return identity

    def scaffold(self, intents=("misty pine forest at dawn", "calm lake at night under stars",
                                "river stream flowing nature"), duration=6.0, fps=12):
        pdir = project.project_dir(self.video_id)
        for sub in project.SUBDIRS:
            (pdir / sub).mkdir(parents=True, exist_ok=True)
        metadata = project.build_metadata(self.video_id, "Visual assets proof", "a test concept",
                                          "testers", duration, "320x180", fps)
        metadata["description"] = "Fixture media, not a real production"
        metadata["script"] = "One sentence. Another sentence. A third."
        metadata["visual_plan"] = {
            "prompt": "a dim still", "negative_prompt": "text", "style": "deep-night",
            "direction": {"identity": {"palette": ["deep indigo", "silver mist"],
                                       "atmosphere": "quiet dawn", "lighting": "soft diffuse",
                                       "materials": ["wet stone"]},
                          "avoid": ["people", "text"]}}
        metadata["audio_plan"] = {
            "direction": {"kind": "music", "chosen": {"source": "synthesised pad",
                                                      "production_grade_capable": False}},
            "composition": {"layers": [{"id": "bed", "provider": "pad", "gain_db": -6.0,
                                        "params": {"root_hz": 110}}]}}
        spec = {"width": 320, "height": 180, "fps": fps, "duration_seconds": duration,
                "images": {"source_dir": "images"}, "audio": {"file": "audio/track.wav"}}
        (pdir / "video_spec.json").write_text(json.dumps(spec, indent=2) + "\n")
        (pdir / "metadata.json").write_text(json.dumps(metadata, indent=2) + "\n")
        board = storyboard_mod.build_storyboard(self.video_id, metadata, spec,
                                                scene_count=len(intents))
        for scene, intent in zip(board["scenes"], intents):
            scene["visual_intent"] = intent
            scene["image_prompt"] = f"{intent}, cinematic, highly detailed, 8k"
        storyboard_mod.save(self.video_id, board)
        return pdir

    def metadata(self):
        return json.loads((project.project_dir(self.video_id) / "metadata.json").read_text())

    def candidate(self, entry, prefix):
        return next(c for c in entry["candidates"] if c["candidate_id"].startswith(prefix))

    def discover(self, scene_id, hub=None, **kw):
        return visual_assets.discover(self.video_id, scene_id, catalog=self.catalog,
                                      hub=hub or self.hub(), **kw)

    def select(self, scene_id, candidate_id, hub=None, **kw):
        return visual_assets.select(self.video_id, scene_id, candidate_id, "owner@example.com",
                                    catalog=self.catalog, hub=hub or self.hub(),
                                    work_dir=self.base / "dl", **kw)


class DiscoveryTests(VisualAssetCase):
    def test_the_library_is_searched_first_and_a_strong_match_spares_the_stock_quota(self):
        identity = self.own_still()
        self.scaffold()
        hub = self.hub()
        entry = self.discover("s01", hub=hub)
        top = entry["candidates"][0]
        self.assertEqual(top["candidate_id"], f"library:{identity}")
        self.assertEqual(entry["recommended"], top["candidate_id"])
        self.assertTrue(entry["library_hit"])
        self.assertEqual(hub.transport.calls, [], "no stock request was made")
        self.assertTrue(all(p["state"] == "skipped" for p in entry["providers"]))
        self.assertEqual(top["policy"]["action"], "auto", "owned, rights recorded: no ceremony")
        self.assertEqual(self.candidate(entry, "generated:")["state"], "deferred")

        more = self.discover("s01", hub=hub, more=True)
        self.assertTrue(hub.transport.calls, "asking for more does search stock")
        self.assertEqual(more["candidates"][0]["candidate_id"], f"library:{identity}")

    def test_stock_and_a_deferred_generation_appear_together_and_stock_is_recommended(self):
        self.scaffold()
        entry = self.discover("s02")
        ids = [c["candidate_id"] for c in entry["candidates"]]
        stock_id = "stock:pexels:image:501"
        self.assertIn(stock_id, ids)
        generated = self.candidate(entry, "generated:")
        self.assertEqual(generated["state"], "deferred")
        self.assertEqual(generated["generation"]["width"], 768, "native 16:9 size for the 3GB card")
        self.assertEqual(generated["generation"]["height"], 432)
        self.assertIn("seed", generated["generation"])
        self.assertIn("negative_prompt", generated["generation"])
        self.assertIsNone(generated["generation"]["reference"], "reserved for reference-guided runs")
        self.assertIn("GPU", entry["gpu"]["detail"] + generated["state_detail"])
        self.assertEqual(entry["recommended"], stock_id)
        ranked = self.candidate(entry, "stock:pexels")["ranking"]
        self.assertTrue(ranked["factors"] and ranked["limitations"])
        self.assertEqual(self.candidate(entry, "stock:pexels")["policy"]["action"], "approval_required")
        # Discovery only reads: nothing downloaded, nothing in the catalog.
        self.assertFalse(self.catalog.exists())
        self.assertEqual(self.hub().downloads, [])

    def test_when_every_stock_provider_fails_the_library_and_generation_still_answer(self):
        identity = self.own_still(description="misty pine forest at dawn", tags=("forest",))
        self.scaffold()
        down = [("api.pexels.com", (503, {}, {}))]
        entry = self.discover("s01", hub=self.hub(down), more=True)
        states = {p["provider"]: p["state"] for p in entry["providers"]}
        self.assertEqual(states["pexels"], "unavailable")
        self.assertEqual(states["pixabay"], "not_configured")
        self.assertEqual(entry["candidates"][0]["candidate_id"], f"library:{identity}")
        self.assertTrue(self.candidate(entry, "generated:"))

    def test_a_rate_limited_provider_is_reported_not_raised(self):
        self.scaffold()
        limited = [("api.pexels.com", (429, {}, {"Retry-After": "90"}))]
        entry = self.discover("s02", hub=self.hub(limited))
        pexels = next(p for p in entry["providers"] if p["provider"] == "pexels")
        self.assertEqual(pexels["state"], "rate_limited")
        self.assertEqual(entry["recommended"], None, "nothing eligible and ready; it says so")
        self.assertIn("Nothing here is both eligible and ready", entry["summary"])

    def test_library_candidates_are_filtered_by_resolution_and_rights_are_shown_not_hidden(self):
        small = self.own_still("small.png", size="320x180", description="misty pine forest at dawn")
        unrighted = self.own_still("norights.png", hue=10, description="pine forest at dawn fog",
                                   tags=("forest", "dawn"), rights=None)
        self.scaffold()
        entry = self.discover("s01", hub=self.hub([]))
        ids = [c["candidate_id"] for c in entry["candidates"]]
        self.assertNotIn(f"library:{small}", ids, "too small for a full-size production")
        shown = self.candidate(entry, f"library:{unrighted}")
        self.assertEqual(shown["policy"]["action"], "blocked")
        self.assertFalse(shown["ranking"]["eligible"])
        self.assertIn("rights", " ".join(shown["policy"]["reasons"]).lower())

    def test_near_identical_library_pictures_collapse_to_one_candidate(self):
        original = self.own_still("a.png", hue=40, description="misty pine forest dawn fog",
                                  tags=("forest", "fog"))
        copy = self.base / "my footage" / "b.png"
        subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", str(self.base / "my footage" / "a.png"),
                        "-vf", "scale=1600:900", str(copy)], check=True)
        media.scan(self.base / "my footage", self.catalog)
        twin = media.digest(copy)
        media.annotate(twin, description="misty pine forest dawn fog", tags=["forest", "fog"],
                       origin="owner", source="camera", rights=dict(OWNER_RIGHTS), catalog=self.catalog)
        self.scaffold()
        entry = self.discover("s01", hub=self.hub([]), sources=("library",))
        library = [c for c in entry["candidates"] if c["source"] == "library"]
        self.assertEqual(len(library), 1)
        self.assertEqual(len(library[0].get("near_duplicates", [])), 1)
        self.assertIn(library[0]["library"]["asset_id"], {original, twin})

    def test_search_words_come_from_the_scene_and_drop_generator_boilerplate(self):
        self.scaffold()
        entry = self.discover("s02", hub=self.hub([]), sources=("library",))
        query = entry["requirement"]["query"]
        self.assertEqual(query, "calm lake night stars")
        self.assertNotIn("cinematic", " ".join(entry["requirement"]["queries"]))
        custom = self.discover("s02", hub=self.hub([]), sources=("library",), query="lake reflections")
        self.assertEqual(custom["requirement"]["query"], "lake reflections")

    def test_unknown_scene_and_missing_storyboard_are_refused_clearly(self):
        self.scaffold()
        with self.assertRaisesRegex(visual_assets.VisualAssetError, "no such scene"):
            self.discover("s99")
        with self.assertRaisesRegex(visual_assets.VisualAssetError, "no such project"):
            visual_assets.discover("nope", "s01", catalog=self.catalog, hub=self.hub())


class SelectionTests(VisualAssetCase):
    def test_first_use_of_stock_waits_for_approval_and_downloads_nothing(self):
        self.scaffold()
        hub = self.hub()
        self.discover("s02", hub=hub)
        waiting = self.select("s02", "stock:pexels:image:501", hub=hub)
        self.assertEqual(waiting["status"], "approval_required")
        self.assertEqual(waiting["terms"]["license"], "Pexels License")
        self.assertTrue(waiting["terms"]["restrictions"])
        self.assertIn("Ada Ng", waiting["terms"]["attribution_text"])
        self.assertEqual(hub.downloads, [])
        self.assertFalse(self.catalog.exists())
        self.assertEqual(ownermedia.visuals(self.metadata()), [])

    def test_an_approved_stock_choice_becomes_a_pinned_catalog_asset_with_its_evidence(self):
        self.scaffold()
        hub = self.hub()
        self.discover("s02", hub=hub)
        result = self.select("s02", "stock:pexels:image:501", hub=hub, approve=True,
                             note="checked the licence page")
        self.assertEqual(result["status"], "selected", result)
        self.assertTrue(result["downloaded"] and result["approved"])
        self.assertIn("scenes", result["stale_stages"])

        asset = media.load(self.catalog)["assets"][result["asset_id"]]
        rights = asset["rights"]
        self.assertTrue(licensing.approved(rights))
        self.assertEqual(rights["approval"]["actor"], "owner@example.com")
        self.assertEqual(rights["license"], "Pexels License")
        self.assertEqual(rights["creator"], "Ada Ng")
        self.assertTrue(rights["retrieved_utc"])
        self.assertTrue(rights["provider_terms"]["restrictions"])
        self.assertEqual(asset["provenance"]["provider"], "pexels")
        self.assertEqual(asset["provenance"]["external_id"], "501")
        self.assertEqual(asset["provenance"]["source_url"], "https://www.pexels.com/photo/quiet-lake-501/")
        self.assertEqual(asset["usage"][0]["scene_id"], "s02")
        media.rights_record(asset)                      # the existing gate agrees

        entry = ownermedia.visuals(self.metadata())[0]
        self.assertEqual(entry["scene_id"], "s02")
        self.assertEqual(entry["lineage"]["candidate_id"], "stock:pexels:image:501")
        self.assertTrue(entry["lineage"]["explanation"])
        self.assertEqual(entry["lineage"]["approval"]["actor"], "owner@example.com")
        self.assertTrue((project.project_dir(self.video_id) / entry["staged_path"]).is_file())

    def test_ambiguous_rights_need_a_reason_and_ineligible_ones_cannot_be_chosen(self):
        self.scaffold()
        anonymous = json.loads(json.dumps(LAKE_PHOTO))
        anonymous["photos"][0]["photographer"] = ""
        hub = self.hub([("api.pexels.com/v1", anonymous)])
        entry = self.discover("s02", hub=hub)
        self.assertEqual(self.candidate(entry, "stock:")["rights"]["eligibility"], "review_required")
        waiting = self.select("s02", "stock:pexels:image:501", hub=hub)
        self.assertTrue(waiting["terms"]["needs_note"])
        with self.assertRaisesRegex(visual_assets.VisualAssetError, "approval note"):
            self.select("s02", "stock:pexels:image:501", hub=hub, approve=True)
        self.assertEqual(hub.downloads, [], "refused before any download")
        done = self.select("s02", "stock:pexels:image:501", hub=hub, approve=True,
                           note="creator is on the photo's page; licence verified by hand")
        self.assertEqual(done["status"], "selected")

        store = visual_assets.load_store(self.video_id)
        stored = next(c for c in store["scenes"]["s02"]["candidates"] if c["source"] == "stock")
        stored["rights"]["eligibility"] = "ineligible"
        visual_assets.save_store(self.video_id, store)
        # The stored record is what the policy reads: now refused outright.
        blocked = self.select("s02", stored["candidate_id"], hub=hub, approve=True)
        self.assertEqual(blocked["status"], "blocked")

    def test_a_client_cannot_supply_its_own_licence(self):
        self.scaffold()
        hub = self.hub()
        self.discover("s02", hub=hub)
        with self.assertRaisesRegex(visual_assets.VisualAssetError, "not among the candidates"):
            self.select("s02", "stock:pexels:image:99999", hub=hub, approve=True)
        with self.assertRaisesRegex(visual_assets.VisualAssetError, "search for scene"):
            self.select("s01", "stock:pexels:image:501", hub=hub, approve=True)

    def test_an_approved_asset_is_reused_automatically_and_never_downloaded_twice(self):
        self.scaffold(intents=("calm lake at night under stars", "calm lake at night under stars",
                               "river stream flowing nature"))
        hub = self.hub()
        self.discover("s01", hub=hub)
        first = self.select("s01", "stock:pexels:image:501", hub=hub, approve=True)
        self.assertEqual(hub.downloads, ["stock:pexels:image:501"])

        entry = self.discover("s02", hub=hub, more=True)
        owned = self.candidate(entry, f"library:{first['asset_id']}")
        self.assertEqual(owned["policy"]["action"], "auto", "approved once, reusable without asking")
        self.assertEqual(owned["library"]["usage"][0]["scene_id"], "s01")
        self.assertFalse([c for c in entry["candidates"] if c["candidate_id"].startswith("stock:pexels:image")],
                         "the stock copy of a file already owned is not offered again")
        again = self.select("s02", owned["candidate_id"], hub=hub)
        self.assertEqual(again["status"], "selected")
        self.assertFalse(again["downloaded"])
        self.assertEqual(hub.downloads, ["stock:pexels:image:501"])
        used = media.load(self.catalog)["assets"][first["asset_id"]]["usage"]
        self.assertEqual({u["scene_id"] for u in used}, {"s01", "s02"}, "usage history is kept")

    def test_replacing_a_choice_swaps_the_pin_and_keeps_the_others(self):
        owned = self.own_still()
        self.scaffold()
        hub = self.hub()
        self.discover("s01", hub=hub)
        self.select("s01", f"library:{owned}", hub=hub)
        self.discover("s02", hub=hub)
        self.select("s02", "stock:pexels:image:501", hub=hub, approve=True)
        pins = {e["scene_id"]: e["asset_id"] for e in ownermedia.visuals(self.metadata())}
        self.assertEqual(set(pins), {"s01", "s02"})

        stock_asset = pins["s02"]
        self.discover("s02", hub=hub, more=True, query="misty pine forest fog")
        self.select("s02", f"library:{owned}", hub=hub)        # same asset serves two scenes
        pins = {e["scene_id"]: e["asset_id"] for e in ownermedia.visuals(self.metadata())}
        self.assertEqual(pins, {"s01": owned, "s02": owned})
        used = media.load(self.catalog)["assets"][stock_asset]["usage"]
        self.assertEqual(used, [], "the replaced asset no longer claims the scene")

    def test_saving_to_the_library_without_approval_keeps_the_rights_gate_closed(self):
        self.scaffold()
        hub = self.hub()
        self.discover("s02", hub=hub)
        saved = visual_assets.save_to_library(self.video_id, "s02", "stock:pexels:image:501",
                                              "owner@example.com", catalog=self.catalog, hub=hub,
                                              work_dir=self.base / "dl")
        self.assertFalse(saved["approved"])
        asset = media.load(self.catalog)["assets"][saved["asset_id"]]
        with self.assertRaises(media.MediaError):
            media.rights_record(asset)
        listing = ownermedia.describe(asset)
        self.assertFalse(listing["selectable"], "present but not usable until approved")
        self.assertTrue(listing["rights"]["external"] and not listing["rights"]["approved"])

        entry = self.discover("s02", hub=hub, more=True)
        saved_candidate = self.candidate(entry, f"library:{saved['asset_id']}")
        self.assertEqual(saved_candidate["policy"]["action"], "approval_required")
        self.assertEqual(self.select("s02", saved_candidate["candidate_id"], hub=hub)["status"],
                         "approval_required")
        chosen = self.select("s02", saved_candidate["candidate_id"], hub=hub, approve=True)
        self.assertEqual(chosen["status"], "selected")
        self.assertTrue(licensing.approved(media.load(self.catalog)["assets"][saved["asset_id"]]["rights"]))
        self.assertEqual(hub.downloads, ["stock:pexels:image:501"], "approving did not download again")

    def test_a_failed_download_changes_nothing(self):
        self.scaffold()
        hub = self.hub()
        self.discover("s02", hub=hub)

        def broken(candidate, directory):
            raise stock.StockError("connection reset")
        hub.download = broken
        with self.assertRaisesRegex(visual_assets.VisualAssetError, "could not download"):
            self.select("s02", "stock:pexels:image:501", hub=hub, approve=True)
        self.assertFalse(self.catalog.exists())
        self.assertEqual(ownermedia.visuals(self.metadata()), [])

    def test_a_choice_records_who_made_it(self):
        self.scaffold()
        hub = self.hub()
        self.discover("s02", hub=hub)
        with self.assertRaisesRegex(visual_assets.VisualAssetError, "actor"):
            visual_assets.select(self.video_id, "s02", "stock:pexels:image:501", " ",
                                 catalog=self.catalog, hub=hub)


class DeferredGenerationTests(VisualAssetCase):
    def test_choosing_generation_while_the_gpu_is_off_is_deferred_not_failed(self):
        owned = self.own_still()
        self.scaffold()
        hub = self.hub()
        self.discover("s01", hub=hub)
        self.select("s01", f"library:{owned}", hub=hub)
        entry = self.discover("s02", hub=hub)
        generated = self.candidate(entry, "generated:")
        self.assertEqual(generated["state"], "deferred")
        self.assertFalse(generated["ranking"]["ready"])
        self.assertNotEqual(entry["recommended"], generated["candidate_id"])
        board = storyboard_mod.load(self.video_id)
        before = board["scenes"][1]["generation"]["seed"]

        result = self.select("s02", generated["candidate_id"], hub=hub)
        self.assertEqual(result["status"], "generation_requested")
        self.assertTrue(result["deferred"], "the GPU is off: recorded, not failed")
        self.assertIn("everything else in this production can continue", result["detail"])
        board = storyboard_mod.load(self.video_id)
        scene = board["scenes"][1]
        self.assertEqual((scene["generation"]["width"], scene["generation"]["height"]), (768, 432))
        self.assertTrue(scene["generation"]["require_depicted"],
                        "asked for as a generation, so an abstract plate would not satisfy it")
        self.assertIsNone(scene["image"])
        self.assertEqual(scene["generation"]["request_digest"], result["request_digest"])
        self.assertEqual(visual_assets.load_store(self.video_id)["scenes"]["s02"]["decision"]["status"],
                         "generation_deferred")
        self.assertIn("scenes", result["stale_stages"])
        self.assertEqual([e["scene_id"] for e in ownermedia.visuals(self.metadata())], ["s01"],
                         "the other scene's choice is untouched")
        self.assertEqual(self.hub().downloads, [])

    def test_regenerating_a_scene_is_a_different_picture_every_time(self):
        self.scaffold()
        first = storyboard_mod.load(self.video_id)["scenes"][1]["generation"]["seed"]
        one = visual_assets.regenerate_scene(self.video_id, "s02", actor="o@example.com",
                                             catalog=self.catalog)
        two = visual_assets.regenerate_scene(self.video_id, "s02", actor="o@example.com",
                                             catalog=self.catalog)
        self.assertEqual(len({first, one["seed"], two["seed"]}), 3)
        self.assertNotEqual(one["request_digest"], two["request_digest"])
        pinned = visual_assets.regenerate_scene(self.video_id, "s02", actor="o@example.com", seed=777,
                                                prompt="a different lake", catalog=self.catalog)
        scene = storyboard_mod.load(self.video_id)["scenes"][1]
        self.assertEqual((pinned["seed"], scene["image_prompt"]), (777, "a different lake"))
        self.assertEqual(len(scene["generation"]["regenerations"]), 3, "the history is kept")
        with self.assertRaisesRegex(visual_assets.VisualAssetError, "actor"):
            visual_assets.regenerate_scene(self.video_id, "s02", actor="", catalog=self.catalog)

    def test_when_the_gpu_is_ready_choosing_generation_requests_it(self):
        self.scaffold()
        self.gpu.return_value = GPU_ON
        hub = self.hub([])
        entry = self.discover("s02", hub=hub, sources=("library", "generated"))
        generated = self.candidate(entry, "generated:")
        self.assertEqual(generated["state"], "available")
        self.assertEqual(generated["policy"]["action"], "auto")
        result = self.select("s02", generated["candidate_id"], hub=hub)
        self.assertEqual(result["status"], "generation_requested")
        self.assertFalse(result["deferred"])
        self.assertEqual(visual_assets.load_store(self.video_id)["scenes"]["s02"]["decision"]["status"],
                         "generation_requested")

    def test_regenerating_a_pinned_scene_unpins_it_and_releases_its_usage(self):
        owned = self.own_still()
        self.scaffold()
        hub = self.hub()
        self.discover("s01", hub=hub)
        self.select("s01", f"library:{owned}", hub=hub)
        visual_assets.regenerate_scene(self.video_id, "s01", actor="o@example.com",
                                       catalog=self.catalog)
        self.assertEqual(ownermedia.visuals(self.metadata()), [])
        self.assertEqual(media.load(self.catalog)["assets"][owned].get("usage"), [])
        board = storyboard_mod.load(self.video_id)
        self.assertFalse(ownermedia.is_owner_scene(board["scenes"][0]))

    def test_cloud_generation_is_only_ever_offered_as_blocked(self):
        self.scaffold()
        with mock.patch.object(visual_assets, "paid_routes", return_value=["gemini"]):
            entry = self.discover("s02", hub=self.hub([]))
        cloud = self.candidate(entry, "cloud:")
        self.assertEqual(cloud["state"], "authorization_required")
        self.assertEqual(cloud["policy"]["action"], "blocked")
        self.assertFalse(cloud["ranking"]["ready"])
        self.assertNotEqual(entry["recommended"], cloud["candidate_id"])
        result = self.select("s02", cloud["candidate_id"], hub=self.hub([]))
        self.assertEqual(result["status"], "blocked")


class SceneAssignmentTests(VisualAssetCase):
    def test_pins_survive_a_plain_id_resave_and_unknown_scenes_are_refused(self):
        owned = self.own_still()
        self.scaffold()
        hub = self.hub()
        self.discover("s01", hub=hub)
        self.select("s01", f"library:{owned}", hub=hub)
        # The panel that lists "my visuals" resaves bare ids; the pin must stay.
        project.set_owner_media(self.video_id, {"visuals": [owned]}, "o@example.com", catalog=self.catalog)
        pinned = ownermedia.visuals(self.metadata())
        self.assertEqual([(e["scene_id"], e["lineage"]["candidate_id"]) for e in pinned],
                         [("s01", f"library:{owned}")])
        with self.assertRaisesRegex(project.ProjectError, "no such scene"):
            project.set_owner_media(self.video_id,
                                    {"visuals": [{"asset_id": owned, "scene_id": "s77"}]},
                                    "o@example.com", catalog=self.catalog)
        with self.assertRaisesRegex(project.ProjectError, "twice"):
            project.set_owner_media(self.video_id,
                                    {"visuals": [{"asset_id": owned, "scene_id": "s02"},
                                                 {"asset_id": owned, "scene_id": "s02"}]},
                                    "o@example.com", catalog=self.catalog)


@unittest.skipUnless(FFMPEG, "ffmpeg required")
class ProductionTests(VisualAssetCase):
    """One production mixing the owner's library, stock footage and stock
    photography, taken all the way through render, review and export."""

    def mixed_production(self):
        self.owned = self.own_still()
        pdir = self.scaffold()
        hub = self.hub()
        self.discover("s01", hub=hub)
        self.select("s01", f"library:{self.owned}", hub=hub)
        self.discover("s02", hub=hub)
        self.photo = self.select("s02", "stock:pexels:image:501", hub=hub, approve=True,
                                 note="checked the licence page")
        self.discover("s03", hub=hub, kind="video")
        self.clip = self.select("s03", "stock:pexels:video:601", hub=hub, approve=True)
        self.assertEqual((self.photo["status"], self.clip["status"]), ("selected", "selected"))
        self.assertTrue(self.clip["approved"])
        return pdir, hub

    def test_selected_assets_flow_through_scenes_render_review_and_export(self):
        pdir, hub = self.mixed_production()
        self.assertEqual(hub.downloads, ["stock:pexels:image:501", "stock:pexels:video:601"])

        scenes = project.run_scenes(self.video_id)
        self.assertTrue(scenes.ok, scenes.message)
        self.assertEqual(scenes.data["generated"], 0, "every scene was sourced; nothing was generated")
        board = storyboard_mod.load(self.video_id)
        kinds = [s["source"]["media_kind"] for s in board["scenes"]]
        self.assertEqual(kinds, ["image", "image", "video"], "stills and footage in one edit")
        self.assertNotIn("external", board["scenes"][0]["source"], "the owner's own still")
        external = board["scenes"][1]["source"]["external"]
        self.assertEqual(external["provider"], "Pexels")
        self.assertEqual(external["creator"], "Ada Ng")
        self.assertEqual(board["scenes"][2]["motion"]["kind"], "static", "footage keeps its own motion")
        self.assertEqual(board["scenes"][1]["source"]["candidate_id"], "stock:pexels:image:501")

        self.assertTrue(project.run_audio(self.video_id).ok)
        result = project.run_pipeline(self.video_id)
        video = pdir / "output" / f"{self.video_id}.mp4"
        self.assertTrue(video.is_file(), result.message)
        qc_report = json.loads((pdir / "output" / "qc_report.json").read_text())
        self.assertEqual(qc_report["status"], "PASS", qc_report["failures"])

        # -- the final package keeps the licence trail ----------------------
        package = json.loads((pdir / "output" / "publication_package.json").read_text())
        by_scene = {v["scene_id"]: v for v in package["visual_sources"]}
        self.assertEqual(set(by_scene), {"s01", "s02", "s03"})
        self.assertEqual(by_scene["s01"]["provider"], "owner library")
        stock_photo = by_scene["s02"]
        self.assertEqual(stock_photo["provider"], "Pexels")
        self.assertEqual(stock_photo["license"], "Pexels License")
        self.assertEqual(stock_photo["creator"], "Ada Ng")
        self.assertEqual(stock_photo["source_url"], "https://www.pexels.com/photo/quiet-lake-501/")
        self.assertTrue(stock_photo["retrieved_utc"])
        self.assertTrue(stock_photo["commercial_use"])
        self.assertEqual(stock_photo["approval"]["actor"], "owner@example.com")
        self.assertTrue(stock_photo["restrictions"])
        self.assertEqual(stock_photo["lineage"]["candidate_id"], "stock:pexels:image:501")
        self.assertEqual(by_scene["s03"]["media_kind"], "video")
        credits = package["source_attributions_required"]
        self.assertTrue(any("Ada Ng" in line for line in credits), credits)
        self.assertTrue(any("Bo Lind" in line for line in credits), credits)
        self.assertIn("Ada Ng", (pdir / "output" / "visual_credits.txt").read_text())

        # -- the description carries the required credits automatically -----
        description = package["description"]
        self.assertTrue(description.startswith("Fixture media, not a real production"))
        self.assertIn(visual_assets.CREDITS_HEADING, description)
        for line in credits:
            self.assertIn(f"- {line}", description)
        self.assertIn("https://www.pexels.com/photo/quiet-lake-501/", description,
                      "the credit keeps the source link, so provenance travels with it")
        self.assertEqual(package["description_credits"], visual_assets.credits_block(package["visual_sources"]))
        self.assertEqual(self.metadata()["description"], "Fixture media, not a real production",
                         "the editorial description itself is not rewritten")

        # -- the Review Center read model shows the same, and the files ------
        assets = project.project_assets(self.video_id)
        self.assertEqual({v["scene_id"] for v in assets["visual_sources"]}, {"s01", "s02", "s03"})
        self.assertEqual(len(assets["visual_attributions"]), 2)
        visuals = assets["owner_media"]["roles"]["visuals"]
        self.assertEqual(visuals["count"], 3)
        self.assertTrue(all(e["file"] for e in visuals["entries"]))
        self.assertEqual(assets["owner_media"]["problems"], [])

        # -- the editable Kdenlive project and its portable archive ----------
        # MLT's `melt` verifies the project when installed; where it is not
        # (this environment) the XML is still built and inspected below.
        verify = (contextlib.nullcontext() if shutil.which("melt") else
                  mock.patch.object(kdenlive_mod, "verify_project", return_value={"valid": True}))
        with verify:
            editable = project.build_kdenlive_project(self.video_id, render_output=False)
        self.assertTrue(editable.ok, editable.message)
        record = self.metadata()["editing"]["kdenlive"]
        handoffs = {h["scene_id"]: h for h in record["source_handoffs"] if h.get("scene_id")}
        self.assertEqual(handoffs["s02"]["visual_source"]["creator"], "Ada Ng")
        self.assertEqual(handoffs["s02"]["visual_source"]["license"], "Pexels License")
        self.assertEqual(handoffs["s03"]["media_kind"], "video")
        project_xml = (pdir / "output" / f"{self.video_id}.kdenlive").read_text()
        self.assertIn("Ada Ng", project_xml, "the credit is in the editor's own project notes")
        manifest = json.loads((pdir / record["package"]["provenance"]).read_text())
        self.assertEqual({v["scene_id"] for v in manifest["visual_sources"]}, {"s01", "s02", "s03"})
        with tarfile.open(pdir / record["package"]["archive"]) as bundle:
            names = bundle.getnames()
        self.assertTrue(any(n.endswith("-visual-credits.txt") for n in names), names)
        self.assertTrue(any(n.endswith("-editable-provenance.json") for n in names))

    def test_the_gate_is_exactly_as_strict_with_external_media(self):
        pdir, _ = self.mixed_production()
        self.assertTrue(project.run_scenes(self.video_id).ok)
        self.assertTrue(project.run_audio(self.video_id).ok)
        project.run_pipeline(self.video_id)

        status = project.status_report(self.video_id)
        self.assertTrue(any("production_grade is not set" in b for b in status["blocking"]),
                        status["blocking"])
        images = self.metadata()["provenance"]["images"]
        self.assertIsNone(images["production_grade"], "choosing media never grades it")
        self.assertNotIn("external", " ".join(status["blocking"]).lower())

        # A record edited to claim commercial use with no approval is not an approval.
        metadata = self.metadata()
        entry = next(e for e in metadata["owner_media"]["visuals"] if e["scene_id"] == "s02")
        entry["rights"].pop("approval")
        entry["rights"]["status"] = licensing.PENDING
        (pdir / "metadata.json").write_text(json.dumps(metadata, indent=2))
        problems = ownermedia.verification_problems(metadata, pdir)
        self.assertTrue(any("has not been approved" in p for p in problems), problems)
        blocking = project.gate_blockers(pdir, metadata, "PASS", [], True, {})
        self.assertTrue(any("has not been approved" in b for b in blocking))

        # Required credits that cannot be given fail closed.
        metadata = self.metadata()
        entry = next(e for e in metadata["owner_media"]["visuals"] if e["scene_id"] == "s02")
        entry["rights"]["attribution_text"] = ""
        blocking = project.gate_blockers(pdir, metadata, "PASS", [], True, {})
        self.assertTrue(any("attribution is required but no credit line" in b for b in blocking), blocking)
        entry["rights"]["attribution_required"] = None
        blocking = project.gate_blockers(pdir, metadata, "PASS", [], True, {})
        self.assertTrue(any("whether attribution is required is not recorded" in b for b in blocking), blocking)
        metadata = self.metadata()
        metadata["description"] = "x" * 4990
        blocking = project.gate_blockers(pdir, metadata, "PASS", [], True, {})
        self.assertTrue(any("do not fit" in b for b in blocking), blocking)

        # And so is bytes that changed after approval.
        metadata = self.metadata()
        staged = pdir / next(e for e in metadata["owner_media"]["visuals"]
                             if e["scene_id"] == "s02")["staged_path"]
        staged.unlink()
        textured_image(staged, hue=5)         # a different picture under the same name
        self.assertTrue(any("no longer matches" in p
                            for p in ownermedia.verification_problems(metadata, pdir)))

    def test_generated_scenes_mix_with_sourced_ones_in_the_provenance_record(self):
        owned = self.own_still()
        pdir = self.scaffold(intents=("misty pine forest at dawn", "something nobody photographed"))
        hub = self.hub([])
        self.discover("s01", hub=hub)
        self.select("s01", f"library:{owned}", hub=hub)
        board = storyboard_mod.load(self.video_id)
        project._apply_owner_visuals(board["scenes"], ownermedia.assignment_for_scenes(
            self.metadata(), board["scenes"]))
        scene = board["scenes"][1]
        scene["image"] = "images/s02.png"
        scene["generation"].update({"provider": "comfyui", "job_id": "job-7", "seed": 4242,
                                    "model": "sd15"})
        storyboard_mod.save(self.video_id, board)
        sources = visual_assets.sources_for_production(self.metadata(), storyboard_mod.load(self.video_id))
        self.assertEqual([s["kind"] for s in sources], ["library", "generated"])
        generated = sources[1]
        self.assertEqual((generated["provider"], generated["seed"], generated["job_id"]),
                         ("comfyui", 4242, "job-7"))
        self.assertEqual(visual_assets.attributions(sources), [])
        text = visual_assets.credits_text(self.video_id, sources)
        self.assertIn("generated (comfyui, seed 4242)", text)


class CreditsTests(unittest.TestCase):
    """The description and the fail-closed rule, without a render."""

    def source(self, scene, **kw):
        base = {"scene_id": scene, "kind": "stock", "provider": "Pexels", "attribution_required": True,
                "attribution_text": f"Photo by Ada {scene} on Pexels (https://x/{scene})"}
        return {**base, **kw}

    def test_required_credits_are_appended_once_and_in_scene_order(self):
        sources = [self.source("s01"), self.source("s02"), self.source("s03", kind="library",
                   provider="owner library", attribution_required=False, attribution_text=None),
                   self.source("s04", kind="generated", attribution_required=False, attribution_text=None)]
        text = visual_assets.description_with_credits("Calm lake at night.", sources)
        self.assertEqual(text, "Calm lake at night.\n\nVisual credits:\n"
                               "- Photo by Ada s01 on Pexels (https://x/s01)\n"
                               "- Photo by Ada s02 on Pexels (https://x/s02)")
        self.assertEqual(visual_assets.description_with_credits(text, sources), text, "idempotent")

    def test_no_required_credits_leaves_the_description_alone(self):
        sources = [self.source("s01", attribution_required=False)]
        self.assertEqual(visual_assets.description_with_credits("Hello", sources), "Hello")
        self.assertEqual(visual_assets.credits_block(sources), "")
        self.assertEqual(visual_assets.attribution_problems(sources), [])

    def test_the_same_credit_for_two_scenes_is_printed_once(self):
        twin = [self.source("s01", attribution_text="Photo by Ada on Pexels"),
                self.source("s02", attribution_text="Photo by Ada on Pexels")]
        self.assertEqual(visual_assets.credits_block(twin).count("Photo by Ada on Pexels"), 1)

    def test_a_credit_that_cannot_be_printed_blocks_rather_than_being_skipped(self):
        problems = visual_assets.attribution_problems([self.source("s01", attribution_text=" ")])
        self.assertEqual(len(problems), 1)
        self.assertIn("scene s01", problems[0])
        self.assertIn("no credit line", problems[0])

    def test_an_unknown_requirement_on_stock_is_not_treated_as_not_required(self):
        problems = visual_assets.attribution_problems(
            [self.source("s01", attribution_required=None, attribution_text=None)])
        self.assertIn("not recorded", problems[0])

    def test_credits_that_overflow_the_description_limit_block(self):
        sources = [self.source(f"s{i:02d}") for i in range(1, 60)]
        problems = visual_assets.attribution_problems(sources, "d" * 4000)
        self.assertTrue(any("do not fit" in p for p in problems))
        self.assertEqual(visual_assets.attribution_problems(sources[:2], "short"), [])


class CliTests(VisualAssetCase):
    def test_status_reports_providers_gpu_and_the_autonomy_level(self):
        status = visual_assets.status()
        by_name = {p["provider"]: p for p in status["providers"]}
        self.assertEqual(by_name["pixabay"]["state"], "not_configured")
        self.assertEqual(by_name["unsplash"]["state"], "disabled",
                         "optional and off until its production access is verified")
        self.assertEqual(by_name["pexels"]["configured"], True)
        self.assertEqual(status["gpu"]["state"], "deferred")
        self.assertEqual(status["autonomy"]["level"], "supervised")
        with mock.patch.dict(os.environ, {"VISUAL_ASSET_AUTONOMY": "graduated"}):
            self.assertIn("not available", visual_assets.status()["autonomy"]["note"])


if __name__ == "__main__":
    unittest.main()
