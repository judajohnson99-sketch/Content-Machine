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
