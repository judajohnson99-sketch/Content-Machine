"""Long-form composition, research influence, derived goals and deletion.

Four changes meet here, and each is tested at the level where it can
actually be wrong:

* a video is no longer limited to twenty-four shots, so a half-hour of
  ambient is an edit rather than a slideshow;
* sourced findings change pacing, movement and dissolves, not just prose;
* a plain-language goal becomes a runnable production without a human
  picking a concept;
* a production can be destroyed, and refuses to be when it has been
  published.

No network, no LLM: the search/model boundaries are injected, exactly as
the rest of this suite treats external services.
"""
import json
import shutil
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import goal as goal_mod  # noqa: E402
import make_visuals  # noqa: E402
import motion  # noqa: E402
import project  # noqa: E402
import qc  # noqa: E402
import render  # noqa: E402
import research  # noqa: E402
import storyboard  # noqa: E402


def _observation(topic, statement, url):
    return research._make_finding(
        "vid", "observation", topic, statement,
        source_url=url, source_title="t", confidence="VERIFIED")


class PiecewiseRenderTest(unittest.TestCase):
    """A scene plan longer than one filter graph can hold still renders, and
    lands on exactly the runtime the storyboard arithmetic promised."""

    def setUp(self):
        self.dir = Path(subprocess.run(
            ["mktemp", "-d"], capture_output=True, text=True).stdout.strip())
        self.addCleanup(shutil.rmtree, self.dir, True)

    def _spec(self, scene_count, per_scene=2.5, overlap=0.4, fps=24):
        for i in range(scene_count):
            subprocess.run(
                ["ffmpeg", "-y", "-v", "error", "-f", "lavfi",
                 "-i", f"color=c=0x{(i * 11) % 200 + 40:02x}3a6e:s=128x128",
                 "-frames:v", "1", str(self.dir / f"i{i:03d}.png")],
                check=True)
        subprocess.run(
            ["ffmpeg", "-y", "-v", "error", "-f", "lavfi",
             "-i", "sine=frequency=200:duration=5", "-ac", "2",
             str(self.dir / "a.wav")], check=True)
        scenes = [{
            "scene_id": f"s{i:03d}",
            "duration_seconds": per_scene,
            "image": f"i{i:03d}.png",
            "motion": {"kind": motion.MOTIONS[i % len(motion.MOTIONS)], "fit": "cover"},
            "transition": ({"kind": "cut", "duration_seconds": 0.0}
                           if i == scene_count - 1
                           else {"kind": "crossfade", "duration_seconds": overlap}),
        } for i in range(scene_count)]
        raw = {"width": 160, "height": 90, "fps": fps,
               "duration_seconds": per_scene * scene_count - overlap * (scene_count - 1),
               "images": {"source_dir": "."}, "audio": {"file": "a.wav"},
               "scenes": scenes}
        return render.validate_and_normalize(raw, base_dir=self.dir)

    def test_more_scenes_than_image_slots_is_accepted(self):
        spec = self._spec(render.MAX_IMAGE_SLOTS + 6)
        self.assertEqual(len(spec["scenes"]), render.MAX_IMAGE_SLOTS + 6)

    def test_piece_plan_frames_sum_to_the_finished_timeline(self):
        """The one arithmetic that must not drift: pieces are cut at absolute
        positions on the timeline, so three hundred of them still add up."""
        spec = self._spec(300, per_scene=6.0, overlap=0.75)
        plan = render._scene_piece_plan(spec["scenes"], spec["fps"])
        assembled = sum(p["body_frames"] + p["tail_frames"] for p in plan)
        expected = round(motion.timeline_seconds(spec["scenes"]) * spec["fps"])
        self.assertEqual(assembled, expected)
        self.assertTrue(all(p["body_frames"] > 0 for p in plan))

    def test_a_scene_shorter_than_its_overlaps_is_refused(self):
        with self.assertRaises(render.SpecValidationError) as caught:
            self._spec(30, per_scene=0.6, overlap=0.4)
        self.assertTrue(any("crossfade" in e for e in caught.exception.errors))

    def test_render_of_thirty_scenes_is_frame_exact(self):
        spec = self._spec(30)
        out = self.dir / "out.mp4"
        render.render(spec, out)
        self.assertTrue(out.is_file())
        probed = qc.probe(out)
        video = next(s for s in probed["streams"] if s["codec_type"] == "video")
        self.assertEqual(int(video["width"]), 160)
        self.assertAlmostEqual(
            float(probed["format"]["duration"]),
            motion.timeline_seconds(spec["scenes"]), delta=0.2)


