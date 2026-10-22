#!/usr/bin/env python3
"""Good/bad evaluation set for scripts/quality.py.

Each test builds a small piece of media that is known to be good or known
to be bad in one specific, perceptual way, then checks that the matching
evaluation flags the bad one and stays quiet on the good one. Everything is
generated locally with ffmpeg; the vision model is a local stand-in server.
"""
import json
import os
import subprocess
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import quality  # noqa: E402


def ffmpeg(*args):
    subprocess.run(["ffmpeg", "-y", "-v", "error", *args], check=True)


def plate(path, colour, noise=12, size="160x90"):
    """A still with some texture, so it measures like an image, not a fill."""
    ffmpeg("-f", "lavfi", "-i", f"color=c={colour}:s={size}",
           "-vf", f"noise=alls={noise}:allf=u", "-frames:v", "1", str(path))
    return Path(path)


def codes(result):
    return [f["code"] for f in result.get("findings") or []]


def intent(profile="sleep", palette=None, key=None, context=None, holds=False):
    context = context or {"sleep": "background_sleep", "calm": "ambient_watch",
                          "narrative": "narration"}[profile]
    resolved, unresolved = quality.resolve_palette(palette or [])
    return {
        "listening_context": {"value": context, "source": "test"},
        "visual_profile": {"value": profile, "label": quality.VISUAL_PROFILES[profile]["label"],
                           "source": "test"},
        "expected_key": {"value": key, "evidence": [key] if key else []},
        "palette": {"terms": palette or [], "source": "test",
                    "resolved": resolved, "unresolved": unresolved},
        "holds_one_frame": holds,
    }


def entry(scene_id, image, start, transition="crossfade", seconds=4.0, prompt=""):
    return {"scene_id": scene_id, "image": Path(image), "prompt": prompt, "start": start,
            "duration": 10.0, "transition": transition,
            "transition_seconds": 0.0 if transition == "cut" else seconds}


