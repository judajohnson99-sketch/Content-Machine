"""Kdenlive/MLT project lowering and render proof."""
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import kdenlive


@unittest.skipUnless(shutil.which("melt"), "MLT required")
class KdenliveTests(unittest.TestCase):
    def test_overlapping_stills_lower_to_editable_tracks_and_render(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            project = root / "dreamdrip.kdenlive"
            output = root / "review.mp4"
            result = kdenlive.build_project(
                project, width=320, height=180, fps=12,
                clips=[
                    kdenlive.Clip(ROOT / "tests/fixtures/images/01_red.png", 0, 60,
                                  label="scene-1"),
                    kdenlive.Clip(ROOT / "tests/fixtures/images/02_orange.png", 48, 72,
                                  label="scene-2"),
                ],
                audio_clips=[kdenlive.Clip(
                    ROOT / "tests/fixtures/audio/test_tone.wav", 0, 120,
                    track="audio", label="bed")],
            )
            self.assertEqual(result["duration_frames"], 120)
            xml = ET.parse(project).getroot()
            lanes = [t for t in xml.findall("tractor")
                     if t.find("property[@name='kdenlive:track_name']") is not None]
            self.assertEqual(len(lanes), 3)
            self.assertTrue(all(len(t.findall("track")) == 2 for t in lanes))
            self.assertTrue(any(p.text == "frei0r.cairoblend"
                                for p in xml.findall(".//property[@name='mlt_service']")))
            self.assertTrue(kdenlive.verify_project(project)["verified"])
            rendered = kdenlive.render_project(project, output)
            self.assertGreater(rendered["bytes"], 1000)


if __name__ == "__main__":
    unittest.main()
