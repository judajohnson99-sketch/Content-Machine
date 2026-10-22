#!/usr/bin/env python3
"""The calibration dataset and harness, with mocked vision models.

No real model runs here. Four stand-ins show the harness telling them apart:
an oracle that answers from the labels (should calibrate everything), a
random scorer (should calibrate nothing), a hallucinator that names a defect
on every image (should lose the artifact dimension but keep the rest), and an
unreliable one that often answers in the wrong format (should lose
everything, however good its other answers are).
"""
import json
import os
import random
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import vision  # noqa: E402
import vision_calibration as cal  # noqa: E402

STYLES = {
    "A": ("digital illustration", ["blue", "white"], "dim light", "calm"),
    "B": ("digital illustration", ["orange", "yellow"], "warm light", "calm"),
    "C": ("digital illustration", ["green", "white"], "cool light", "calm"),
    "D": ("digital illustration", ["red", "yellow"], "warm light", "calm"),
    "E": ("digital illustration", ["blue", "orange"], "dim light", "calm"),
    "F": ("digital illustration", ["green", "white"], "cool light", "calm"),
    None: ("digital illustration", ["black", "blue"], "dim light", "calm"),
}
ODD_STYLE = ("photograph", ["magenta", "red"], "harsh daylight", "busy")


class OracleProvider(vision.VisionProvider):
    """Answers from the labels: what a perfect model would say."""

    name = "oracle"

    def __init__(self, cases, **overrides):
        self.by_sha = {c["sha256"]: c for c in cases}
        self.overrides = overrides
        self.calls = 0

    def configured(self):
        return True

    def installed(self, timeout=5.0):
        return ["oracle-model"]

    def judge(self, image_path, intent, model, timeout):
        self.calls += 1
        case = self.by_sha[vision.sha256_file(image_path)]
        labels = case["labels"]
        medium, palette, lighting, mood = (
            ODD_STYLE if labels.get("odd_one_out") else STYLES[labels.get("style_set")])
        reply = {
            "adherence": {"match": 5, "mismatch": 1}.get(labels.get("adherence"), 4),
            "adherence_reason": "scripted", "composition": {"good": 4, "poor": 1}.get(
                labels.get("composition"), 4),
            "composition_reason": "scripted", "artifacts": list(labels.get("artifacts") or []),
            "artifact_reason": "", "style_medium": medium, "style_palette": palette,
            "style_lighting": lighting, "style_mood": mood}
        reply.update(self.overrides)
        hook = self.overrides.get("_hook")
        reply.pop("_hook", None)
        if hook:
            reply = hook(case, reply)
        return reply


class CalibrationTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cases = cal.load_cases()

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.addCleanup(self.tmp.cleanup)

    def runner(self, provider):
        return vision.Runner(provider=provider, models=["oracle-model"], coordination="off",
                             cache=vision.Cache(self.dir / "cache"), state_path=self.dir / "state.json",
                             settle_seconds=0)

    def random_provider(self, seed):
        """A model that answers every question at random, descriptors included."""
        rng = random.Random(seed)
        colours = ["red", "green", "blue", "white", "orange", "yellow", "black", "purple"]
        return OracleProvider(self.cases, _hook=lambda case, reply: dict(
            reply, adherence=rng.randint(1, 5), composition=rng.randint(1, 5),
            artifacts=rng.sample(list(vision.ARTIFACT_VOCABULARY), rng.randint(0, 2)),
            style_palette=rng.sample(colours, 2), style_mood=rng.choice(["calm", "busy", "dark"]),
            style_medium=rng.choice(["digital illustration", "photograph", "3D render", "watercolour"]),
            style_lighting=rng.choice(["dim light", "warm light", "harsh daylight", "soft glow"])))

    def measure(self, provider):
        report = cal.run_model("oracle-model", self.cases, runner=self.runner(provider))
        self.assertNotIn("not_run", report)
        return report