class LoopedStoryboardRenderTest(unittest.TestCase):
    """Storyboard -> render contract -> render -> QC for a looped silent
    video: the storyboard's cycle reaches the renderer through the one
    place a board becomes a spec, and the finished file is the full length."""

    def setUp(self):
        self.dir = Path(subprocess.run(
            ["mktemp", "-d"], capture_output=True, text=True).stdout.strip())
        self.addCleanup(shutil.rmtree, self.dir, True)

    def test_a_five_minute_video_loops_a_one_minute_cycle(self):
        for i in range(3):
            subprocess.run(
                ["ffmpeg", "-y", "-v", "error", "-f", "lavfi",
                 "-i", "testsrc2=s=128x128", "-vf", f"hue=h={i * 90}",
                 "-frames:v", "1", str(self.dir / f"i{i}.png")], check=True)
        subprocess.run(
            ["ffmpeg", "-y", "-v", "error", "-f", "lavfi",
             "-i", "sine=frequency=200:duration=300", "-ac", "2",
             str(self.dir / "a.wav")], check=True)
        raw_spec = {"width": 96, "height": 54, "fps": 8, "duration_seconds": 300.0,
                    "unique_cycle_seconds": 60, "audio": {"file": "a.wav"}}
        metadata = {"concept": "slow rain for sleep", "script": "",
                    "visual_plan": {"prompt": "misty lake"}}
        board = storyboard.build_storyboard("vid-loop", metadata, raw_spec)
        self.assertEqual(board["loop"]["full_seconds"], 300.0)
        for index, scene in enumerate(board["scenes"]):
            scene["image"] = f"i{index % 3}.png"

        spec = project.storyboard_scene_spec(self.dir, raw_spec, board)
        self.assertEqual(spec["timeline_seconds"], 300.0)
        self.assertEqual(spec["loop"]["unique_scenes"], len(board["scenes"]))

        out = self.dir / "out.mp4"
        render.render(spec, out)
        report = qc.qc_video(out, expected={
            "width": 96, "height": 54, "fps": 8,
            "duration_seconds": spec["timeline_seconds"]})
        self.assertEqual(report["status"], "PASS", report["failures"])
        record = render.load_render_provenance(out)
        self.assertEqual(record["whole_copies"], 5)
        self.assertEqual(record["remainder_frames"], 0)
        kinds = set(record["motions"])
        self.assertTrue(kinds & {"drift", "parallax", "parallax_in"}, kinds)


class ProductionDirectiveTest(unittest.TestCase):
    """Findings become numbers the build uses - and only where a source
    actually said something."""

    def test_nothing_found_means_nothing_decided(self):
        directives = research.production_directives({"findings": []})
        self.assertEqual(directives["decisions"], [])
        self.assertEqual(directives["values"], {})

    def test_a_stated_shot_length_becomes_the_shot_length(self):
        findings = {"findings": [
            _observation("pacing", "Each shot is held for about 20 seconds.",
                         "https://a.test/1"),
            _observation("pacing", "Scenes change every 30 seconds in this format.",
                         "https://b.test/2"),
        ]}
        directives = research.production_directives(findings)
        decision = next(d for d in directives["decisions"]
                        if d["parameter"] == "seconds_per_scene")
        self.assertEqual(decision["value"], 25.0)
        self.assertEqual(decision["confidence"], "VERIFIED")
        self.assertEqual(sorted(decision["source_urls"]),
                         ["https://a.test/1", "https://b.test/2"])

    def test_described_pacing_is_inferred_not_claimed_as_stated(self):
        findings = {"findings": [
            _observation("pacing", "The visuals are slow and sustained throughout.",
                         "https://a.test/1"),
            _observation("editing", "Gentle, gradual movement with long dissolves.",
                         "https://b.test/2"),
        ]}
        directives = research.production_directives(findings)
        by_parameter = {d["parameter"]: d for d in directives["decisions"]}
        self.assertEqual(by_parameter["seconds_per_scene"]["confidence"], "INFERRED")
        self.assertIn("transition_seconds", by_parameter)

    def test_audio_layers_come_from_what_sources_named(self):
        findings = {"findings": [
            _observation("audio", "Steady rain over a soft ambient pad.",
                         "https://a.test/1"),
        ]}
        directives = research.production_directives(findings)
        decision = next(d for d in directives["decisions"]
                        if d["parameter"] == "audio_emphasis")
        self.assertIn("rain", decision["value"])
        self.assertIn("music", decision["value"])


