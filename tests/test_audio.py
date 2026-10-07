#!/usr/bin/env python3
"""Tests for the audio subsystem.

Covers duration fitting, looping, trimming, mixing, determinism, silence
rejection, provider selection, licensing refusal, and failure handling.
Standard library only; TTS tests skip when the voice model is absent.
"""
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import audio  # noqa: E402

CLI = ROOT / "content-machine"

# A licence block that satisfies the file provider's evidence requirement.
CLEARED_LICENCE = {
    "source": "locally generated test fixture",
    "creator": "content-machine tests",
    "license": "generated-original",
    "commercial_use": True,
    "attribution_required": False,
    "evidence": "Synthesised by the test suite; no third-party rights.",
}


class AudioTestCase(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        for tool in ("ffmpeg", "ffprobe"):
            if not shutil.which(tool):
                raise unittest.SkipTest(f"{tool} not found on PATH")

    def setUp(self):
        self._tmp = __import__("tempfile").TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)

    def make_wav(self, name, seconds, frequency=440):
        """A real, decodable audio file to use as a source asset."""
        path = self.tmp / name
        subprocess.run([
            "ffmpeg", "-y", "-v", "error", "-f", "lavfi",
            "-i", f"sine=frequency={frequency}:duration={seconds}:sample_rate=48000",
            "-ac", "2", str(path)], check=True, capture_output=True)
        return path

    def compose(self, plan, name="out.wav"):
        out = self.tmp / name
        return audio.compose(plan, out), out


class TestDurationFitting(AudioTestCase):

    def test_generated_source_hits_exact_duration(self):
        manifest, out = self.compose({
            "target_seconds": 3, "normalize": False,
            "layers": [{"provider": "noise", "params": {"color": "brown"}}]})
        self.assertAlmostEqual(audio.probe_seconds(out), 3.0, delta=0.05)
        self.assertEqual(manifest["status"], "OK")
        self.assertAlmostEqual(manifest["actual_seconds"], 3.0, delta=0.05)

    def test_short_source_is_looped_to_target(self):
        src = self.make_wav("short.wav", 1.0)
        _, out = self.compose({
            "target_seconds": 4, "normalize": False,
            "layers": [{"provider": "file",
                        "params": {"path": str(src), "license": CLEARED_LICENCE}}]})
        self.assertAlmostEqual(audio.probe_seconds(out), 4.0, delta=0.05,
                               msg="a 1s source should loop to fill 4s")
        self.assertGreater(audio.mean_volume_db(out), -60,
                           "looped region must contain audio, not silence")

    def test_long_source_is_trimmed_to_target(self):
        src = self.make_wav("long.wav", 8.0)
        _, out = self.compose({
            "target_seconds": 2, "normalize": False,
            "layers": [{"provider": "file",
                        "params": {"path": str(src), "license": CLEARED_LICENCE}}]})
        self.assertAlmostEqual(audio.probe_seconds(out), 2.0, delta=0.05)

    def test_crossfade_looping_produces_exact_duration(self):
        src = self.make_wav("loopme.wav", 2.0)
        _, out = self.compose({
            "target_seconds": 6, "normalize": False,
            "layers": [{"provider": "file", "params": {
                "path": str(src), "license": CLEARED_LICENCE,
                "crossfade_loop_seconds": 0.5}}]})
        self.assertAlmostEqual(audio.probe_seconds(out), 6.0, delta=0.05)

    def test_crossfade_longer_than_source_is_rejected(self):
        src = self.make_wav("tiny.wav", 1.0)
        with self.assertRaises(audio.AudioError) as ctx:
            self.compose({"target_seconds": 5, "normalize": False,
                          "layers": [{"provider": "file", "params": {
                              "path": str(src), "license": CLEARED_LICENCE,
                              "crossfade_loop_seconds": 2.0}}]})
        self.assertIn("crossfade_loop_seconds", str(ctx.exception))