class DatasetTest(CalibrationTestCase):
    def test_the_committed_dataset_is_intact(self):
        self.assertEqual(cal.verify(), [])

    def test_every_group_has_enough_examples_to_say_something(self):
        manifest = cal.load_manifest()
        counts = manifest["counts"]
        self.assertGreaterEqual(counts["adherence"], 20)
        self.assertGreaterEqual(counts["composition"], 10)
        self.assertGreaterEqual(counts["consistency"], 36)
        self.assertEqual(sum(counts.values()), len(manifest["cases"]))

    def test_labels_sit_only_on_the_group_that_measures_them(self):
        for case in self.cases:
            labels = case["labels"]
            if case["group"] != "adherence":
                self.assertIsNone(labels["adherence"], case["id"])
            if case["group"] != "composition":
                self.assertIsNone(labels["composition"], case["id"])
            if case["group"] != "consistency":
                self.assertIsNone(labels["style_set"], case["id"])

    def test_every_match_has_a_mismatch_with_the_same_prompt_and_a_different_image(self):
        by_id = {c["id"]: c for c in self.cases}
        for case in self.cases:
            if case["id"].endswith("-match"):
                other = by_id[case["id"].replace("-match", "-mismatch")]
                self.assertEqual(case["intent"], other["intent"])
                self.assertNotEqual(case["sha256"], other["sha256"])
                self.assertEqual(other["labels"]["adherence"], "mismatch")
                self.assertIn(other["labels"]["mismatch_kind"],
                              ("colour", "missing", "shape", "position"))

    def test_artifact_labels_name_only_what_the_dataset_can_honestly_show(self):
        named = {a for c in self.cases for a in c["labels"]["artifacts"]}
        self.assertEqual(named, {"text_or_watermark", "blur", "blocky_compression", "noise"})
        self.assertTrue(named.isdisjoint(cal.NOT_COVERED))
        self.assertIn("banding", cal.NOT_COVERED)

    def test_no_two_cases_are_the_same_picture(self):
        by_sha = {}
        for case in self.cases:
            by_sha.setdefault(case["sha256"], []).append(case["id"])
        duplicates = [ids for ids in by_sha.values() if len(ids) > 1]
        self.assertEqual(duplicates, [], "identical images would be counted as independent evidence")

    def test_each_consistency_set_has_exactly_one_odd_image(self):
        sets = {}
        for c in self.cases:
            if c["labels"]["style_set"]:
                sets.setdefault(c["labels"]["style_set"], []).append(c["labels"]["odd_one_out"])
        self.assertEqual(set(sets), set("ABCDEF"))
        for tag, flags in sets.items():
            self.assertEqual((len(flags), sum(flags)), (6, 1), tag)

    def test_the_source_plates_are_tracked_production_assets_and_unchanged(self):
        manifest = cal.load_manifest()
        for key, entry in manifest["source_plates"].items():
            path = ROOT / entry["path"]
            self.assertTrue(path.is_file(), entry["path"])
            self.assertEqual(cal._sha256(path), entry["sha256"], f"{key} plate changed")
        self.assertFalse(manifest["human_reviewed"])
        self.assertEqual(manifest["label_source"], "constructed")

    def test_the_build_is_reproducible_in_content_if_not_in_bytes(self):
        out = self.dir / "rebuilt"
        manifest = cal.build(out)
        committed = cal.load_manifest()
        strip = lambda cs: [(c["id"], c["intent"], c["labels"]) for c in cs]  # noqa: E731
        self.assertEqual(strip(manifest["cases"]), strip(committed["cases"]))
        self.assertEqual(cal.verify(out), [])


class VerifyTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.copy = Path(self.tmp.name) / "vision"
        shutil.copytree(cal.DATASET_DIR, self.copy, ignore=shutil.ignore_patterns("sheets", "results"))

    def test_a_changed_image_is_caught(self):
        with (self.copy / "images" / "adh-01-match.jpg").open("ab") as stream:
            stream.write(b"x")
        self.assertTrue(any("changed since it was labelled" in p for p in cal.verify(self.copy)))

    def test_a_missing_image_is_caught(self):
        (self.copy / "images" / "cmp-good-01.jpg").unlink()
        self.assertTrue(any("missing" in p for p in cal.verify(self.copy)))

    def test_a_label_outside_the_vocabulary_is_caught(self):
        path = self.copy / "manifest.json"
        manifest = json.loads(path.read_text())
        manifest["cases"][0]["labels"]["artifacts"] = ["sparkles"]
        manifest["cases"][1]["labels"]["adherence"] = "maybe"
        path.write_text(json.dumps(manifest))
        problems = cal.verify(self.copy)
        self.assertTrue(any("outside the vocabulary" in p for p in problems))
        self.assertTrue(any("bad adherence label" in p for p in problems))

    def test_a_run_refuses_a_dataset_that_no_longer_matches_its_manifest(self):
        with (self.copy / "images" / "adh-01-match.jpg").open("ab") as stream:
            stream.write(b"x")
        with mock.patch.object(cal, "DATASET_DIR", self.copy):
            self.assertEqual(cal.main(["run", "--model", "m"]), 1)


