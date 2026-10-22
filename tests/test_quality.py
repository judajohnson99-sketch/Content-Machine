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
import generation  # noqa: E402
import quality  # noqa: E402
import vision  # noqa: E402
sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_vision import GOOD, FakeOllama, make_image  # noqa: E402,F401


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


def _vision_runner(url="", **kwargs):
    kwargs.setdefault("models", ["qwen3-vl:2b"])
    kwargs.setdefault("coordination", "off")
    kwargs.setdefault("settle_seconds", 0)
    return vision.Runner(provider=vision.OllamaProvider(url=url), **kwargs)


def _vision_item(path, scene_id="s01", prompt="a moonlit glasshouse in deep indigo",
                 start=0.0, depicted=True, job_id=None):
    return {"path": Path(path), "scene_ids": [scene_id], "prompt": prompt,
            "depicted": depicted, "job_id": job_id, "start": start}


class SceneVisionTest(Fixtures):
    """The vision evaluation, with a stand-in Ollama returning scripted replies."""

    def setUp(self):
        FakeOllama.installed = ["qwen3-vl:2b"]
        FakeOllama.requests = []
        FakeOllama.delay = 0.0
        self.reply = dict(GOOD)
        FakeOllama.handler = staticmethod(lambda body: (200, {"response": json.dumps(self.reply)}))
        self.server = HTTPServer(("127.0.0.1", 0), FakeOllama)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.server.server_port}"
        self.work = tempfile.TemporaryDirectory()
        self.jobs = Path(self.work.name) / "jobs"
        patch = mock.patch.dict(os.environ, {"CONTENT_MACHINE_JOBS_DIR": str(self.jobs)})
        patch.start()
        self.addCleanup(patch.stop)
        jobs_patch = mock.patch.object(generation, "JOBS_DIR", self.jobs)
        jobs_patch.start()
        self.addCleanup(jobs_patch.stop)

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.work.cleanup()

    def runner(self, **kwargs):
        return _vision_runner(self.url, state_path=Path(self.work.name) / "state.json", **kwargs)

    def images(self, *paths):
        return [_vision_item(p, f"s{i + 1:02d}", start=i * 6.0) for i, p in enumerate(paths)]

    # --- eligibility and honest absence ---------------------------------

    def test_no_images_is_unavailable(self):
        self.assertEqual(quality.evaluate_scene_vision([], self.runner())["status"], quality.UNAVAILABLE)

    def test_abstract_plates_are_not_judged_for_depiction(self):
        items = [_vision_item(self.red, depicted=False)]
        result = quality.evaluate_scene_vision(items, self.runner())
        self.assertEqual(result["status"], quality.NOT_APPLICABLE)
        self.assertIn("abstract plates", result["reason"])
        self.assertEqual(FakeOllama.requests, [])

    def test_without_a_model_or_evidence_it_is_not_evaluated_and_says_why(self):
        result = quality.evaluate_scene_vision(self.images(self.red, self.indigo), _vision_runner())
        self.assertEqual(result["status"], quality.UNAVAILABLE)
        self.assertIn("QUALITY_VISION_URL", result["reason"])
        self.assertIn("2 depicted image(s) were not judged", result["reason"])
        self.assertNotIn("findings", result)

    def test_an_unreachable_model_is_not_evaluated(self):
        runner = _vision_runner("http://127.0.0.1:9", state_path=Path(self.work.name) / "s.json")
        result = quality.evaluate_scene_vision(self.images(self.red), runner)
        self.assertEqual(result["status"], quality.UNAVAILABLE)
        self.assertIn("not reachable", result["reason"])

    def test_an_image_with_no_prompt_cannot_be_judged_against_one(self):
        items = [_vision_item(self.red, prompt="")]
        result = quality.evaluate_scene_vision(items, self.runner())
        self.assertEqual(result["status"], quality.UNAVAILABLE)
        self.assertIn("no prompt", result["reason"])
        self.assertEqual(FakeOllama.requests, [])

    # --- what a judgement says ------------------------------------------

    def test_a_mismatched_image_is_flagged_with_the_models_reason_and_its_place(self):
        self.reply = dict(GOOD, adherence=1, adherence_reason="a red field, no glasshouse")
        result = quality.evaluate_scene_vision(
            [_vision_item(self.red, "s05", start=29.0)], self.runner())
        found = [f for f in result["findings"] if f["code"] == "does_not_depict_intent"]
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0]["severity"], "warn")
        self.assertEqual(found[0]["scene_ids"], ["s05"])
        self.assertEqual(found[0]["at_seconds"], 29.0)
        self.assertEqual(found[0]["dimension"], "adherence")
        self.assertIn("a red field, no glasshouse", found[0]["detail"])
        self.assertIn("qwen3-vl:2b", found[0]["detail"])
        self.assertIn("not yet calibrated", found[0]["detail"])

    def test_vision_findings_are_never_blocking(self):
        self.reply = dict(GOOD, adherence=1, composition=1,
                          artifacts=["text_or_watermark", "deformed_anatomy"])
        result = quality.evaluate_scene_vision(self.images(self.red), self.runner())
        self.assertTrue(result["findings"])
        self.assertNotIn("block", {f["severity"] for f in result["findings"]})

    def test_a_clean_image_passes_and_reports_its_dimensions_and_uncertainty(self):
        result = quality.evaluate_scene_vision(self.images(self.indigo), self.runner())
        self.assertEqual(result["findings"], [])
        self.assertFalse(result["partial"])
        dims = result["measurements"]["dimensions"]
        self.assertEqual(dims["adherence"], {"mean": 4.0, "min": 4, "flagged": 0})
        self.assertEqual(dims["consistency"]["assessed"], False)
        self.assertEqual(result["measurements"]["models"], {"qwen3-vl:2b": {
            "calibrated": [], "uncalibrated": list(vision.DIMENSIONS)}})
        self.assertIn("Not yet calibrated", result["method"])
        self.assertEqual(result["measurements"]["judgements"][0]["model"], "qwen3-vl:2b")

    def test_composition_and_artifacts_are_separate_dimensions(self):
        self.reply = dict(GOOD, composition=1, composition_reason="subject cut off",
                          artifacts=["blur"], artifact_reason="soft focus")
        result = quality.evaluate_scene_vision(self.images(self.indigo), self.runner())
        by_dim = {f["dimension"]: f for f in result["findings"]}
        self.assertEqual(set(by_dim), {"composition", "artifacts"})
        self.assertEqual(by_dim["composition"]["severity"], "info",
                         "an uncalibrated model may not warn about composition")
        self.assertEqual(result["measurements"]["dimensions"]["artifacts"]["by_name"], {"blur": 1})

    def test_a_model_calibrated_for_composition_may_warn_about_it(self):
        self.reply = dict(GOOD, composition=1)
        lookup = lambda model: dict(vision.thresholds_for(model),  # noqa: E731
                                    calibrated_dimensions=["composition"])
        result = quality.evaluate_scene_vision(self.images(self.indigo), self.runner(), lookup)
        self.assertEqual([f["severity"] for f in result["findings"]], ["warn"])
        self.assertEqual(result["measurements"]["models"]["qwen3-vl:2b"]["calibrated"], ["composition"])
        self.assertIn("qwen3-vl:2b for adherence, artifacts, consistency", result["method"])

    def test_a_fully_calibrated_model_says_so(self):
        lookup = lambda model: dict(vision.thresholds_for(model),  # noqa: E731
                                    calibrated_dimensions=list(vision.DIMENSIONS))
        result = quality.evaluate_scene_vision(self.images(self.indigo), self.runner(), lookup)
        self.assertIn("Every dimension was calibrated", result["method"])

    # --- where judgements come from -------------------------------------

    def test_worker_evidence_is_used_without_calling_a_model(self):
        sha = vision.sha256_file(self.indigo)
        judgement = vision.coerce_judgement(
            dict(GOOD, adherence=2, adherence_reason="wrong room"), model="qwen3-vl:2b",
            provider="ollama", intent="p", image_sha=sha)
        evidence = vision.sanitize_evidence(
            {"judgements": {"gen_01.png": judgement}}, {"gen_01.png": sha}, "home-gpu-01")
        generation.save_job({"job_id": "deadbeef01", "status": generation.COMPLETED,
                             "request": {}, "attempts": [], "vision": evidence})
        items = [_vision_item(self.indigo, job_id="deadbeef01")]
        result = quality.evaluate_scene_vision(items, _vision_runner())
        self.assertEqual(codes(result), ["does_not_depict_intent"])
        self.assertEqual(result["measurements"]["coverage"]["sources"], {"worker": 1})
        self.assertEqual(FakeOllama.requests, [])

    def test_evidence_for_different_bytes_is_not_used(self):
        other = vision.coerce_judgement(GOOD, model="m", provider="ollama", intent="p",
                                        image_sha=vision.sha256_file(self.red))
        evidence = vision.sanitize_evidence({"judgements": {"gen_01.png": other}},
                                            {"gen_01.png": vision.sha256_file(self.red)})
        generation.save_job({"job_id": "deadbeef02", "status": generation.COMPLETED,
                             "request": {}, "attempts": [], "vision": evidence})
        result = quality.evaluate_scene_vision(
            [_vision_item(self.indigo, job_id="deadbeef02")], _vision_runner())
        self.assertEqual(result["status"], quality.UNAVAILABLE)

    def test_the_workers_reason_beats_no_local_model_when_nothing_was_judged(self):
        sha = vision.sha256_file(self.indigo)
        evidence = vision.sanitize_evidence(
            {"skipped": {"gen_01.png": {"code": vision.GPU_BUSY, "detail": "deferred: ComfyUI busy"}}},
            {"gen_01.png": sha}, "home-gpu-01")
        generation.save_job({"job_id": "deadbeef03", "status": generation.COMPLETED,
                             "request": {}, "attempts": [], "vision": evidence})
        result = quality.evaluate_scene_vision(
            [_vision_item(self.indigo, job_id="deadbeef03")], _vision_runner())
        self.assertEqual(result["status"], quality.UNAVAILABLE)
        self.assertIn("deferred: ComfyUI busy", result["reason"])
        self.assertIn("GPU worker", result["reason"])

    def test_a_second_assessment_reuses_the_cache(self):
        runner = self.runner()
        quality.evaluate_scene_vision(self.images(self.indigo), runner)
        calls = len(FakeOllama.requests)
        again = quality.evaluate_scene_vision(self.images(self.indigo), self.runner())
        self.assertEqual(len(FakeOllama.requests), calls)
        self.assertEqual(again["measurements"]["coverage"]["sources"], {"cache": 1})

    # --- partial coverage ------------------------------------------------

    def test_a_partly_judged_set_is_partial_and_says_how_much(self):
        FakeOllama.delay = 0.35
        runner = self.runner(budget_seconds=0.3)
        result = quality.evaluate_scene_vision(
            self.images(self.indigo, self.violet, self.deep), runner)
        self.assertTrue(result["partial"])
        self.assertEqual(result["measurements"]["coverage"]["judged"], 1)
        self.assertIn("judged 1 of 3", result["coverage_note"])
        report = quality._run("scene_vision", "t", False, lambda: result)
        self.assertEqual(report["status"], quality.PARTIAL)
        self.assertTrue(report["partial"])
        self.assertIn("partly evaluated", report["summary"])
        self.assertIn("time budget", report["summary"])

    def test_a_partial_evaluation_with_findings_is_still_review(self):
        self.reply = dict(GOOD, adherence=1)
        FakeOllama.delay = 0.35
        result = quality.evaluate_scene_vision(
            self.images(self.indigo, self.violet), self.runner(budget_seconds=0.3))
        report = quality._run("scene_vision", "t", False, lambda: result)
        self.assertEqual(report["status"], quality.REVIEW)
        self.assertTrue(report["partial"])

    def test_abstract_plates_in_a_mixed_set_are_counted_not_judged(self):
        items = [_vision_item(self.indigo), _vision_item(self.red, "s02", depicted=False)]
        result = quality.evaluate_scene_vision(items, self.runner())
        self.assertEqual(result["measurements"]["coverage"]["abstract_plates_not_judged"], 1)
        self.assertEqual(len(FakeOllama.requests), 1)

    # --- consistency -----------------------------------------------------

    def set_styles(self, styles):
        self.styles = iter(styles)

        def handler(body):
            return 200, {"response": json.dumps(dict(GOOD, **next(self.styles)))}
        FakeOllama.handler = staticmethod(handler)

    CALM = {"style_medium": "digital painting", "style_palette": ["indigo", "violet"],
            "style_lighting": "dim moonlight", "style_mood": "calm"}
    ODD = {"style_medium": "photograph", "style_palette": ["orange", "yellow"],
           "style_lighting": "harsh daylight", "style_mood": "busy"}

    def five_images(self):
        paths = [make_image(Path(self.work.name) / f"c{i}.png", "0x%06x" % (0x202040 + i * 0x101)) for i in range(5)]
        return [dict(item, prompt=f"a room, view {i}") for i, item in enumerate(self.images(*paths))]

    def test_one_scene_in_a_different_style_is_an_outlier(self):
        self.set_styles([self.CALM, self.CALM, self.ODD, self.CALM, self.CALM])
        result = quality.evaluate_scene_vision(self.five_images(), self.runner())
        off = [f for f in result["findings"] if f["code"] == "off_style_image"]
        self.assertEqual([f["scene_ids"] for f in off], [["s03"]])
        self.assertEqual(off[0]["severity"], "info", "uncalibrated consistency is a note")
        self.assertIn("photograph", off[0]["detail"])
        self.assertEqual(result["measurements"]["dimensions"]["consistency"]["outliers"], 1)

    def test_a_uniform_set_has_no_outlier(self):
        self.set_styles([self.CALM] * 5)
        result = quality.evaluate_scene_vision(self.five_images(), self.runner())
        self.assertNotIn("off_style_image", codes(result))
        self.assertTrue(result["measurements"]["dimensions"]["consistency"]["assessed"])

    def test_consistency_is_not_assessed_with_too_few_images(self):
        self.set_styles([self.CALM, self.ODD])
        result = quality.evaluate_scene_vision(self.five_images()[:2], self.runner())
        consistency = result["measurements"]["dimensions"]["consistency"]
        self.assertFalse(consistency["assessed"])
        self.assertIn("too few", consistency["reason"])

    def test_descriptors_from_different_models_are_not_compared(self):
        def lookup(model):
            return vision.thresholds_for(model)
        calm = [vision.coerce_judgement(dict(GOOD, **self.CALM), model="a", provider="o",
                                        intent="x", image_sha="a" * 64) for _ in range(2)]
        odd = [vision.coerce_judgement(dict(GOOD, **self.ODD), model="b", provider="o",
                                       intent="x", image_sha="b" * 64) for _ in range(2)]
        items = [(_vision_item(self.indigo, f"s0{i}"), j) for i, j in enumerate(calm + odd)]
        findings, measure = quality.consistency_findings(items, lookup)
        self.assertEqual(findings, [])
        self.assertFalse(measure["assessed"], "two models' descriptors were pooled")

    def test_style_tokens_resolve_colour_words(self):
        tokens = quality.style_tokens({"medium": "Digital painting", "palette": ["deep indigo", "silver"],
                                       "lighting": "dim moonlight", "mood": "calm"})
        self.assertTrue({"digital", "painting", "indigo", "silver", "dim", "moonlight", "calm"} <= tokens)


