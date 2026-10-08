#!/usr/bin/env python3
"""End-to-end tests for the video render pipeline.

Drives scripts/render.py through its CLI (the real entry point) against
the checked-in fixtures, then verifies the resulting MP4 with ffprobe.
Standard library only — run with:

    python3 -m unittest discover -s tests -v
"""
import json
import shutil
import struct
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
RENDER = ROOT / "scripts" / "render.py"
IMAGES = ROOT / "tests" / "fixtures" / "images"
AUDIO = ROOT / "tests" / "fixtures" / "audio" / "test_tone.wav"

# The fixture tone is 12s; rendering longer than this exercises looping.
AUDIO_FIXTURE_SECONDS = 12


def require_tool(name):
    path = shutil.which(name)
    if not path:
        raise unittest.SkipTest(f"{name} not found on PATH")
    return path


class RenderTestCase(unittest.TestCase):
    """Shared helpers for driving the renderer and probing its output."""

    @classmethod
    def setUpClass(cls):
        require_tool("ffmpeg")
        require_tool("ffprobe")
        if not RENDER.is_file():
            raise unittest.SkipTest(f"renderer not found: {RENDER}")
        if not IMAGES.is_dir() or not any(IMAGES.iterdir()):
            raise unittest.SkipTest(
                f"image fixtures missing: {IMAGES} "
                "(run: python3 scripts/make_test_fixtures.py)"
            )
        if not AUDIO.is_file():
            raise unittest.SkipTest(
                f"audio fixture missing: {AUDIO} "
                "(run: python3 scripts/make_test_fixtures.py)"
            )

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)

    def spec(self, **overrides):
        """A small, fast baseline spec; override any field per test."""
        spec = {
            "width": 480,
            "height": 270,
            "fps": 24,
            "duration_seconds": 5,
            "images": {"source_dir": str(IMAGES), "seconds_per_image": 2.0},
            "audio": {"file": str(AUDIO)},
            "ken_burns": {"enabled": True, "zoom_start": 1.0, "zoom_end": 1.1},
            "crossfade": {"enabled": True, "duration_seconds": 0.5},
        }
        spec.update(overrides)
        return spec

    def render(self, spec, name="out.mp4", expect_success=True):
        """Run the renderer CLI; return (output_path, CompletedProcess)."""
        spec_path = self.tmp / f"{Path(name).stem}.json"
        out_path = self.tmp / name
        spec_path.write_text(json.dumps(spec))
        proc = subprocess.run(
            ["python3", str(RENDER), "--spec", str(spec_path), "--output", str(out_path)],
            capture_output=True, text=True, cwd=str(ROOT),
        )
        if expect_success:
            self.assertEqual(
                proc.returncode, 0,
                f"render failed (exit {proc.returncode}).\nstderr:\n{proc.stderr}",
            )
        return out_path, proc

    def probe(self, path):
        proc = subprocess.run(
            ["ffprobe", "-v", "error", "-print_format", "json",
             "-show_format", "-show_streams", str(path)],
            capture_output=True, text=True,
        )
        self.assertEqual(
            proc.returncode, 0,
            f"ffprobe could not read {path}.\nstderr:\n{proc.stderr}",
        )
        return json.loads(proc.stdout)

    def stream(self, probed, codec_type):
        for s in probed["streams"]:
            if s["codec_type"] == codec_type:
                return s
        self.fail(f"no {codec_type} stream found in output")

    def mean_volume_db(self, path, start, duration):
        """Mean dBFS over a segment, via ffmpeg's volumedetect filter."""
        proc = subprocess.run(
            ["ffmpeg", "-hide_banner", "-ss", str(start), "-t", str(duration),
             "-i", str(path), "-af", "volumedetect", "-f", "null", "-"],
            capture_output=True, text=True,
        )
        for line in proc.stderr.splitlines():
            if "mean_volume:" in line:
                return float(line.split("mean_volume:")[1].strip().split()[0])
        self.fail(f"volumedetect produced no reading for {path}")