class TestMixing(AudioTestCase):

    def test_layers_are_mixed_together(self):
        manifest, out = self.compose({
            "target_seconds": 3, "normalize": False,
            "layers": [
                {"id": "bed", "provider": "noise", "params": {"color": "pink"}, "gain_db": -12},
                {"id": "drone", "provider": "tone", "params": {"frequency": 90}, "gain_db": -6},
            ]})
        self.assertEqual(len(manifest["layers"]), 2)
        self.assertEqual([l["layer_id"] for l in manifest["layers"]], ["bed", "drone"])
        self.assertAlmostEqual(audio.probe_seconds(out), 3.0, delta=0.05)
        self.assertGreater(audio.mean_volume_db(out), -60)

    def test_gain_reduction_is_audible_in_the_mix(self):
        loud, _ = self.compose({
            "target_seconds": 2, "normalize": False,
            "layers": [{"provider": "noise", "params": {"color": "white"}, "gain_db": 0}]},
            name="loud.wav")
        quiet, _ = self.compose({
            "target_seconds": 2, "normalize": False,
            "layers": [{"provider": "noise", "params": {"color": "white"}, "gain_db": -20}]},
            name="quiet.wav")
        self.assertLess(quiet["mean_volume_db"], loud["mean_volume_db"] - 10,
                        "a -20 dB layer should measurably reduce the mix level")

    def test_fades_do_not_change_duration(self):
        _, out = self.compose({
            "target_seconds": 4, "normalize": False,
            "fade_in_seconds": 1, "fade_out_seconds": 1,
            "layers": [{"provider": "noise", "params": {"color": "brown"}}]})
        self.assertAlmostEqual(audio.probe_seconds(out), 4.0, delta=0.05)


class TestPadProvider(AudioTestCase):

    def test_renders_an_audible_evolving_layer(self):
        manifest, out = self.compose({
            "target_seconds": 2, "normalize": False,
            "layers": [{"id": "music", "provider": "pad",
                       "params": {"root_hz": 130.0, "chord": "warm"}}]})
        self.assertAlmostEqual(audio.probe_seconds(out), 2.0, delta=0.05)
        self.assertGreater(audio.mean_volume_db(out), -60)
        self.assertEqual(manifest["layers"][0]["parameters"]["chord"], "warm")

    def test_unknown_chord_falls_back_to_the_default_rather_than_erroring(self):
        manifest, _ = self.compose({
            "target_seconds": 1, "normalize": False,
            "layers": [{"provider": "pad", "params": {"chord": "not-a-real-chord"}}]})
        self.assertEqual(manifest["layers"][0]["parameters"]["intervals"],
                         list(audio.DEFAULT_PAD_CHORD))

    def test_marked_generated_and_commercially_clear(self):
        manifest, _ = self.compose({
            "target_seconds": 1, "normalize": False,
            "layers": [{"provider": "pad", "params": {}}]})
        self.assertTrue(manifest["commercial_use_cleared"])
        self.assertEqual(manifest["layers"][0]["license"], "generated-original")


class TestMusicProvider(AudioTestCase):
    """The generative-music provider. These tests establish that it renders
    a progression - they deliberately establish nothing about whether the
    result is pleasant, which no check here can decide (see
    creative.route_audio, which marks this source not production-grade
    capable, and project.gate_blockers, which holds review until a human
    has listened)."""

    def test_renders_a_progression_to_the_exact_duration(self):
        manifest, out = self.compose({
            "target_seconds": 20, "normalize": False,
            "layers": [{"id": "bed", "provider": "music",
                       "params": {"root_hz": 98.0, "mood": "warm",
                                  "chord_seconds": 5.0}}]})
        self.assertAlmostEqual(audio.probe_seconds(out), 20.0, delta=0.05)
        self.assertGreater(audio.mean_volume_db(out), -60)
        params = manifest["layers"][0]["parameters"]
        self.assertEqual(params["mood"], "warm")
        self.assertGreaterEqual(len(params["progression"]), 2)

    def test_the_mood_chooses_the_progression(self):
        warm = audio.MUSIC_PROGRESSIONS["warm"]
        eerie = audio.MUSIC_PROGRESSIONS["eerie"]
        self.assertNotEqual(warm, eerie)

    def test_an_unknown_mood_falls_back_rather_than_erroring(self):
        manifest, _ = self.compose({
            "target_seconds": 9, "normalize": False,
            "layers": [{"provider": "music",
                       "params": {"mood": "not-a-real-mood", "chord_seconds": 4.0}}]})
        self.assertEqual(manifest["layers"][0]["parameters"]["progression"],
                         [list(shape) for _off, shape
                          in audio.DEFAULT_MUSIC_PROGRESSION[:2]])

    def test_marked_generated_and_commercially_clear(self):
        manifest, _ = self.compose({
            "target_seconds": 9, "normalize": False,
            "layers": [{"provider": "music", "params": {"chord_seconds": 4.0}}]})
        self.assertTrue(manifest["commercial_use_cleared"])
        self.assertEqual(manifest["layers"][0]["license"], "generated-original")

    def test_a_track_longer_than_one_cycle_loops_without_changing_duration(self):
        _manifest, out = self.compose({
            "target_seconds": 30, "normalize": False,
            "layers": [{"provider": "music", "params": {"chord_seconds": 4.0}}]})
        self.assertAlmostEqual(audio.probe_seconds(out), 30.0, delta=0.05)