class StoryboardDirectiveTest(unittest.TestCase):
    """The storyboard applies a directive, bounds it, and records that it did."""

    METADATA = {"visual_plan": {"prompt": "a field"}, "concept": "ambient"}
    SPEC = {"duration_seconds": 600.0, "width": 1920, "height": 1080, "fps": 30}

    def test_shot_length_decides_the_scene_count(self):
        board = storyboard.build_storyboard(
            "vid", self.METADATA, self.SPEC,
            directives={"seconds_per_scene": 20.0})
        self.assertEqual(len(board["scenes"]), 30)
        self.assertEqual(board["research_applied"]["seconds_per_scene"], 20.0)

    def test_no_directive_leaves_the_default_pacing_untouched(self):
        board = storyboard.build_storyboard("vid", self.METADATA, self.SPEC)
        self.assertEqual(board["research_applied"], {})
        self.assertEqual(
            len(board["scenes"]),
            round(self.SPEC["duration_seconds"] / storyboard.DEFAULT_SECONDS_PER_SCENE))

    def test_movement_style_changes_which_moves_are_used(self):
        still = storyboard.build_storyboard(
            "vid", self.METADATA, self.SPEC,
            directives={"seconds_per_scene": 30.0, "motion_style": "still"})
        self.assertEqual(still["research_applied"]["motion_style"], "still")
        self.assertTrue(set(s["motion"]["kind"] for s in still["scenes"])
                        <= set(storyboard.MOTION_STYLE_CYCLES["still"]))

    def test_an_absurd_dissolve_is_bounded_by_the_shot_it_leaves(self):
        board = storyboard.build_storyboard(
            "vid", self.METADATA, self.SPEC,
            directives={"seconds_per_scene": 20.0, "transition_seconds": 90.0})
        for scene in board["scenes"][:-1]:
            self.assertLess(scene["transition"]["duration_seconds"],
                            scene["duration_seconds"])


class ProceduralPlateVariationTest(unittest.TestCase):
    """Two shots that asked for different pictures must not come back as the
    same picture - the failure that made long storyboards pointless."""

    PROMPTS = ("a violet rain field", "a deep forest hollow",
               "a slow drifting nebula", "an amber lamplit haze")

    def setUp(self):
        self.dir = Path(subprocess.run(
            ["mktemp", "-d"], capture_output=True, text=True).stdout.strip())
        self.addCleanup(shutil.rmtree, self.dir, True)

    def test_distinct_prompts_make_distinct_structured_plates(self):
        hashes = []
        for index, prompt in enumerate(self.PROMPTS):
            path = self.dir / f"p{index}.png"
            make_visuals.build_plate("deep-night", index, path, 20260827,
                                     width=256, height=144, prompt=prompt)
            verdict = qc.assess_image(path)
            self.assertEqual(verdict["verdict"], "OK",
                             f"{prompt}: {verdict['findings']}")
            hashes.append(qc.image_hash(path))
        for i in range(len(hashes)):
            for j in range(i + 1, len(hashes)):
                self.assertGreater(qc.hash_distance(hashes[i], hashes[j]),
                                   qc.DUPLICATE_HASH_DISTANCE)

    def test_the_same_prompt_makes_the_same_plate(self):
        first, second = self.dir / "a.png", self.dir / "b.png"
        for path in (first, second):
            make_visuals.build_plate("deep-night", 0, path, 20260827,
                                     width=256, height=144, prompt="one prompt")
        self.assertEqual(first.read_bytes(), second.read_bytes())