class TestRenderedOutput(RenderTestCase):

    def test_produces_youtube_ready_mp4(self):
        """The core contract: an H.264/AAC MP4 matching the requested spec."""
        spec = self.spec(width=640, height=360, fps=30, duration_seconds=6)
        out, _ = self.render(spec)

        self.assertTrue(out.is_file(), f"expected output MP4 at {out}")
        self.assertGreater(out.stat().st_size, 1024, "output MP4 is suspiciously small")

        probed = self.probe(out)
        video = self.stream(probed, "video")
        audio = self.stream(probed, "audio")

        self.assertAlmostEqual(
            float(probed["format"]["duration"]), 6.0, delta=0.1,
            msg="output duration does not match duration_seconds",
        )
        self.assertEqual(video["width"], 640, "output width does not match spec")
        self.assertEqual(video["height"], 360, "output height does not match spec")
        self.assertEqual(video["r_frame_rate"], "30/1", "output frame rate does not match spec")
        self.assertEqual(video["codec_name"], "h264", "video codec must be H.264 for YouTube")
        self.assertEqual(video["pix_fmt"], "yuv420p", "pixel format must be yuv420p for compatibility")
        self.assertEqual(audio["codec_name"], "aac", "audio codec must be AAC for YouTube")
        self.assertEqual(int(audio["sample_rate"]), 48000, "audio should be 48kHz")

    def test_moov_atom_precedes_mdat_for_streaming(self):
        """+faststart must actually move moov ahead of mdat."""
        out, _ = self.render(self.spec())
        order = []
        with open(out, "rb") as f:
            offset = 0
            while True:
                header = f.read(8)
                if len(header) < 8:
                    break
                size = struct.unpack(">I", header[:4])[0]
                order.append(header[4:8].decode("latin1"))
                if size == 1:
                    size = struct.unpack(">Q", f.read(8))[0]
                if size == 0:
                    break
                offset += size
                f.seek(offset)
        self.assertIn("moov", order, "MP4 has no moov atom")
        self.assertIn("mdat", order, "MP4 has no mdat atom")
        self.assertLess(
            order.index("moov"), order.index("mdat"),
            f"moov must precede mdat for progressive playback; got {order}",
        )

    def test_audio_loops_when_shorter_than_video(self):
        """Audio past the source file's end must be looped, not silence."""
        duration = AUDIO_FIXTURE_SECONDS + 8
        spec = self.spec(duration_seconds=duration, images={
            "source_dir": str(IMAGES), "seconds_per_image": 4.0,
        })
        out, _ = self.render(spec)

        within = self.mean_volume_db(out, start=1, duration=6)
        beyond = self.mean_volume_db(out, start=AUDIO_FIXTURE_SECONDS + 2, duration=5)

        self.assertGreater(
            beyond, -60.0,
            f"audio past {AUDIO_FIXTURE_SECONDS}s is effectively silent "
            f"({beyond} dB) — looping did not happen",
        )
        self.assertAlmostEqual(
            beyond, within, delta=3.0,
            msg=f"looped audio level ({beyond} dB) differs from source ({within} dB)",
        )

    def test_images_cycle_to_fill_requested_duration(self):
        """Fewer images than slots must repeat rather than truncate the video."""
        n_images = len(list(IMAGES.iterdir()))
        # Deliberately demand more slots than there are source images.
        duration = (n_images + 3) * 2
        spec = self.spec(duration_seconds=duration)
        out, proc = self.render(spec)

        probed = self.probe(out)
        self.assertAlmostEqual(
            float(probed["format"]["duration"]), float(duration), delta=0.1,
            msg="cycled render did not reach the requested duration",
        )
        self.assertIn("image slot", proc.stderr, "expected image-cycling to be logged")

    def test_render_is_deterministic(self):
        """Same spec and inputs must yield byte-identical output."""
        spec = self.spec()
        first, _ = self.render(spec, name="a.mp4")
        second, _ = self.render(spec, name="b.mp4")
        self.assertEqual(
            first.read_bytes(), second.read_bytes(),
            "two renders of the same spec produced different bytes",
        )

    def test_crossfade_disabled_still_renders(self):
        """The concat path (no crossfade) is a distinct branch; exercise it."""
        spec = self.spec(crossfade={"enabled": False})
        out, _ = self.render(spec)
        probed = self.probe(out)
        self.assertAlmostEqual(float(probed["format"]["duration"]), 5.0, delta=0.1)

    def test_single_image_renders(self):
        """One image means no transitions at all; must not fail."""
        single = self.tmp / "one_image"
        single.mkdir()
        shutil.copy(sorted(IMAGES.iterdir())[0], single)
        spec = self.spec(
            duration_seconds=3,
            images={"source_dir": str(single), "seconds_per_image": 3.0},
        )
        out, _ = self.render(spec)
        probed = self.probe(out)
        self.assertAlmostEqual(float(probed["format"]["duration"]), 3.0, delta=0.1)