class TestMusicLibrary(AudioTestCase):
    """The licensed-library path: real recordings a human dropped in, with
    rights declared per track."""

    def _library(self, tracks):
        for track in tracks:
            if track.get("path") == "__real__":
                track["path"] = str(self.make_wav(f"{track['id']}.wav", 1))
        path = self.tmp / "library.json"
        path.write_text(json.dumps({"tracks": tracks}))
        return path

    def _track(self, id_, **extra):
        base = {"id": id_, "path": "__real__", "source": "test fixture",
                "license": "CC0", "commercial_use": True}
        base.update(extra)
        return base

    def test_no_library_is_the_normal_empty_state_not_an_error(self):
        self.assertEqual(audio.load_music_library(self.tmp / "absent.json"), [])

    def test_a_track_missing_a_rights_field_is_skipped(self):
        incomplete = self._track("t1")
        del incomplete["license"]
        path = self._library([incomplete, self._track("t2")])
        loaded = audio.load_music_library(path)
        self.assertEqual([t["id"] for t in loaded], ["t2"])

    def test_a_track_whose_file_is_missing_is_skipped(self):
        path = self._library([self._track("ghost", path="assets/music/nope.wav")])
        self.assertEqual(audio.load_music_library(path), [])

    def test_the_mood_picks_the_matching_track(self):
        path = self._library([self._track("generic"),
                              self._track("cosy", tags=["warm", "piano"])])
        library = audio.load_music_library(path)
        self.assertEqual(audio.select_library_track(mood="warm", library=library)["id"],
                         "cosy")

    def test_nothing_tagged_still_offers_a_real_recording(self):
        path = self._library([self._track("generic", duration_seconds=600)])
        library = audio.load_music_library(path)
        self.assertEqual(audio.select_library_track(mood="eerie", library=library)["id"],
                         "generic")

    def test_an_empty_library_selects_nothing(self):
        self.assertIsNone(audio.select_library_track(mood="warm", library=[]))


class TestQualityCheck(AudioTestCase):

    def test_a_single_static_layer_is_flagged(self):
        manifest, _ = self.compose({
            "target_seconds": 1, "normalize": False,
            "layers": [{"provider": "noise", "params": {"color": "brown"}}]})
        self.assertFalse(manifest["quality"]["has_variation"])
        self.assertEqual(manifest["quality"]["layer_count"], 1)
        self.assertTrue(manifest["quality"]["warnings"])
        self.assertIn("monotonous", manifest["quality"]["warnings"][0])

    def test_a_second_distinct_provider_clears_the_warning(self):
        manifest, _ = self.compose({
            "target_seconds": 1, "normalize": False,
            "layers": [
                {"provider": "noise", "params": {"color": "brown"}, "gain_db": -12},
                {"provider": "pad", "params": {"chord": "calm"}, "gain_db": -18},
            ]})
        self.assertTrue(manifest["quality"]["has_variation"])
        self.assertEqual(manifest["quality"]["distinct_providers"], ["noise", "pad"])
        self.assertEqual(manifest["quality"]["warnings"], [])

    def test_fades_alone_also_clear_the_warning(self):
        manifest, _ = self.compose({
            "target_seconds": 2, "normalize": False,
            "layers": [{"provider": "noise", "params": {"color": "brown"},
                       "fade_in_seconds": 0.5}]})
        self.assertTrue(manifest["quality"]["has_variation"])
        self.assertEqual(manifest["quality"]["warnings"], [])