class MetricsTest(unittest.TestCase):
    def test_auc(self):
        self.assertEqual(cal.auc([5, 5, 4], [1, 2, 1]), 1.0)
        self.assertEqual(cal.auc([3, 3], [3, 3]), 0.5)
        self.assertEqual(cal.auc([1, 1], [5, 5]), 0.0)
        self.assertIsNone(cal.auc([], [1]))

    def test_best_threshold_respects_the_false_alarm_limit(self):
        good, bad = [5, 5, 4, 4, 3, 2], [1, 1, 2, 2, 3, 4]
        pick = cal.best_threshold(good, bad, 0.2)
        self.assertEqual(pick["threshold"], 2)
        self.assertLessEqual(pick["false_alarm"], 0.2)
        self.assertIsNone(cal.best_threshold([1, 1, 1], [1, 1, 1], 0.1))


class HarnessTest(CalibrationTestCase):
    def test_a_perfect_model_calibrates_every_dimension(self):
        report = self.measure(OracleProvider(self.cases))
        self.assertEqual(report["calibrated_dimensions"], list(vision.DIMENSIONS))
        self.assertEqual(report["reliability"]["parse_rate"], 1.0)
        self.assertEqual(report["dimensions"]["adherence"]["auc"], 1.0)
        thresholds = report["proposed_thresholds"]
        self.assertGreaterEqual(thresholds["adherence"]["warn_at_or_below"], 1)
        self.assertLess(thresholds["adherence"]["warn_at_or_below"], 5)
        self.assertEqual(thresholds["artifacts"]["calibrated_names"], ["text_or_watermark"])
        self.assertIn("deformed_anatomy", thresholds["artifacts"]["warn"],
                      "an artifact the dataset cannot test lost its warning")
        self.assertEqual(report["dimensions"]["consistency"]["suggested"]["recall"], 1.0)
        self.assertEqual(report["dimensions"]["consistency"]["suggested"]["false_alarm"], 0.0)
        self.assertEqual(report["dimensions"]["artifacts"]["clean_false_positive_rate"], 0.0)

    def test_a_random_model_calibrates_nothing_whatever_the_seed(self):
        for seed in range(5):
            with self.subTest(seed=seed):
                report = self.measure(self.random_provider(seed))
                self.assertEqual(report["calibrated_dimensions"], [],
                                 f"a random model calibrated {report['calibrated_dimensions']}")

    def test_a_hallucinating_model_loses_artifacts_but_keeps_what_it_gets_right(self):
        provider = OracleProvider(self.cases, _hook=lambda case, reply: dict(
            reply, artifacts=["text_or_watermark"]))
        report = self.measure(provider)
        self.assertNotIn("artifacts", report["calibrated_dimensions"])
        self.assertIn("adherence", report["calibrated_dimensions"])
        artifacts = report["dimensions"]["artifacts"]
        self.assertEqual(artifacts["clean_false_positive_rate"], 1.0)
        self.assertIn("names an artifact on 100% of clean images", artifacts["reason"])

    def test_an_unreliable_model_calibrates_nothing_whatever_else_it_gets_right(self):
        class Flaky(OracleProvider):
            def judge(self, image_path, intent, model, timeout):
                if int(vision.sha256_file(image_path)[:2], 16) % 2:
                    raise vision.VisionError(vision.UNUSABLE, "the model's reply was not JSON")
                return super().judge(image_path, intent, model, timeout)
        report = self.measure(Flaky(self.cases))
        self.assertLess(report["reliability"]["parse_rate"], cal.CRITERIA["parse_rate"])
        self.assertFalse(report["reliability"]["meets_criteria"])
        self.assertEqual(report["calibrated_dimensions"], [])
        self.assertTrue(report["reliability"]["not_judged"])

    def test_a_model_that_never_notices_the_odd_image_does_not_calibrate_consistency(self):
        provider = OracleProvider(self.cases, _hook=lambda case, reply: dict(
            reply, style_medium="digital illustration", style_palette=["black", "blue"],
            style_lighting="dim light", style_mood="calm"))
        report = self.measure(provider)
        self.assertNotIn("consistency", report["calibrated_dimensions"])
        self.assertIn("adherence", report["calibrated_dimensions"])

    def test_an_unreachable_model_is_not_run_rather_than_reported_as_bad(self):
        runner = vision.Runner(provider=vision.OllamaProvider(url="http://127.0.0.1:9"),
                               models=["m"], coordination="off", cache=vision.Cache(self.dir / "c"),
                               state_path=self.dir / "s.json")
        report = cal.run_model("m", self.cases[:3], runner=runner)
        self.assertIn("not_run", report)
        self.assertEqual(report["code"], vision.UNREACHABLE)

    def test_the_report_states_its_own_limits(self):
        report = self.measure(OracleProvider(self.cases))
        self.assertTrue(any("easier, different task" in l or "simple shapes" in l for l in report["limits"]))
        self.assertEqual(report["by_source"]["constructed"]["cases"], len(self.cases))


