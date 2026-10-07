#!/usr/bin/env python3
"""Tests for the structured visual direction and its prompt compiler.

The properties that matter: the identity is the same in every scene (one
video), the environment/framing/light differ (not one picture ten times),
the generic-quality vocabulary never reaches a generator, and the prompt is
written in the dialect the target provider actually responds to.
"""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import visual_direction as vd  # noqa: E402

RAW = {
    "reasoning": "one setting, watched passively for hours",
    "identity": {
        "palette": ["deep indigo", "aqua", "silver mist"],
        "lighting": "low-angle moonlight through condensation on glass",
        "atmosphere": "cool, humid, silent",
        "materials": ["cast iron framing", "beaded glass", "wet flagstone"],
        "continuity_anchors": ["the ornate iron ribbing", "pooled water"],
        "camera": {"lens": "35mm", "perspective": "low and close",
                   "depth_of_field": "shallow"},
        "render_intent": "available-light photography, faint grain",
    },
    "environments": [
        {"slug": "dome", "description": "a glasshouse dome of towering palms.",
         "focal_point": "a fern frond beaded with water", "scale": "medium interior"},
        {"slug": "walk", "description": "a narrow planted walkway under arched glass",
         "focal_point": "a wet flagstone path", "scale": "a corridor of the house"},
    ],
    "avoid": ["rain", "people"],
}


def scenes(count):
    return [{"scene_id": f"s{i:02d}"} for i in range(1, count + 1)]


class SlopStrippingTest(unittest.TestCase):

    def test_generic_quality_vocabulary_never_survives(self):
        clean, removed = vd._strip_slop(
            "a wet stone floor, 8k, hyper-realistic, unreal engine, masterpiece")
        self.assertEqual(clean, "a wet stone floor")
        self.assertIn("8k", removed)
        self.assertIn("unreal engine", removed)

    def test_stripping_does_not_eat_words_that_merely_contain_a_term(self):
        clean, removed = vd._strip_slop("a brass key on dark velvet")
        self.assertEqual(clean, "a brass key on dark velvet")
        self.assertEqual(removed, [])

    def test_what_was_stripped_is_recorded_not_silently_dropped(self):
        direction = vd.sanitize_direction({
            "identity": {"render_intent": "photography, 8k, award winning",
                         "palette": ["oat"]},
            "environments": [{"description": "a room"}]})
        self.assertIn("8k", direction["slop_removed"])
        self.assertIn("award winning", direction["slop_removed"])
        self.assertNotIn("8k", direction["identity"]["render_intent"])


class SanitisationTest(unittest.TestCase):

    def test_bounded_lists_stay_bounded(self):
        direction = vd.sanitize_direction({
            "identity": {"palette": [f"colour{i}" for i in range(20)],
                         "materials": [f"material{i}" for i in range(20)],
                         "continuity_anchors": [f"anchor{i}" for i in range(20)]},
            "environments": [{"description": f"place {i}"} for i in range(20)]})
        self.assertLessEqual(len(direction["identity"]["palette"]), vd.MAX_PALETTE_TERMS)
        self.assertLessEqual(len(direction["identity"]["materials"]), vd.MAX_MATERIAL_TERMS)
        self.assertLessEqual(len(direction["identity"]["continuity_anchors"]), vd.MAX_ANCHORS)
        self.assertLessEqual(len(direction["environments"]), vd.MAX_ENVIRONMENTS)

    def test_an_unknown_depth_of_field_falls_back_to_a_real_one(self):
        direction = vd.sanitize_direction({
            "identity": {"camera": {"depth_of_field": "swirly"}, "palette": ["oat"]},
            "environments": [{"description": "a room"}]})
        self.assertIn(direction["identity"]["camera"]["depth_of_field"],
                      vd.DEPTH_OF_FIELD)

    def test_environments_never_outnumber_the_scenes(self):
        direction = vd.sanitize_direction(
            {"environments": [{"description": f"p{i}"} for i in range(8)]},
            scene_count=3)
        self.assertLessEqual(len(direction["environments"]), 3)

    def test_a_document_with_nothing_in_it_is_not_usable(self):
        self.assertFalse(vd.is_usable(vd.sanitize_direction({})))
        self.assertFalse(vd.is_usable(None))

    def test_environments_without_identity_are_not_usable_either(self):
        direction = vd.sanitize_direction(
            {"environments": [{"description": "a room"}]})
        self.assertFalse(vd.is_usable(direction))

    def test_a_complete_document_is_usable(self):
        self.assertTrue(vd.is_usable(vd.sanitize_direction(RAW)))


class ShotPlanTest(unittest.TestCase):

    def setUp(self):
        self.direction = vd.sanitize_direction(RAW)

    def test_every_scene_gets_an_environment_a_framing_and_a_light_state(self):
        plan = vd.shot_plan(self.direction, scenes(10))
        self.assertEqual(len(plan), 10)
        for entry in plan:
            self.assertIn(entry["framing"], vd.FRAMINGS)
            self.assertIn(entry["light_state"], vd.LIGHT_STATES)

    def test_a_returning_environment_returns_differently(self):
        """Environments and framings cycle at different lengths on purpose:
        if they advanced together, scene 9 would be scene 1 again."""
        plan = vd.shot_plan(self.direction, scenes(12))
        same_env = [e for e in plan if e["environment"] == plan[0]["environment"]]
        self.assertGreater(len({(e["framing"], e["light_state"]) for e in same_env}), 1)

    def test_no_environments_means_no_plan_rather_than_a_guess(self):
        self.assertEqual(vd.shot_plan({"environments": []}, scenes(4)), [])