class TestAmbienceAndEvents(AudioTestCase):
    """The two providers that turn a bed into a place."""

    def test_ambience_element_renders_to_the_exact_length(self):
        manifest, out = self.compose({
            "target_seconds": 3, "normalize": False,
            "layers": [{"provider": "ambience",
                        "params": {"element": "fireplace"}}]})
        self.assertAlmostEqual(audio.probe_seconds(out), 3.0, delta=0.05)
        self.assertEqual(manifest["layers"][0]["parameters"]["element"], "fireplace")
        self.assertTrue(manifest["layers"][0]["commercial_use"])

    def test_unknown_ambience_element_is_refused_by_name(self):
        with self.assertRaises(audio.AudioError) as ctx:
            self.compose({"target_seconds": 1, "normalize": False,
                          "layers": [{"provider": "ambience",
                                      "params": {"element": "spaceship"}}]})
        self.assertIn("spaceship", str(ctx.exception))
        self.assertIn("fireplace", str(ctx.exception))

    def test_ambience_is_deterministic_for_the_same_seed(self):
        plan = {"target_seconds": 2, "normalize": False,
                "layers": [{"provider": "ambience",
                            "params": {"element": "ocean_surf", "seed": 7}}]}
        _, first = self.compose(plan, "a.wav")
        _, second = self.compose(plan, "b.wav")
        self.assertEqual(first.read_bytes(), second.read_bytes())

    def test_events_place_one_shots_without_changing_the_duration(self):
        manifest, out = self.compose({
            "target_seconds": 12, "normalize": False,
            "layers": [{"provider": "events",
                        "params": {"element": "drip", "every_seconds": 8}}]})
        self.assertAlmostEqual(audio.probe_seconds(out), 12.0, delta=0.05)
        params = manifest["layers"][0]["parameters"]
        self.assertGreaterEqual(params["events_per_block"], 1)

    def test_event_placement_never_lands_in_the_loop_seam(self):
        """Both ends of the pattern block are kept empty, so crossfading a
        repeat cannot cut an event in half."""
        offsets = audio._event_offsets(6, block_seconds=120.0, guard=10.0, seed=3)
        self.assertEqual(len(offsets), 6)
        self.assertGreaterEqual(min(offsets), 10.0)
        self.assertLessEqual(max(offsets), 110.0)
        self.assertEqual(offsets, sorted(offsets))

    def test_unknown_event_element_is_refused(self):
        with self.assertRaises(audio.AudioError) as ctx:
            self.compose({"target_seconds": 2, "normalize": False,
                          "layers": [{"provider": "events",
                                      "params": {"element": "car_horn"}}]})
        self.assertIn("car_horn", str(ctx.exception))


class TestLayerShapingAndDucking(AudioTestCase):

    def test_band_limiting_and_width_survive_the_mix(self):
        manifest, out = self.compose({
            "target_seconds": 3, "normalize": False,
            "layers": [{"provider": "noise", "params": {"color": "white"},
                        "lowpass_hz": 500, "width": 1.3,
                        "swell": {"rate_hz": 0.05, "depth": 0.3}}]})
        self.assertEqual(manifest["status"], "OK")
        measured = audio.measure(out)
        # A 500 Hz lowpass on white noise must leave the high band well
        # below the midrange; without the filter it sits above it.
        self.assertLess(measured["brightness_db"], -10)

    def test_ducking_names_a_layer_that_must_exist(self):
        with self.assertRaises(audio.AudioError) as ctx:
            self.compose({
                "target_seconds": 2, "normalize": False,
                "layers": [{"id": "bed", "provider": "noise",
                            "params": {"color": "brown"},
                            "duck_under": "voice"}]})
        self.assertIn("duck_under", str(ctx.exception))
        self.assertIn("voice", str(ctx.exception))

    def test_a_layer_cannot_duck_under_itself(self):
        with self.assertRaises(audio.AudioError) as ctx:
            self.compose({
                "target_seconds": 2, "normalize": False,
                "layers": [{"id": "bed", "provider": "noise",
                            "params": {"color": "brown"},
                            "duck_under": "bed"}]})
        self.assertIn("itself", str(ctx.exception))

    def test_ducked_bed_is_quieter_than_the_same_mix_unducked(self):
        base = {
            "target_seconds": 6, "normalize": False,
            "layers": [
                {"id": "bed", "provider": "noise", "params": {"color": "brown"},
                 "gain_db": -6},
                {"id": "voice", "provider": "tone",
                 "params": {"frequency": 220.0}, "gain_db": -6},
            ]}
        _, plain = self.compose(base, "plain.wav")
        ducked = json.loads(json.dumps(base))
        ducked["layers"][0]["duck_under"] = "voice"
        _, shaped = self.compose(ducked, "ducked.wav")
        self.assertLess(audio.mean_volume_db(shaped), audio.mean_volume_db(plain))


class TestMasteringAndMeasurement(AudioTestCase):

    def test_master_block_reaches_the_declared_loudness(self):
        _, out = self.compose({
            "target_seconds": 8,
            "master": {"lufs": -20.0, "true_peak_dbfs": -1.5},
            "layers": [{"provider": "ambience",
                        "params": {"element": "stream"}}]})
        measured = audio.measure(out)
        self.assertAlmostEqual(measured["integrated_lufs"], -20.0, delta=2.0)
        self.assertLessEqual(measured["true_peak_dbfs"], 0.0)

    def test_master_settings_prefer_the_explicit_block(self):
        settings = audio.master_settings(
            {"normalize": True, "target_lufs": -18.0, "master": {"lufs": -23.0}})
        self.assertEqual(settings["lufs"], -23.0)
        settings = audio.master_settings({"normalize": True, "target_lufs": -14.0})
        self.assertEqual(settings["lufs"], -14.0)

    def test_measurement_reports_what_it_measured_over(self):
        _, out = self.compose({
            "target_seconds": 6, "normalize": False,
            "layers": [{"provider": "noise", "params": {"color": "pink"}}]})
        measured = audio.measure(out, window_seconds=2.0)
        self.assertEqual(measured["measured_window"], "centred excerpt")
        self.assertAlmostEqual(measured["measured_seconds"], 2.0, delta=0.01)
        self.assertAlmostEqual(measured["duration_seconds"], 6.0, delta=0.05)

    def test_measurement_never_emits_non_finite_numbers(self):
        _, out = self.compose({
            "target_seconds": 3, "normalize": False,
            "layers": [{"provider": "noise", "params": {"color": "brown"}}]})
        blob = json.dumps(audio.measure(out))
        self.assertNotIn("Infinity", blob)
        self.assertNotIn("NaN", blob)