class ApplyTest(CalibrationTestCase):
    def thresholds_file(self):
        path = self.dir / "thresholds.json"
        shutil.copy(vision.THRESHOLDS_PATH, path)
        return path

    def test_apply_writes_that_models_entry_and_findings_then_use_it(self):
        path = self.thresholds_file()
        report = self.measure(OracleProvider(self.cases))
        entry = cal.apply_to_thresholds(report, path, result_file="calibration/vision/results/x.json")
        self.assertEqual(entry["calibrated_dimensions"], list(vision.DIMENSIONS))
        self.assertEqual(entry["uncalibrated_dimensions"], [])
        self.assertFalse(entry["dataset"]["human_reviewed"])
        self.assertEqual(entry["result_file"], "calibration/vision/results/x.json")

        lookup = vision.thresholds_for("oracle-model", path)
        self.assertEqual(lookup["calibrated_dimensions"], list(vision.DIMENSIONS))
        self.assertEqual(vision.thresholds_for("another-model", path)["calibrated_dimensions"], [],
                         "calibration leaked to a model that was never measured")

        judgement = vision.coerce_judgement(
            {"adherence": 1, "adherence_reason": "x", "composition": 4, "composition_reason": "",
             "artifacts": ["text_or_watermark", "deformed_anatomy"], "artifact_reason": "",
             "style_medium": "", "style_palette": [], "style_lighting": "", "style_mood": ""},
            model="oracle-model", provider="oracle", intent="x", image_sha="a" * 64)
        findings = {f["code"]: f for f in vision.per_image_findings(judgement, "s01", lookup)}
        self.assertNotIn("not yet calibrated", findings["does_not_depict_intent"]["detail"])
        self.assertNotIn("not yet calibrated", findings["visible_artifacts"]["detail"].split("text or watermark")[0])

    def test_an_artifact_the_dataset_could_not_test_stays_marked_as_uncalibrated(self):
        path = self.thresholds_file()
        cal.apply_to_thresholds(self.measure(OracleProvider(self.cases)), path)
        lookup = vision.thresholds_for("oracle-model", path)

        def finding_for(names):
            j = vision.coerce_judgement(
                {"adherence": 5, "adherence_reason": "", "composition": 5, "composition_reason": "",
                 "artifacts": names, "artifact_reason": "", "style_medium": "", "style_palette": [],
                 "style_lighting": "", "style_mood": ""},
                model="oracle-model", provider="oracle", intent="x", image_sha="a" * 64)
            return [f for f in vision.per_image_findings(j, "s01", lookup)
                    if f["code"] == "visible_artifacts"][0]
        self.assertNotIn("not yet calibrated", finding_for(["text_or_watermark"])["detail"])
        untested = finding_for(["deformed_anatomy"])
        self.assertIn("not yet calibrated", untested["detail"])
        self.assertEqual(untested["severity"], "warn")

    def test_a_model_that_calibrates_nothing_writes_nothing(self):
        path = self.thresholds_file()
        before = path.read_text()
        report = self.measure(self.random_provider(2))
        self.assertEqual(report["calibrated_dimensions"], [])
        self.assertIsNone(cal.apply_to_thresholds(report, path))
        self.assertEqual(path.read_text(), before)

    def test_applying_one_model_leaves_another_alone(self):
        path = self.thresholds_file()
        first = self.measure(OracleProvider(self.cases))
        cal.apply_to_thresholds(first, path)
        second = dict(first, model="other-model", calibrated_dimensions=["adherence"],
                      proposed_thresholds={"calibrated_dimensions": ["adherence"],
                                           "adherence": {"warn_at_or_below": 3, "note_at_or_below": 4}})
        cal.apply_to_thresholds(second, path)
        config = json.loads(path.read_text())
        self.assertEqual(set(config["models"]), {"oracle-model", "other-model"})
        self.assertEqual(config["models"]["other-model"]["uncalibrated_dimensions"],
                         ["composition", "artifacts", "consistency"])

    def test_the_shipped_thresholds_still_calibrate_no_model(self):
        self.assertEqual(vision.load_thresholds()["models"], {})