class CompilerTest(unittest.TestCase):

    def setUp(self):
        self.direction = vd.sanitize_direction(RAW)

    def test_the_environment_leads_the_prompt(self):
        prompt = vd.compile_scene_prompt(
            self.direction, self.direction["environments"][0])
        self.assertTrue(prompt.startswith("a glasshouse dome of towering palms"))

    def test_every_identity_facet_reaches_every_scene(self):
        prompts, _, _ = vd.compile_scene_prompts(self.direction, scenes(6))
        for prompt in prompts.values():
            self.assertIn("deep indigo", prompt)
            self.assertIn("cast iron framing", prompt)
            self.assertIn("ornate iron ribbing", prompt)
            self.assertIn("35mm", prompt)
            self.assertIn("available-light photography", prompt)

    def test_scenes_differ_without_the_identity_changing(self):
        prompts, _, _ = vd.compile_scene_prompts(self.direction, scenes(10))
        self.assertGreater(vd.distinct_prompt_count(prompts), 1)
        self.assertLess(vd.distinct_prompt_count(prompts), 10,
                        "some repetition is correct - a repeated picture is "
                        "one render reused, not a defect")

    def test_a_sentence_written_facet_does_not_become_an_empty_tag(self):
        prompt = vd.compile_scene_prompt(
            self.direction, self.direction["environments"][0], style="tag")
        self.assertNotIn(".,", prompt)
        self.assertNotIn(". ,", prompt)

    def test_the_natural_dialect_is_not_a_comma_salad(self):
        prompt = vd.compile_scene_prompt(
            self.direction, self.direction["environments"][0], style="natural")
        self.assertTrue(prompt.endswith("."))
        self.assertIn(";", prompt)
        self.assertTrue(prompt[0].isupper())

    def test_the_negative_prompt_carries_the_concept_and_the_failure_modes(self):
        negative = vd.compile_negative_prompt(self.direction, "tag")
        self.assertIn("rain", negative)
        self.assertIn("watermark", negative)
        self.assertIn("deformed geometry", negative)

    def test_a_scene_framing_supersedes_the_environments_fixed_scale(self):
        # The scale is the same in every scene; the framing is what makes
        # scene 3 a different picture from scene 1. Both in one prompt is
        # two shot sizes, and the constant one wins.
        prompt = vd.compile_scene_prompt(
            self.direction, self.direction["environments"][0], framing="detail")
        self.assertIn(vd.FRAMINGS["detail"], prompt)
        self.assertNotIn("medium interior", prompt)

    def test_the_environment_scale_still_speaks_when_nothing_else_does(self):
        prompt = vd.compile_scene_prompt(
            self.direction, self.direction["environments"][0], framing="")
        self.assertIn("medium interior", prompt)

    def test_one_environment_still_yields_different_shot_sizes(self):
        direction = dict(self.direction,
                         environments=self.direction["environments"][:1])
        prompts, _, _ = vd.compile_scene_prompts(direction, scenes(8))
        sizes = {framing for framing in vd.FRAMINGS.values()
                 if any(framing in p for p in prompts.values())}
        self.assertGreaterEqual(len(sizes), 4)

    def test_sound_and_pacing_language_never_reaches_an_image_model(self):
        negative = vd.compile_negative_prompt(
            self.direction, "tag",
            base="storm sounds, barking dogs, rapid cuts, sudden motion, "
                 "cheesy stock imagery")
        for term in ("storm sounds", "barking", "rapid cuts", "sudden motion"):
            self.assertNotIn(term, negative)
        self.assertIn("cheesy stock imagery", negative)

    def test_a_negative_the_prompt_itself_asks_for_is_dropped(self):
        prompts, negative, _ = vd.compile_scene_prompts(
            self.direction, scenes(4), base_negative="water, people, bright daylight")
        self.assertTrue(any("water" in p for p in prompts.values()))
        self.assertNotIn("water", [t.strip() for t in negative.split(",")])
        self.assertIn("people", negative)
        self.assertIn("bright daylight", negative)

    def test_the_generic_failure_modes_survive_the_filters(self):
        _, negative, _ = vd.compile_scene_prompts(
            self.direction, scenes(4), base_negative="thunder")
        self.assertNotIn("thunder", negative)
        for term in ("watermark", "deformed geometry", "visual noise"):
            self.assertIn(term, negative)

    def test_a_dialect_with_no_negative_channel_gets_no_negative_prompt(self):
        self.assertEqual(vd.compile_negative_prompt(self.direction, "natural"), "")

    def test_the_project_negative_prompt_is_kept_and_not_duplicated(self):
        negative = vd.compile_negative_prompt(
            self.direction, "tag", base="no dogs, watermark")
        self.assertIn("no dogs", negative)
        self.assertEqual(negative.lower().count("watermark"), 1)

    def test_compiled_prompts_carry_no_slop(self):
        direction = vd.sanitize_direction({
            "identity": {"palette": ["oat"], "render_intent": "8k hyper-realistic"},
            "environments": [{"description": "a room, masterpiece"}]})
        prompts, _, _ = vd.compile_scene_prompts(direction, scenes(3))
        for prompt in prompts.values():
            lowered = prompt.lower()
            for term in vd.SLOP_TERMS:
                self.assertNotIn(term, lowered)


if __name__ == "__main__":
    unittest.main()