class ReportTest(Fixtures):
    def project(self):
        pdir = self.dir / ("proj-" + self.id().rsplit(".", 1)[-1])
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
                                    vision_runner=_vision_runner())
        by_id = {e["id"]: e for e in report["evaluations"]}
        self.assertEqual(by_id["rendered_continuity"]["status"], quality.UNAVAILABLE)
        self.assertIn("boom", by_id["rendered_continuity"]["summary"])
        self.assertIn(by_id["soundtrack_dynamics"]["status"],
                      (quality.PASS, quality.REVIEW, quality.UNAVAILABLE))
        self.assertNotEqual(report["verdict"], quality.OK)
        unavailable = {u["id"] for u in report["coverage"]["unavailable"]}
        self.assertTrue({"rendered_continuity", "scene_vision", "scene_intent"} <= unavailable)
        self.assertNotIn("rendered_continuity", report["coverage"]["evaluated"])

    def test_gate_sees_only_objective_defects_from_a_current_report(self):
        pdir, video = self.project()
        report = quality.assess(pdir, video_path=video, metadata={}, storyboard={},
                                audio_manifest={}, concept={},
                                vision_runner=_vision_runner())
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

    def vision_project(self, depicted=True):
        pdir, video = self.project()
        (pdir / "images").mkdir(exist_ok=True)
        subprocess.run(["cp", str(self.indigo), str(pdir / "images" / "a.png")], check=True)
        board = {"scenes": [{
            "scene_id": "s01", "image": "images/a.png", "image_prompt": "a moonlit glasshouse",
            "duration_seconds": 6.0, "transition": {"kind": "cut"},
            "generation": {"produces_depicted": depicted, "job_id": None}}]}
        return pdir, video, board

    def test_vision_findings_inform_review_but_never_reach_the_gate(self):
        pdir, video, board = self.vision_project()
        result = vision.BatchResult()
        result.judgements[str(pdir / "images" / "a.png")] = vision.coerce_judgement(
            dict(GOOD, adherence=1, adherence_reason="a flat field", artifacts=["text_or_watermark"]),
            model="qwen3-vl:2b", provider="ollama", intent="a moonlit glasshouse",
            image_sha=vision.sha256_file(pdir / "images" / "a.png"))
        with mock.patch.object(vision.Runner, "judge_images", return_value=result):
            report = quality.assess(pdir, video_path=video, metadata={}, storyboard=board,
                                    audio_manifest={}, concept={"niche": "adult_sleep"},
                                    vision_runner=_vision_runner())
        by_id = {e["id"]: e for e in report["evaluations"]}
        evaluation = by_id["scene_vision"]
        self.assertEqual(evaluation["status"], quality.REVIEW)
        self.assertEqual({f["severity"] for f in evaluation["findings"]}, {"warn"})
        self.assertIn("scene_vision", report["coverage"]["evaluated"])
        quality.write_report(pdir, report)
        self.assertEqual(quality.blockers(pdir, video), [],
                         "a subjective vision finding blocked the review gate")

    def test_abstract_plates_make_the_vision_evaluation_not_applicable(self):
        pdir, video, board = self.vision_project(depicted=False)
        report = quality.assess(pdir, video_path=video, metadata={}, storyboard=board,
                                audio_manifest={}, concept={}, vision_runner=_vision_runner())
        by_id = {e["id"]: e for e in report["evaluations"]}
        self.assertEqual(by_id["scene_vision"]["status"], quality.NOT_APPLICABLE)
        self.assertEqual([n["id"] for n in report["coverage"]["not_applicable"]
                          if n["id"] == "scene_vision"], ["scene_vision"])

    def test_a_partial_vision_evaluation_is_listed_as_partial_in_coverage(self):
        pdir, video, board = self.vision_project()
        board["scenes"].append(dict(board["scenes"][0], scene_id="s02", image="images/b.png",
                                    image_prompt="a pale moon"))
        subprocess.run(["cp", str(self.violet), str(pdir / "images" / "b.png")], check=True)
        result = vision.BatchResult()
        result.judgements[str(pdir / "images" / "a.png")] = vision.coerce_judgement(
            GOOD, model="qwen3-vl:2b", provider="ollama", intent="x",
            image_sha=vision.sha256_file(pdir / "images" / "a.png"))
        result.skipped[str(pdir / "images" / "b.png")] = {"code": vision.GPU_BUSY, "detail": "deferred: ComfyUI busy"}
        with mock.patch.object(vision.Runner, "judge_images", return_value=result):
            report = quality.assess(pdir, video_path=video, metadata={}, storyboard=board,
                                    audio_manifest={}, concept={}, vision_runner=_vision_runner())
        by_id = {e["id"]: e for e in report["evaluations"]}
        self.assertEqual(by_id["scene_vision"]["status"], quality.PARTIAL)
        self.assertEqual([p["id"] for p in report["coverage"]["partial"]], ["scene_vision"])
        self.assertIn("deferred: ComfyUI busy", report["coverage"]["partial"][0]["reason"])
        self.assertNotIn("scene_vision", {u["id"] for u in report["coverage"]["unavailable"]})

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