class RealCasesTest(CalibrationTestCase):
    def setUp(self):
        super().setUp()
        self.data = self.dir / "vision"
        shutil.copytree(cal.DATASET_DIR, self.data, ignore=shutil.ignore_patterns("sheets", "results"))
        self.images = []
        for i in range(2):
            path = self.dir / f"real{i}.png"
            subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i",
                            f"color=c=0x{i + 3}0{i + 3}060:s=320x180", "-frames:v", "1", str(path)],
                           check=True)
            self.images.append(path)

    def labels(self, **overrides):
        entries = [dict({"image": str(p), "intent": f"a real scene {i}", "adherence": "match",
                         "composition": "good", "artifacts": [], "notes": ""}, **overrides)
                   for i, p in enumerate(self.images)]
        path = self.dir / "labels.json"
        path.write_text(json.dumps({"images": entries}))
        return path

    def test_labelled_real_renders_join_the_dataset_as_a_separate_split(self):
        added, problems = cal.import_real(self.labels(), "owner", self.data)
        self.assertEqual((added, problems), (2, []))
        cases = cal.load_cases(self.data)
        real = [c for c in cases if c["source"] == "real"]
        self.assertEqual(len(real), 2)
        self.assertEqual(real[0]["labelled_by"], "owner")
        self.assertEqual(cal.verify(self.data), [])
        again, _ = cal.import_real(self.labels(), "owner", self.data)
        self.assertEqual(again, 0, "the same image was imported twice")

    def test_incomplete_or_invalid_labels_are_refused_not_guessed(self):
        added, problems = cal.import_real(self.labels(adherence=None), "owner", self.data)
        self.assertEqual(added, 0)
        self.assertTrue(all("adherence must be" in p for p in problems))
        added, problems = cal.import_real(self.labels(artifacts=["sparkles"]), "owner", self.data)
        self.assertTrue(all("vocabulary" in p for p in problems))
        entry = json.loads(self.labels().read_text())
        entry["images"][0]["image"] = str(self.dir / "nope.png")
        (self.dir / "labels2.json").write_text(json.dumps(entry))
        added, problems = cal.import_real(self.dir / "labels2.json", "owner", self.data)
        self.assertEqual(added, 1)
        self.assertTrue(any("not found" in p for p in problems))

    def test_real_and_constructed_results_are_reported_separately(self):
        cal.import_real(self.labels(), "owner", self.data)
        cases = cal.load_cases(self.data)
        report = cal.run_model("oracle-model", cases, runner=self.runner(OracleProvider(cases)))
        self.assertEqual(set(report["by_source"]), {"constructed", "real"})
        self.assertEqual(report["by_source"]["real"]["cases"], 2)


class SheetTest(unittest.TestCase):
    def test_a_contact_sheet_is_written_per_group(self):
        with tempfile.TemporaryDirectory() as tmp:
            written = cal.sheets(out=Path(tmp))
            self.assertEqual({p.stem for p in written},
                             {"adherence", "artifacts", "composition", "consistency"})
            self.assertTrue(all(p.stat().st_size > 10_000 for p in written))


if __name__ == "__main__":
    unittest.main()