class TestSpecValidation(RenderTestCase):

    def assertRejects(self, spec, *expected_fragments):
        out, proc = self.render(spec, expect_success=False)
        self.assertNotEqual(proc.returncode, 0, "invalid spec should exit non-zero")
        self.assertFalse(out.exists(), "no MP4 should be written for an invalid spec")
        for fragment in expected_fragments:
            self.assertIn(
                fragment, proc.stderr,
                f"expected error mentioning {fragment!r}.\nstderr:\n{proc.stderr}",
            )

    def test_reports_every_error_in_one_pass(self):
        """Validation should not stop at the first problem."""
        spec = self.spec(
            width=481,
            images={"source_dir": str(self.tmp / "nope"), "seconds_per_image": 2.0},
            audio={"file": str(self.tmp / "missing.wav")},
            crossfade={"enabled": True, "duration_seconds": 99},
        )
        self.assertRejects(spec, "width", "images.source_dir", "audio.file", "crossfade")

    def test_missing_audio_file_is_named(self):
        self.assertRejects(self.spec(audio={"file": "/nonexistent/track.wav"}),
                           "audio.file", "/nonexistent/track.wav")

    def test_empty_image_directory_is_rejected(self):
        empty = self.tmp / "empty"
        empty.mkdir()
        self.assertRejects(
            self.spec(images={"source_dir": str(empty), "seconds_per_image": 2.0}),
            "No supported images",
        )

    def test_odd_dimensions_rejected_for_h264(self):
        self.assertRejects(self.spec(height=271), "height", "even")

    def test_crossfade_longer_than_image_duration_rejected(self):
        spec = self.spec(
            images={"source_dir": str(IMAGES), "seconds_per_image": 2.0},
            crossfade={"enabled": True, "duration_seconds": 2.0},
        )
        self.assertRejects(spec, "crossfade.duration_seconds")

    def test_excessive_image_slots_rejected_before_oom(self):
        """Too many concurrent inputs exhaust memory; fail readably, not by SIGKILL."""
        spec = self.spec(duration_seconds=720, images={
            "source_dir": str(IMAGES), "seconds_per_image": 25.0})
        out, proc = self.render(spec, name="toomany.mp4", expect_success=False)
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("image slots", proc.stderr)
        self.assertIn("seconds_per_image", proc.stderr,
                      "the error should say how to fix it")
        self.assertFalse(out.exists())

    def test_slot_count_matches_the_renderer_maths(self):
        import render as render_mod
        # No crossfade: plain division.
        self.assertEqual(render_mod.required_image_slots(100, 10, 0), 10)
        # With crossfade each clip after the first advances by (clip - xfade).
        self.assertEqual(render_mod.required_image_slots(100, 10, 2), 13)
        # Never fewer than one slot.
        self.assertEqual(render_mod.required_image_slots(1, 600, 0), 1)

    def test_slot_limit_allows_a_normal_long_form_spec(self):
        """The guard must not block legitimate long ambient specs."""
        import render as render_mod
        slots = render_mod.required_image_slots(10800, 600, 4)
        self.assertLessEqual(slots, render_mod.MAX_IMAGE_SLOTS,
                             "a 3h ambient spec should still be renderable")

    def test_malformed_json_is_reported(self):
        bad = self.tmp / "bad.json"
        bad.write_text("{ not valid json")
        proc = subprocess.run(
            ["python3", str(RENDER), "--spec", str(bad)],
            capture_output=True, text=True, cwd=str(ROOT),
        )
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("not valid JSON", proc.stderr)

    def test_missing_spec_file_is_reported(self):
        proc = subprocess.run(
            ["python3", str(RENDER), "--spec", str(self.tmp / "absent.json")],
            capture_output=True, text=True, cwd=str(ROOT),
        )
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("Spec file not found", proc.stderr)