class TestAssessment(unittest.TestCase):
    """The criteria an unattended run judges its own audio against."""

    def test_silence_blocks(self):
        findings = audio.assess({"integrated_lufs": -70.0, "measured_seconds": 60},
                                audio.criteria_for("background_sleep"))
        self.assertEqual(audio.verdict(findings), "BLOCKED")
        self.assertIn("silent", [f["code"] for f in findings])

    def test_clipping_blocks(self):
        findings = audio.assess(
            {"integrated_lufs": -23.0, "true_peak_dbfs": 0.6, "measured_seconds": 60},
            audio.criteria_for("background_sleep"))
        self.assertEqual(audio.verdict(findings), "BLOCKED")
        self.assertIn("clipping", [f["code"] for f in findings])

    def test_loudness_far_from_target_blocks_but_near_it_only_warns(self):
        criteria = audio.criteria_for("background_sleep")
        far = audio.assess({"integrated_lufs": -12.0, "measured_seconds": 60}, criteria)
        self.assertEqual(audio.verdict(far), "BLOCKED")
        near = audio.assess({"integrated_lufs": -25.5, "measured_seconds": 60}, criteria)
        self.assertEqual(audio.verdict(near), "REVIEW")

    def test_a_plan_that_opted_out_of_normalisation_is_not_judged_on_loudness(self):
        findings = audio.assess(
            {"integrated_lufs": -35.0, "measured_seconds": 60},
            audio.criteria_for("background_sleep"), {"normalized": False})
        self.assertEqual(audio.verdict(findings), "OK")
        self.assertIn("loudness_unmanaged", [f["code"] for f in findings])

    def test_brightness_is_judged_against_the_listening_context(self):
        measurement = {"integrated_lufs": -23.0, "brightness_db": 1.0,
                       "measured_seconds": 60}
        sleep = audio.assess(measurement, audio.criteria_for("background_sleep"))
        self.assertIn("too_bright", [f["code"] for f in sleep])
        narrated = audio.assess(
            {"integrated_lufs": -16.0, "brightness_db": 1.0, "measured_seconds": 60},
            audio.criteria_for("narration"))
        self.assertNotIn("too_bright", [f["code"] for f in narrated])

    def test_an_unmeasurable_track_blocks_rather_than_passing_quietly(self):
        findings = audio.assess({}, audio.criteria_for())
        self.assertEqual(audio.verdict(findings), "BLOCKED")

    def test_passing_every_check_never_claims_the_audio_is_good(self):
        findings = audio.assess(
            {"integrated_lufs": -23.0, "true_peak_dbfs": -2.0,
             "brightness_db": -12.0, "loudness_range_lu": 3.0,
             "measured_seconds": 60},
            audio.criteria_for("background_sleep"), {"has_variation": True})
        self.assertEqual(audio.verdict(findings), "OK")
        self.assertEqual(findings, [])


class TestBlockingVersusAdvisory(AudioTestCase):
    """Only findings with no legitimate reading may hold up review."""

    def test_a_measured_defect_reaches_the_blocking_channel(self):
        """A master target that contradicts the listening context is a real
        defect, measurable after the fact, and must hold up review."""
        manifest, _ = self.compose({
            "target_seconds": 8,
            "listening_context": "background_sleep",
            "master": {"lufs": -12.0},
            "layers": [{"provider": "ambience", "params": {"element": "stream"},
                        "fade_in_seconds": 1}]})
        codes = [f["code"] for f in manifest["assessment"]["findings"]]
        self.assertIn("loudness_far_off", codes)
        self.assertEqual(manifest["assessment"]["verdict"], "BLOCKED")
        self.assertTrue(manifest["quality"]["warnings"])

    def test_a_matter_of_degree_stays_advisory(self):
        manifest, _ = self.compose({
            "target_seconds": 6,
            "listening_context": "background_sleep",
            "master": {"lufs": -23.0},
            "layers": [{"provider": "noise", "params": {"color": "white"},
                        "fade_in_seconds": 1}]})
        codes = [f["code"] for f in manifest["assessment"]["findings"]]
        self.assertIn("too_bright", codes)
        self.assertEqual(manifest["quality"]["warnings"], [])
        self.assertTrue(manifest["quality"]["advisories"])