class Fixtures(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        cls.dir = Path(cls._tmp.name)
        d = cls.dir
        # Good for a moonlit sleep video: dark, deep, on the indigo/violet palette.
        cls.indigo = plate(d / "indigo.png", "0x262058")
        cls.violet = plate(d / "violet.png", "0x382060")
        cls.deep = plate(d / "deep.png", "0x141230", noise=6)
        # Bad for it: a bright, saturated red-orange field.
        cls.red = plate(d / "red.png", "0xe04020")
        # A bright, airy, low-chroma daylight plate.
        cls.day = plate(d / "day.png", "0xd8e4ec")

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def measured(self, *paths):
        return {str(p): quality.measure_image(p) for p in paths}


class PaletteResolutionTest(unittest.TestCase):
    def test_head_colour_word_names_the_colour(self):
        resolved, unresolved = quality.resolve_palette(
            ["deep indigo", "silver mist", "restrained brass warmth", "aqua rain light",
             "liquid amber", "the feeling of home"])
        by_term = {r["term"]: r for r in resolved}
        self.assertEqual(by_term["deep indigo"]["colour"], "indigo")
        self.assertEqual(by_term["silver mist"]["colour"], "mist")
        self.assertTrue(by_term["silver mist"]["neutral"])
        self.assertEqual(by_term["restrained brass warmth"]["colour"], "brass")
        self.assertEqual(by_term["aqua rain light"]["colour"], "aqua")
        self.assertEqual(unresolved, ["the feeling of home"])


class SceneIntentTest(Fixtures):
    def run_intent(self, image, profile_intent):
        images = [{"path": image, "scene_ids": ["s01"], "prompt": ""}]
        return quality.evaluate_scene_intent(images, profile_intent, self.measured(image))

    def test_good_dark_on_palette_plate_passes_for_sleep(self):
        result = self.run_intent(self.indigo, intent(
            "sleep", palette=["deep indigo", "violet"], key="low"))
        self.assertEqual([f for f in result["findings"] if f["severity"] != "info"], [])

    def test_bright_saturated_off_palette_plate_is_flagged_for_sleep(self):
        result = self.run_intent(self.red, intent(
            "sleep", palette=["deep indigo", "violet"], key="low"))
        found = codes(result)
        self.assertIn("off_key_bright", found)
        self.assertIn("vivid_for_context", found)
        self.assertIn("off_palette", found)
        off = next(f for f in result["findings"] if f["code"] == "off_palette")
        self.assertIn("red", off["detail"] + off["detail"].replace("orange", "red"))

    def test_darkness_is_never_a_finding_for_sleep(self):
        result = self.run_intent(self.deep, intent("sleep", key="low"))
        self.assertEqual(codes(result), [])

    def test_the_same_bright_plate_depends_on_intent(self):
        sleep = self.run_intent(self.day, intent("sleep"))
        narrative = self.run_intent(self.day, intent("narrative", key="high"))
        self.assertIn("bright_for_context", codes(sleep))
        self.assertEqual(codes(narrative), [])

    def test_high_key_direction_flags_a_dark_plate(self):
        result = self.run_intent(self.deep, intent("narrative", key="high"))
        self.assertIn("off_key_dark", codes(result))

    def test_unresolvable_palette_is_reported_not_passed(self):
        result = self.run_intent(self.red, intent("calm", palette=["the feeling of home"]))
        self.assertNotIn("off_palette", codes(result))
        self.assertIn("Palette not checked", result["method"])

    def test_no_images_is_unavailable(self):
        result = quality.evaluate_scene_intent([], intent("sleep"), {})
        self.assertEqual(result["status"], quality.UNAVAILABLE)


class VisualContinuityTest(Fixtures):
    def test_gentle_change_over_a_long_dissolve_passes(self):
        entries = [entry("s01", self.indigo, 0.0, seconds=4.0),
                   entry("s02", self.violet, 6.0, seconds=4.0)]
        result = quality.evaluate_visual_continuity(
            entries, intent("sleep"), self.measured(self.indigo, self.violet))
        self.assertEqual(codes(result), [])

    def test_dark_to_bright_cut_is_jarring_and_located(self):
        entries = [entry("s01", self.indigo, 0.0, transition="cut"),
                   entry("s02", self.red, 10.0)]
        result = quality.evaluate_visual_continuity(
            entries, intent("sleep"), self.measured(self.indigo, self.red))
        jarring = [f for f in result["findings"] if f["code"] == "jarring_transition"]
        self.assertEqual(len(jarring), 1)
        self.assertEqual(jarring[0]["at_seconds"], 10.0)
        self.assertEqual(jarring[0]["scene_ids"], ["s01", "s02"])
        self.assertIn("rises", jarring[0]["detail"])

    def test_the_same_cut_is_acceptable_in_a_narrative_video(self):
        entries = [entry("s01", self.indigo, 0.0, transition="cut"),
                   entry("s02", self.violet, 10.0)]
        measured = self.measured(self.indigo, self.violet)
        self.assertEqual(codes(quality.evaluate_visual_continuity(
            entries, intent("narrative"), measured)), [])

    def test_darkening_is_more_forgiving_than_brightening_for_sleep(self):
        measured = {"a": {"mean_lab": (20.0, 10.0, -30.0)}, "b": {"mean_lab": (32.0, 10.0, -30.0)}}
        rise = [entry("s01", "a", 0.0, transition="cut"), entry("s02", "b", 10.0)]
        drop = [entry("s01", "b", 0.0, transition="cut"), entry("s02", "a", 10.0)]
        self.assertIn("jarring_transition", codes(
            quality.evaluate_visual_continuity(rise, intent("sleep"), measured)))
        self.assertNotIn("jarring_transition", codes(
            quality.evaluate_visual_continuity(drop, intent("sleep"), measured)))

    def test_a_scene_unlike_the_rest_is_an_outlier(self):
        images = [self.indigo, self.violet, self.deep, self.indigo, self.red]
        entries = [entry(f"s{i:02d}", p, i * 6.0, seconds=6.0) for i, p in enumerate(images)]
        result = quality.evaluate_visual_continuity(
            entries, intent("calm"), self.measured(*set(images)))
        outliers = [f for f in result["findings"] if f["code"] == "off_look_scene"]
        self.assertEqual([f["scene_ids"] for f in outliers], [["s04"]])

    def test_one_held_frame_is_not_applicable(self):
        entries = [entry("s01", self.deep, 0.0)]
        result = quality.evaluate_visual_continuity(
            entries, intent("sleep", holds=True), self.measured(self.deep))
        self.assertEqual(result["status"], quality.NOT_APPLICABLE)
        result = quality.evaluate_visual_continuity(
            entries, intent("sleep"), self.measured(self.deep))
        self.assertEqual(result["status"], quality.NOT_APPLICABLE)


def clip(path, colour, seconds, size="160x90", fps=24):
    ffmpeg("-f", "lavfi", "-i", f"color=c={colour}:s={size}:d={seconds}:r={fps}",
           "-pix_fmt", "yuv420p", "-c:v", "libx264", str(path))
    return Path(path)


class RenderedContinuityTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        d = Path(cls._tmp.name)
        a, b = clip(d / "a.mp4", "0x262058", 6), clip(d / "b.mp4", "0x382060", 6)
        cls.good = d / "good.mp4"
        ffmpeg("-i", str(a), "-i", str(b), "-filter_complex",
               "[0][1]xfade=transition=fade:duration=2:offset=4,format=yuv420p",
               "-c:v", "libx264", str(cls.good))
        # A single white frame at ~3s inside an otherwise steady shot.
        cls.flash = d / "flash.mp4"
        ffmpeg("-i", str(a), "-vf",
               "drawbox=c=white:t=fill:enable='eq(n,72)',format=yuv420p",
               "-c:v", "libx264", str(cls.flash))
        # A hard cut at 6s from dark indigo to bright grey.
        bright = clip(d / "bright.mp4", "0xb0b0b0", 6)
        cls.cut = d / "cut.mp4"
        ffmpeg("-i", str(a), "-i", str(bright), "-filter_complex",
               "[0][1]concat=n=2:v=1:a=0,format=yuv420p", "-c:v", "libx264", str(cls.cut))

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def test_a_clean_dissolve_passes(self):
        entries = [entry("s01", "a", 0.0, seconds=2.0), entry("s02", "b", 4.0)]
        result = quality.evaluate_rendered_continuity(self.good, entries, intent("sleep"))
        self.assertEqual(codes(result), [])
        self.assertEqual(result["measurements"]["coverage"], "every frame")

    def test_a_flash_frame_is_found_and_timed(self):
        result = quality.evaluate_rendered_continuity(self.flash, [], intent("calm"))
        flashes = [f for f in result["findings"] if f["code"] == "flash_frame"]
        self.assertEqual(len(flashes), 1)
        self.assertAlmostEqual(flashes[0]["at_seconds"], 3.0, delta=0.1)

    def test_a_step_is_unplanned_only_where_no_cut_was_planned(self):
        planned_dissolve = [entry("s01", "a", 0.0, seconds=2.0), entry("s02", "b", 6.0)]
        planned_cut = [entry("s01", "a", 0.0, transition="cut"), entry("s02", "b", 6.0)]
        found = quality.evaluate_rendered_continuity(self.cut, planned_dissolve, intent("sleep"))
        self.assertIn("unplanned_step", codes(found))
        self.assertNotIn("unplanned_step", codes(
            quality.evaluate_rendered_continuity(self.cut, planned_cut, intent("sleep"))))

    def test_long_videos_are_scanned_around_transitions(self):
        entries = [entry("s01", "a", 0.0, seconds=0.0), entry("s02", "b", 3.0)]
        with mock.patch.dict(os.environ, {"QUALITY_FULL_SCAN_MAX_SECONDS": "1"}):
            result = quality.evaluate_rendered_continuity(self.flash, entries, intent("calm"))
        self.assertIn("windows around", result["measurements"]["coverage"])
        self.assertIn("flash_frame", codes(result))

    def test_no_video_is_unavailable(self):
        result = quality.evaluate_rendered_continuity(None, [], intent("sleep"))
        self.assertEqual(result["status"], quality.UNAVAILABLE)


def noise(path, seconds, seed=3, amplitude=0.2, extra_filter=None):
    args = ["-f", "lavfi", "-i",
            f"anoisesrc=color=brown:amplitude={amplitude}:duration={seconds}:seed={seed}"]
    if extra_filter:
        args += ["-af", extra_filter]
    ffmpeg(*args, "-ar", "48000", "-ac", "2", str(path))
    return Path(path)


def with_picture(audio, out):
    """Mux a soundtrack under a tiny still picture, the way a render does."""
    ffmpeg("-f", "lavfi", "-i", "color=c=0x202040:s=64x36:r=4", "-i", str(audio),
           "-shortest", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac",
           "-b:a", "192k", str(out))
    return Path(out)


class SoundtrackTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        d = Path(cls._tmp.name)
        cls.dir = d
        cls.brown = noise(d / "brown.wav", 150, seed=9)
        cls.short = noise(d / "short.wav", 5, seed=4)
        cls.looped = d / "looped.wav"
        ffmpeg("-stream_loop", "40", "-i", str(cls.short), "-t", "150", str(cls.looped))
        # The same loop with a 3s dropout in it: one anomaly must not hide
        # the repetition everywhere else.
        cls.looped_gap = d / "looped_gap.wav"
        ffmpeg("-i", str(cls.looped), "-af", "volume=enable='between(t,80,83)':volume=0.0003",
               str(cls.looped_gap))
        # +22 dB for half a second at 60s, and 3s of near-silence at 90s.
        cls.events = noise(d / "events.wav", 150, seed=9, extra_filter=(
            "volume=enable='between(t,60,60.5)':volume=12.6,"
            "volume=enable='between(t,90,93)':volume=0.0005"))
        # A bed that climbs 8 dB from start to finish.
        cls.rising = noise(d / "rising.wav", 150, seed=9,
                           extra_filter="volume='pow(10,(8*t/150)/20)':eval=frame")
        cls.scans = {name: quality.scan_soundtrack(p) for name, p in (
            ("brown", cls.brown), ("looped", cls.looped), ("looped_gap", cls.looped_gap),
            ("events", cls.events),
            ("rising", cls.rising))}

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def test_generated_noise_does_not_repeat(self):
        result = quality.evaluate_soundtrack_repetition(self.scans["brown"], 2, 2, 150)
        self.assertEqual(codes(result), [])
        self.assertLess(result["measurements"]["best_match"], 0.5)

    def test_a_short_loop_is_heard_at_its_true_period(self):
        result = quality.evaluate_soundtrack_repetition(self.scans["looped"], 2, 2, 150)
        self.assertEqual(codes(result), ["audible_loop"])
        self.assertAlmostEqual(result["measurements"]["best_period_seconds"], 5.0, delta=0.5)

    def test_a_dropout_does_not_hide_a_loop(self):
        result = quality.evaluate_soundtrack_repetition(self.scans["looped_gap"], 2, 2, 150)
        self.assertEqual(codes(result), ["audible_loop"])
        self.assertGreater(result["measurements"]["masked_seconds"], 2.0)

    def test_a_steady_bed_has_no_dynamics_findings(self):
        result = quality.evaluate_soundtrack_dynamics(
            self.scans["brown"], intent("sleep"), 2, 2, 150)
        self.assertEqual(codes(result), [])

    def test_spike_and_dropout_are_found_and_timed(self):
        result = quality.evaluate_soundtrack_dynamics(
            self.scans["events"], intent("sleep"), 2, 2, 150)
        spikes = [f for f in result["findings"] if f["code"] == "startle_spike"]
        drops = [f for f in result["findings"] if f["code"] == "dropout"]
        self.assertEqual(len(spikes), 1)
        self.assertAlmostEqual(spikes[0]["at_seconds"], 60.0, delta=0.6)
        self.assertEqual(len(drops), 1)
        self.assertAlmostEqual(drops[0]["at_seconds"], 90.0, delta=0.6)
        self.assertEqual(drops[0]["severity"], "warn")

    def test_a_pause_in_narration_is_only_a_note(self):
        result = quality.evaluate_soundtrack_dynamics(
            self.scans["events"], intent("narrative"), 2, 2, 150)
        drops = [f for f in result["findings"] if f["code"] == "dropout"]
        self.assertEqual([f["severity"] for f in drops], ["info"])

    def test_rising_loudness_is_drift_for_sleep(self):
        result = quality.evaluate_soundtrack_dynamics(
            self.scans["rising"], intent("sleep"), 2, 2, 150)
        drift = [f for f in result["findings"] if f["code"] == "loudness_drift"]
        self.assertEqual(len(drift), 1)
        self.assertIn("louder", drift[0]["detail"])

    def test_clipping_in_the_delivered_soundtrack_is_an_objective_defect(self):
        hot = noise(self.dir / "hot.wav", 30, seed=5, amplitude=0.9, extra_filter="volume=12")
        video = with_picture(hot, self.dir / "hot.mp4")
        result = quality.evaluate_soundtrack_delivery(
            video, {}, intent("sleep"), quality.scan_soundtrack(video), 2, 2)
        blocks = [f for f in result["findings"] if f["severity"] == "block"]
        self.assertTrue(blocks)
        self.assertTrue(all("delivered soundtrack" in f["detail"] for f in blocks))

    def test_distance_from_a_loudness_target_informs_but_never_blocks(self):
        quiet = noise(self.dir / "quiet.wav", 30, seed=6, amplitude=0.02)
        video = with_picture(quiet, self.dir / "quiet.mp4")
        result = quality.evaluate_soundtrack_delivery(
            video, {}, intent("calm"), quality.scan_soundtrack(video), 2, 2)
        far = [f for f in result["findings"] if f["code"] == "loudness_far_off"]
        self.assertTrue(far)
        self.assertTrue(all(f["severity"] == "warn" for f in far))

    def test_a_delivered_track_unlike_the_composed_one_is_flagged(self):
        video = with_picture(self.brown, self.dir / "brown.mp4")
        delivered = quality.audio_mod.measure(video, skip_head=2, skip_tail=2)
        same = {"measurement": {"integrated_lufs": delivered["integrated_lufs"]},
                "assessment": {"criteria": quality.audio_mod.criteria_for("background_sleep"),
                               "findings": [{"code": "loudness_far_off"}, {"code": "loudness_off"}]}}
        other = json.loads(json.dumps(same))
        other["measurement"]["integrated_lufs"] -= 6.0
        scan = quality.scan_soundtrack(video)
        self.assertNotIn("delivery_mismatch", codes(quality.evaluate_soundtrack_delivery(
            video, same, intent("sleep"), scan, 2, 2)))
        self.assertIn("delivery_mismatch", codes(quality.evaluate_soundtrack_delivery(
            video, other, intent("sleep"), scan, 2, 2)))


class FakeVisionHandler(BaseHTTPRequestHandler):
    reply = {"score": 5, "defects": [], "explanation": "matches"}
    hits = 0

    def do_POST(self):  # noqa: N802
        type(self).hits += 1
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        assert body["images"] and body["model"] == "local-vision"
        payload = json.dumps({"response": json.dumps(type(self).reply)}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, *args):
        pass


class SceneDepictionTest(Fixtures):
    def setUp(self):
        FakeVisionHandler.hits = 0
        self.server = HTTPServer(("127.0.0.1", 0), FakeVisionHandler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.judge = quality.VisionJudge(
            url=f"http://127.0.0.1:{self.server.server_port}", model="local-vision", timeout=10)
        self.images = [{"path": self.red, "scene_ids": ["s01"],
                        "prompt": "a moonlit glasshouse in deep indigo"}]

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()

    def test_unconfigured_is_unavailable_never_passed(self):
        result = quality.evaluate_scene_depiction(
            self.images, quality.VisionJudge(url="", model=""), {})
        self.assertEqual(result["status"], quality.UNAVAILABLE)
        self.assertIn("QUALITY_VISION_URL", result["reason"])

    def test_a_mismatched_image_is_flagged_with_the_models_reason(self):
        FakeVisionHandler.reply = {"score": 1, "defects": ["watermark"],
                                   "explanation": "a flat red field, no glasshouse"}
        result = quality.evaluate_scene_depiction(self.images, self.judge, {})
        self.assertEqual(sorted(codes(result)),
                         ["does_not_depict_intent", "visible_generation_defects"])
        self.assertIn("no glasshouse", result["findings"][0]["detail"])

    def test_a_matching_image_passes_and_the_verdict_is_cached(self):
        FakeVisionHandler.reply = {"score": 5, "defects": [], "explanation": "matches"}
        cache = {}
        self.assertEqual(codes(quality.evaluate_scene_depiction(self.images, self.judge, cache)), [])
        quality.evaluate_scene_depiction(self.images, self.judge, cache)
        self.assertEqual(FakeVisionHandler.hits, 1)

    def test_an_unreachable_model_is_unavailable(self):
        # A port nothing listens on: the server this class started is
        # deliberately not the one asked.
        dead = quality.VisionJudge(url="http://127.0.0.1:9", model="local-vision", timeout=2)
        result = quality.evaluate_scene_depiction(self.images, dead, {})
        self.assertEqual(result["status"], quality.UNAVAILABLE)


class ReportTest(Fixtures):
    def project(self):
        pdir = self.dir / "proj"
        (pdir / "output").mkdir(parents=True, exist_ok=True)
        video = pdir / "output" / "proj.mp4"
        ffmpeg("-f", "lavfi", "-i", "color=c=0x262058:s=64x36:d=12:r=8",
               "-f", "lavfi", "-i", "anoisesrc=color=brown:amplitude=0.2:duration=12:seed=2",
               "-shortest", "-pix_fmt", "yuv420p", "-c:v", "libx264", "-c:a", "aac", str(video))
        return pdir, video

    def test_an_evaluator_that_crashes_is_unavailable_and_the_rest_still_run(self):
        pdir, video = self.project()
        with mock.patch.object(quality, "evaluate_rendered_continuity",
                               side_effect=RuntimeError("boom")):
            report = quality.assess(pdir, video_path=video, metadata={}, storyboard={},
                                    audio_manifest={}, concept={"niche": "adult_sleep"},
                                    judge=quality.VisionJudge(url="", model=""))
        by_id = {e["id"]: e for e in report["evaluations"]}
        self.assertEqual(by_id["rendered_continuity"]["status"], quality.UNAVAILABLE)
        self.assertIn("boom", by_id["rendered_continuity"]["summary"])
        self.assertIn(by_id["soundtrack_dynamics"]["status"],
                      (quality.PASS, quality.REVIEW, quality.UNAVAILABLE))
        self.assertNotEqual(report["verdict"], quality.OK)
        unavailable = {u["id"] for u in report["coverage"]["unavailable"]}
        self.assertTrue({"rendered_continuity", "scene_depiction", "scene_intent"} <= unavailable)
        self.assertNotIn("rendered_continuity", report["coverage"]["evaluated"])

    def test_gate_sees_only_objective_defects_from_a_current_report(self):
        pdir, video = self.project()
        report = quality.assess(pdir, video_path=video, metadata={}, storyboard={},
                                audio_manifest={}, concept={},
                                judge=quality.VisionJudge(url="", model=""))
        report["evaluations"][0]["findings"] = [
            quality.finding("vivid_for_context", "warn", "taste"),
            quality.finding("sample_clipping", "block", "it clips")]
        quality.write_report(pdir, report)
        blockers = quality.blockers(pdir, video)
        self.assertEqual(len(blockers), 1)
        self.assertIn("it clips", blockers[0])
        summary = quality.summary(pdir, video)
        self.assertTrue(summary["assessed"] and summary["current"])

        # The video changes after the assessment: the report no longer applies.
        with video.open("ab") as stream:
            stream.write(b"\0")
        self.assertEqual(len(quality.blockers(pdir, video)), 1)
        self.assertIn("stale", quality.blockers(pdir, video)[0])
        self.assertFalse(quality.summary(pdir, video)["current"])

    def test_no_report_is_unassessed_not_passed(self):
        empty = self.dir / "never-assessed"
        empty.mkdir(exist_ok=True)
        self.assertEqual(quality.blockers(empty, empty / "x.mp4"), [])
        summary = quality.summary(empty, empty / "x.mp4")
        self.assertFalse(summary["assessed"])
        self.assertIsNone(summary["verdict"])

    def test_findings_are_capped_but_still_counted(self):
        many = [quality.finding("jarring_transition", "warn", f"t{i}") for i in range(10)]
        capped = quality.cap_findings(many)
        self.assertEqual(len(capped), quality.MAX_LISTED + 1)
        self.assertEqual(capped[-1]["unlisted"], 10 - quality.MAX_LISTED)


class IntentProfileTest(unittest.TestCase):
    def test_sleep_concepts_get_the_sleep_profile_even_when_narrated(self):
        profile = quality.intent_profile(
            {"audio_plan": {"listening_context": "narration"}}, {}, {},
            {"niche": "bedtime_stories", "content_format": "story to fall asleep to"})
        self.assertEqual(profile["listening_context"]["value"], "narration")
        self.assertEqual(profile["visual_profile"]["value"], "sleep")

    def test_direction_palette_and_key_are_read(self):
        board = {"visual_direction": {"identity": {
            "palette": ["deep indigo", "silver mist"], "lighting": "moonlit, dim"}}}
        profile = quality.intent_profile({}, board, {}, {})
        self.assertEqual(profile["expected_key"]["value"], "low")
        self.assertEqual(profile["palette"]["source"], "visual direction")
        self.assertEqual([r["colour"] for r in profile["palette"]["resolved"]], ["indigo", "mist"])

    def test_no_information_falls_back_and_says_so(self):
        profile = quality.intent_profile({}, {}, {}, {})
        self.assertEqual(profile["listening_context"]["source"], "default")
        self.assertEqual(profile["visual_profile"]["value"], "calm")
        self.assertIsNone(profile["expected_key"]["value"])


if __name__ == "__main__":
    unittest.main()