class TestLoopedLongForm(RenderTestCase):
    """A long video is one unique cycle of shots, encoded once, repeated by
    stream copy to the full length - with an honest record that it is."""

    FPS = 12

    def loop_spec(self, to_seconds=20.0, closing=0.5, **overrides):
        images = sorted(IMAGES.iterdir())
        kinds = ("drift", "parallax", "zoom_in")
        scenes = [{
            "scene_id": f"s{i + 1:02d}",
            "duration_seconds": 2.0,
            "image": str(images[i]),
            "motion": {"kind": kinds[i], "fit": "cover"},
            "transition": {"kind": "crossfade",
                           "duration_seconds": closing if i == 2 else 0.5},
        } for i in range(3)]
        spec = self.spec(width=160, height=90, fps=self.FPS, duration_seconds=to_seconds,
                         scenes=scenes, loop={"to_seconds": to_seconds})
        spec.update(overrides)
        return spec

    def normalized(self, raw):
        import render
        return render.validate_and_normalize(raw, base_dir=ROOT)

    def test_spec_states_the_cycle_and_the_full_length(self):
        spec = self.normalized(self.loop_spec(to_seconds=10800.0))
        self.assertEqual(spec["loop"]["unique_scenes"], 3)
        self.assertAlmostEqual(spec["loop"]["cycle_seconds"], 4.5)
        self.assertEqual(spec["timeline_seconds"], 10800.0)
        self.assertAlmostEqual(spec["loop"]["repeats"], 2400.0)

    def test_circular_piece_plan_sums_to_the_cycle(self):
        import motion
        import render
        spec = self.normalized(self.loop_spec())
        plan = render._scene_piece_plan(spec["scenes"], self.FPS, circular=True)
        frames = sum(p["body_frames"] + p["tail_frames"] for p in plan)
        self.assertEqual(frames, round(motion.cycle_seconds(spec["scenes"]) * self.FPS))
        self.assertEqual(plan[0]["lead_frames"], plan[-1]["tail_frames"])
        self.assertTrue(all(p["body_frames"] > 0 for p in plan))

    def test_a_cycle_too_short_for_its_closing_dissolve_is_refused(self):
        import render
        raw = self.loop_spec()
        raw["scenes"] = raw["scenes"][:1]
        raw["scenes"][0]["transition"]["duration_seconds"] = 1.0
        with self.assertRaises(render.SpecValidationError) as caught:
            self.normalized(raw)
        self.assertTrue(any("crossfade" in e for e in caught.exception.errors))

    def test_loop_needs_scenes_and_a_sane_length(self):
        import render
        for raw in (self.spec(loop={"to_seconds": 60}),
                    self.loop_spec(loop={"to_seconds": -1}),
                    self.loop_spec(loop={"to_seconds": render.MAX_LOOP_SECONDS + 1}),
                    self.loop_spec(loop="forever")):
            with self.assertRaises(render.SpecValidationError):
                self.normalized(raw)

    def test_layout_is_whole_copies_plus_an_exact_remainder(self):
        import render
        self.assertEqual(render.loop_layout(54, 20.0, 12), (4, 24))   # 240 frames
        self.assertEqual(render.loop_layout(120, 30.0, 12), (3, 0))   # exact
        self.assertEqual(render.loop_layout(500, 10.0, 12), (0, 120))  # under one cycle

    def test_renders_full_length_from_a_stream_copied_cycle(self):
        import render
        out, _ = self.render(self.loop_spec(to_seconds=20.0), name="looped.mp4")
        probed = self.probe(out)
        self.assertAlmostEqual(float(probed["format"]["duration"]), 20.0, delta=0.05)
        video = self.stream(probed, "video")
        self.assertEqual((video["width"], video["height"]), (160, 90))
        self.stream(probed, "audio")
        frames = subprocess.run(
            ["ffprobe", "-v", "error", "-count_frames", "-select_streams", "v",
             "-show_entries", "stream=nb_read_frames", "-of", "csv=p=0", str(out)],
            capture_output=True, text=True).stdout.strip()
        self.assertEqual(int(frames), 240)

        record = render.load_render_provenance(out)
        self.assertEqual(record["mode"], "looped_cycle")
        self.assertEqual(record["unique_scenes"], 3)
        self.assertEqual(record["cycle_frames"], 54)
        self.assertEqual((record["whole_copies"], record["remainder_frames"]), (4, 24))
        self.assertEqual(record["total_frames"], 240)
        self.assertEqual(record["motions"], {"drift": 1, "parallax": 1, "zoom_in": 1})

        # The second copy starts on exactly the picture the first did: the
        # repeat is the same encoded frames, not a re-render.
        md5 = subprocess.run(
            ["ffmpeg", "-v", "error", "-i", str(out), "-map", "0:v",
             "-vf", "select='eq(n\\,0)+eq(n\\,54)+eq(n\\,108)'", "-vsync", "0",
             "-f", "framemd5", "-"], capture_output=True, text=True).stdout
        hashes = [line.split(",")[-1].strip() for line in md5.splitlines()
                  if line and not line.startswith("#")]
        self.assertEqual(len(hashes), 3)
        self.assertEqual(len(set(hashes)), 1)

    def test_qc_checks_the_full_length_and_samples_the_black_scan(self):
        """Duration is checked on the whole file; on a long one the per-frame
        black scan covers the first cycle, every seam and spread windows.
        The long-file threshold is lowered so a 20s file stands in for 3h."""
        import qc
        out, _ = self.render(self.loop_spec(to_seconds=20.0), name="looped.mp4")
        saved = (qc.FULL_SCAN_MAX_SECONDS, qc.SAMPLE_WINDOW_SECONDS, qc.SAMPLE_WINDOWS)
        qc.FULL_SCAN_MAX_SECONDS, qc.SAMPLE_WINDOW_SECONDS, qc.SAMPLE_WINDOWS = 5.0, 1.0, 3
        self.addCleanup(setattr, qc, "SAMPLE_WINDOWS", saved[2])
        self.addCleanup(setattr, qc, "SAMPLE_WINDOW_SECONDS", saved[1])
        self.addCleanup(setattr, qc, "FULL_SCAN_MAX_SECONDS", saved[0])
        report = qc.qc_video(out, expected={"width": 160, "height": 90, "fps": self.FPS,
                                            "duration_seconds": 20.0})
        self.assertEqual(report["status"], "PASS", report["failures"])
        black = next(c for c in report["checks"] if c["check"] == "no_long_black_segment")
        self.assertIn("window(s)", black["detail"])
        self.assertIn("every loop seam", black["detail"])

    def test_scan_windows_are_whole_file_until_long_then_cover_cycle_and_seams(self):
        import qc
        self.assertIsNone(qc.black_scan_windows(qc.FULL_SCAN_MAX_SECONDS))
        windows = qc.black_scan_windows(10800.0, {"cycle_seconds": 1200.0})
        self.assertEqual(windows[0][0], 0.0)
        self.assertGreaterEqual(windows[0][1], 1200.0)
        for seam in range(1200, 10800, 1200):
            self.assertTrue(any(start <= seam <= start + length
                                for start, length in windows), seam)
        self.assertLessEqual(windows[-1][0] + windows[-1][1], 10800.0)
        self.assertEqual(windows, qc.black_scan_windows(10800.0, {"cycle_seconds": 1200.0}))
        plain = qc.black_scan_windows(10800.0)
        self.assertEqual(len(plain), qc.SAMPLE_WINDOWS)

    def test_layered_moves_render_in_one_filter_graph_too(self):
        """Up to MAX_IMAGE_SLOTS scenes share one graph: the layered and fog
        chains must not collide on pad names there."""
        raw = self.loop_spec(to_seconds=5.0)
        del raw["loop"]
        raw["scenes"][0]["motion"]["ambient"] = "fog"
        raw["scenes"][2]["motion"] = {"kind": "parallax_in", "ambient": "fog"}
        raw["duration_seconds"] = 5.0
        out, _ = self.render(raw, name="graph.mp4")
        self.assertAlmostEqual(float(self.probe(out)["format"]["duration"]), 5.0, delta=0.15)

    def test_a_non_looped_render_clears_a_stale_loop_record(self):
        import render
        out = self.tmp / "plain.mp4"
        render.provenance_path(out).write_text("{}")
        self.render(self.spec(), name="plain.mp4")
        self.assertFalse(render.provenance_path(out).exists())


if __name__ == "__main__":
    unittest.main(verbosity=2)