class TestValidationAndFailure(AudioTestCase):

    def test_missing_source_file_is_reported(self):
        with self.assertRaises(audio.AudioError) as ctx:
            self.compose({"target_seconds": 2, "layers": [
                {"provider": "file", "params": {
                    "path": str(self.tmp / "nope.wav"), "license": CLEARED_LICENCE}}]})
        self.assertIn("not found", str(ctx.exception))

    def test_corrupt_audio_file_is_rejected(self):
        bad = self.tmp / "corrupt.wav"
        bad.write_bytes(b"RIFF not actually a wave file")
        with self.assertRaises(audio.AudioError) as ctx:
            self.compose({"target_seconds": 2, "layers": [
                {"provider": "file", "params": {"path": str(bad), "license": CLEARED_LICENCE}}]})
        self.assertIn("unreadable", str(ctx.exception))

    def test_unknown_provider_is_rejected(self):
        with self.assertRaises(audio.AudioError) as ctx:
            self.compose({"target_seconds": 2,
                          "layers": [{"provider": "hologram", "params": {}}]})
        self.assertIn("unknown provider", str(ctx.exception))

    def test_invalid_target_duration_is_rejected(self):
        with self.assertRaises(audio.AudioError) as ctx:
            self.compose({"target_seconds": 0,
                          "layers": [{"provider": "noise", "params": {}}]})
        self.assertIn("target_seconds", str(ctx.exception))

    def test_empty_layer_list_is_rejected(self):
        with self.assertRaises(audio.AudioError):
            self.compose({"target_seconds": 2, "layers": []})

    def test_silent_output_is_refused(self):
        """A track that is effectively silence is a failed render, not a result."""
        with self.assertRaises(audio.AudioError) as ctx:
            self.compose({"target_seconds": 2, "normalize": False,
                          "layers": [{"provider": "silence", "params": {}}]})
        self.assertIn("silent", str(ctx.exception))

    def test_invalid_noise_colour_is_rejected(self):
        with self.assertRaises(audio.AudioError) as ctx:
            self.compose({"target_seconds": 2,
                          "layers": [{"provider": "noise", "params": {"color": "chartreuse"}}]})
        self.assertIn("noise color", str(ctx.exception))


class TestLicensing(AudioTestCase):

    def test_file_without_licence_is_refused(self):
        src = self.make_wav("unlicensed.wav", 2.0)
        with self.assertRaises(audio.AudioError) as ctx:
            self.compose({"target_seconds": 2, "layers": [
                {"provider": "file", "params": {"path": str(src)}}]})
        self.assertIn("licence fields missing", str(ctx.exception))

    def test_non_commercial_file_is_refused(self):
        src = self.make_wav("nc.wav", 2.0)
        licence = dict(CLEARED_LICENCE, license="CC BY-NC 4.0", commercial_use=False)
        with self.assertRaises(audio.AudioError) as ctx:
            self.compose({"target_seconds": 2, "layers": [
                {"provider": "file", "params": {"path": str(src), "license": licence}}]})
        self.assertIn("commercial_use=false", str(ctx.exception))

    def test_generated_audio_is_marked_commercially_clear(self):
        manifest, _ = self.compose({
            "target_seconds": 2, "normalize": False,
            "layers": [{"provider": "noise", "params": {"color": "brown"}}]})
        self.assertTrue(manifest["commercial_use_cleared"])
        self.assertEqual(manifest["attributions_required"], [])

    def test_known_non_commercial_voices_are_blocked(self):
        """Voices whose datasets forbid commercial use must never be selectable."""
        self.assertIn("en_US-lessac-medium", audio.REJECTED_VOICES)
        self.assertIn("en_US-hfc_female-medium", audio.REJECTED_VOICES)
        for name in audio.REJECTED_VOICES:
            with self.assertRaises(audio.AudioError) as ctx:
                self.compose({"target_seconds": 2, "layers": [
                    {"provider": "tts", "params": {"text": "hello", "voice": name}}]})
            self.assertIn("not usable", str(ctx.exception))

    def test_every_approved_voice_permits_commercial_use(self):
        for name, voice in audio.VOICES.items():
            self.assertTrue(voice["commercial_use"], f"{name} is not commercially usable")
            self.assertTrue(voice["evidence"], f"{name} has no licence evidence recorded")