class GoalDerivationTest(unittest.TestCase):
    """A goal becomes a plan the pipeline can run - and cannot talk the
    studio into a capability it has not got."""

    GOAL = "Create a 2-hour psychedelic sleep experience with rain and ambient music"

    def test_the_length_is_read_from_the_words(self):
        self.assertEqual(goal_mod.requested_minutes(self.GOAL), 120.0)
        self.assertEqual(goal_mod.requested_minutes("a 30-minute study session"), 30.0)
        self.assertEqual(goal_mod.requested_minutes("no length here"), None)

    def test_the_longest_stated_length_wins(self):
        self.assertEqual(
            goal_mod.requested_minutes("a 2 hour video with 30 second scenes"), 120.0)

    def test_enumerations_are_clamped_to_what_this_build_implements(self):
        plan = goal_mod.sanitize_plan({
            "title_pattern": "T", "niche": "Adult Sleep",
            "shape": "something_invented", "narration": "sung",
            "research_topics": ["audio", "not_a_topic"],
        }, self.GOAL)
        self.assertEqual(plan["shape"], goal_mod.DEFAULT_SHAPE)
        self.assertEqual(plan["narration"], "silent")
        self.assertEqual(plan["niche"], "adult_sleep")
        self.assertEqual(plan["research_topics"], ["audio"])
        self.assertEqual(plan["minutes"], 120.0)

    def test_a_narrated_ambient_plan_gets_a_narrated_template(self):
        plan = goal_mod.sanitize_plan(
            {"title_pattern": "T", "shape": "ambient_motion", "narration": "narrated"},
            self.GOAL)
        concept = goal_mod.concept_from_plan(plan, "vid", self.GOAL)
        self.assertEqual(concept["spec_template"], goal_mod.SPEC_TEMPLATES["narrated_story"])
        self.assertEqual(concept["audio_source_requirement"], "tts_required")

    def test_depicted_imagery_is_not_claimed_from_a_wish(self):
        plan = goal_mod.sanitize_plan(
            {"title_pattern": "T", "needs_depicted_imagery": True}, self.GOAL)
        concept = goal_mod.concept_from_plan(plan, "vid", self.GOAL)
        self.assertFalse(concept["procedural_visuals_acceptable"])
        self.assertTrue(concept["derived_from_goal"])

    def test_a_derived_brief_always_exists_and_names_no_topics_as_instructions(self):
        plan = goal_mod.sanitize_plan({"title_pattern": "T"}, self.GOAL)
        brief = goal_mod.brief_from_plan(plan, self.GOAL)
        self.assertTrue(brief["niche"])
        self.assertEqual(brief["research_topics"], [])
        self.assertIn("Planned research topics", brief["notes"])


class DeleteProductionTest(unittest.TestCase):

    def setUp(self):
        self.video_id = "pytest-delete-me"
        self.pdir = project.project_dir(self.video_id)
        self.addCleanup(shutil.rmtree, self.pdir, True)
        self.pdir.mkdir(parents=True, exist_ok=True)

    def _write(self, metadata):
        (self.pdir / "metadata.json").write_text(json.dumps(metadata))

    def test_a_published_production_refuses_to_be_deleted(self):
        self._write({"video_id": self.video_id, "publish": {"published": True}})
        with self.assertRaises(project.ProjectError):
            project.delete_project(self.video_id, "someone")
        self.assertTrue(self.pdir.is_dir())

    def test_deletion_removes_the_directory_and_is_recorded(self):
        self._write({"video_id": self.video_id, "selected_title": "T"})
        result = project.delete_project(self.video_id, "someone", reason="residue")
        self.assertFalse(self.pdir.exists())
        self.assertIn(f"projects/{self.video_id}", result["removed"])
        ledger = project.DELETION_LEDGER.read_text().strip().splitlines()
        entry = json.loads(ledger[-1])
        self.assertEqual(entry["video_id"], self.video_id)
        self.assertEqual(entry["actor"], "someone")
        self.assertEqual(entry["reason"], "residue")

    def test_deletion_needs_a_named_actor(self):
        self._write({"video_id": self.video_id})
        with self.assertRaises(project.ProjectError):
            project.delete_project(self.video_id, "")


if __name__ == "__main__":
    unittest.main()