class TestDeterminism(AudioTestCase):

    def test_same_plan_produces_identical_bytes(self):
        plan = {"target_seconds": 3, "normalize": True, "target_lufs": -20,
                "layers": [{"provider": "noise", "params": {"color": "brown"}},
                           {"provider": "tone", "params": {"frequency": 100}, "gain_db": -10}]}
        _, first = self.compose(plan, name="a.wav")
        _, second = self.compose(plan, name="b.wav")
        self.assertEqual(first.read_bytes(), second.read_bytes(),
                         "composing the same plan twice must be byte-identical")


class TestNarration(AudioTestCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        voice = audio.VOICES["en_US-libritts_r-medium"]
        if not (audio.VOICES_DIR / voice["model"]).is_file():
            raise unittest.SkipTest("Piper voice model not installed")
        if not audio._piper_python():
            raise unittest.SkipTest("piper-tts not installed")

    def test_narration_records_licence_and_attribution(self):
        manifest, out = self.compose({
            "target_seconds": 8, "normalize": False,
            "layers": [{"id": "vo", "provider": "tts",
                        "params": {"text": "This is a short narration test."}}]})
        layer = manifest["layers"][0]
        self.assertEqual(layer["license"], "CC BY 4.0")
        self.assertTrue(layer["commercial_use"])
        self.assertTrue(layer["attribution_required"])
        self.assertTrue(manifest["attributions_required"],
                        "a CC BY voice must surface an attribution string")
        self.assertAlmostEqual(audio.probe_seconds(out), 8.0, delta=0.05,
                               msg="narration should be padded to the target length")

    def test_narration_is_deterministic_with_zero_noise(self):
        plan = {"target_seconds": 6, "normalize": False,
                "layers": [{"provider": "tts", "params": {
                    "text": "Determinism check.", "noise_scale": 0.0, "noise_w_scale": 0.0}}]}
        _, first = self.compose(plan, name="n1.wav")
        _, second = self.compose(plan, name="n2.wav")
        self.assertEqual(first.read_bytes(), second.read_bytes())

    def test_empty_narration_text_is_rejected(self):
        with self.assertRaises(audio.AudioError) as ctx:
            self.compose({"target_seconds": 3,
                          "layers": [{"provider": "tts", "params": {"text": "   "}}]})
        self.assertIn("non-empty", str(ctx.exception))


class TestNarrationStatus(unittest.TestCase):
    """The readiness answer behind the control center's narration light.

    Needs no voice model and no piper-tts: the point of these tests is what
    the status says when one of them is missing, which is the case the old
    hardcoded green light got wrong.
    """

    def setUp(self):
        self.voices_dir = audio.VOICES_DIR
        self.piper_python = audio._piper_python
        self.addCleanup(self._restore)
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        audio.VOICES_DIR = self.tmp

    def _restore(self):
        audio.VOICES_DIR = self.voices_dir
        audio._piper_python = self.piper_python

    def _install_voice(self):
        (self.tmp / audio.VOICES[audio.DEFAULT_VOICE]["model"]).write_bytes(b"onnx")

    def test_missing_voice_model_is_unavailable_and_says_where(self):
        audio._piper_python = lambda: "/usr/bin/python3"
        status = audio.narration_status()
        self.assertFalse(status["available"])
        self.assertIn(str(self.tmp), status["detail"])

    def test_missing_package_is_unavailable_and_says_how_to_install(self):
        self._install_voice()
        audio._piper_python = lambda: None
        status = audio.narration_status()
        self.assertFalse(status["available"])
        self.assertIn("pip install piper-tts", status["detail"])

    def test_available_only_when_both_halves_are_present(self):
        self._install_voice()
        audio._piper_python = lambda: "/usr/bin/python3"
        status = audio.narration_status()
        self.assertTrue(status["available"])
        self.assertEqual(status["engine"], "piper")
        self.assertEqual(status["voice"], audio.DEFAULT_VOICE)

    def test_non_commercial_voice_is_refused_with_its_reason(self):
        self._install_voice()
        audio._piper_python = lambda: "/usr/bin/python3"
        rejected = next(iter(audio.REJECTED_VOICES))
        status = audio.narration_status(rejected)
        self.assertFalse(status["available"])
        self.assertIn(audio.REJECTED_VOICES[rejected], status["detail"])

    def test_host_capabilities_reports_the_same_answer(self):
        """The control center must not be able to disagree with the domain."""
        self._install_voice()
        audio._piper_python = lambda: None
        import experiment
        capabilities = experiment.host_capabilities()
        self.assertFalse(capabilities["narration_available"])
        self.assertEqual(capabilities["narration"], audio.narration_status())


class TestProjectIntegration(AudioTestCase):

    def setUp(self):
        super().setUp()
        self.video_id = f"pytest-audio-{uuid.uuid4().hex[:8]}"
        self.pdir = ROOT / "projects" / self.video_id
        self.addCleanup(lambda: shutil.rmtree(self.pdir, ignore_errors=True))

    def cm(self, *args):
        return subprocess.run([str(CLI), *args], capture_output=True, text=True, cwd=str(ROOT))

    def scaffold_with_plan(self, layers):
        proc = subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "experiment.py"), "scaffold",
             "sleep-brown-noise-dark", self.video_id, "--duration", "5"],
            capture_output=True, text=True, cwd=str(ROOT))
        self.assertEqual(proc.returncode, 0, proc.stderr)
        meta_path = self.pdir / "metadata.json"
        meta = json.loads(meta_path.read_text())
        meta["audio_plan"]["composition"] = {"normalize": False, "layers": layers}
        meta_path.write_text(json.dumps(meta, indent=2))

    def test_audio_command_builds_track_and_updates_spec(self):
        self.scaffold_with_plan([{"id": "bed", "provider": "noise",
                                  "params": {"color": "brown"}}])
        proc = self.cm("audio", self.video_id)
        self.assertEqual(proc.returncode, 0, proc.stderr)

        track = self.pdir / "audio" / "track.wav"
        self.assertTrue(track.is_file(), "no audio track written")
        self.assertAlmostEqual(audio.probe_seconds(track), 5.0, delta=0.05,
                               msg="audio length should follow the video duration")

        spec = json.loads((self.pdir / "video_spec.json").read_text())
        self.assertEqual(spec["audio"]["file"], "audio/track.wav",
                         "the render contract should point at the generated track")

        manifest = json.loads((self.pdir / "audio" / "audio_manifest.json").read_text())
        self.assertTrue(manifest["commercial_use_cleared"])

        meta = json.loads((self.pdir / "metadata.json").read_text())
        self.assertEqual(meta["status"]["audio"], "OK")
        self.assertEqual(meta["provenance"]["audio"]["provider"], "noise")

    def test_quality_warnings_are_persisted_not_silently_discarded(self):
        """A single flat layer's quality warning must survive into
        metadata, not just the manifest on disk - the dashboard has no
        other way to surface it."""
        self.scaffold_with_plan([{"id": "bed", "provider": "noise",
                                  "params": {"color": "brown"}}])
        proc = self.cm("audio", self.video_id)
        self.assertEqual(proc.returncode, 0, proc.stderr)

        meta = json.loads((self.pdir / "metadata.json").read_text())
        quality = meta["provenance"]["audio"]["quality"]
        self.assertFalse(quality["has_variation"])
        self.assertTrue(quality["warnings"])

    def test_project_without_audio_plan_fails_clearly(self):
        proc = subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "experiment.py"), "scaffold",
             "sleep-brown-noise-dark", self.video_id, "--duration", "5"],
            capture_output=True, text=True, cwd=str(ROOT))
        self.assertEqual(proc.returncode, 0, proc.stderr)
        proc = self.cm("audio", self.video_id)
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("No audio plan", proc.stderr)

    def test_uncleared_audio_blocks_ready_for_review(self):
        """The rights gate must stop unclearable audio reaching review."""
        self.scaffold_with_plan([{"id": "bed", "provider": "noise",
                                  "params": {"color": "brown"}}])
        self.assertEqual(self.cm("audio", self.video_id).returncode, 0)

        # Simulate a layer whose rights could not be established.
        manifest_path = self.pdir / "audio" / "audio_manifest.json"
        manifest = json.loads(manifest_path.read_text())
        manifest["commercial_use_cleared"] = False
        manifest["layers"][0]["commercial_use"] = False
        manifest_path.write_text(json.dumps(manifest, indent=2))

        meta_path = self.pdir / "metadata.json"
        meta = json.loads(meta_path.read_text())
        meta["description"] = "Description present so only audio rights can block."
        meta_path.write_text(json.dumps(meta, indent=2))

        subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi",
                        "-i", "color=c=0x1a1a2e:s=640x360:d=1", "-frames:v", "1",
                        str(self.pdir / "images" / "dark.png")], check=True, capture_output=True)

        proc = self.cm("run", self.video_id)
        self.assertEqual(proc.returncode, 2,
                         "uncleared audio should yield NEEDS_ATTENTION, not success")
        package = json.loads((self.pdir / "output" / "publication_package.json").read_text())
        self.assertEqual(package["status"], "NEEDS_ATTENTION")
        self.assertTrue(any("not cleared for commercial use" in issue
                            for issue in package["blocking_issues"]))


if __name__ == "__main__":
    unittest.main(verbosity=2)
