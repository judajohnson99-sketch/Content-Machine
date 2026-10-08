#!/usr/bin/env python3
"""Tests for the creative brief: the deterministic audio/style mappers and
the LLM brief under TEST_MODE (no network is ever contacted here).
"""
import json
import os
import sys
import unittest
import unittest.mock
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import creative  # noqa: E402


def concept(**overrides):
    base = {
        "id": "test-concept",
        "audio_source_requirement": "synthesisable_now",
        "audio_concept": "",
        "visual_concept": "",
        "content_format": "Test format",
        "niche": "test_niche",
        "target_audience": "testers",
        "working_title_pattern": "Test Title",
        "monetization_hypothesis": "none",
    }
    base.update(overrides)
    return base


class AudioCompositionTest(unittest.TestCase):

    def test_synthesisable_now_defaults_to_brown_noise(self):
        comp = creative.build_audio_composition(concept(), 30.0)
        self.assertEqual(comp["layers"][0]["provider"], "noise")
        self.assertEqual(comp["layers"][0]["params"]["color"], "brown")
        self.assertEqual(comp["target_seconds"], 30.0)

    def test_pink_noise_keyword_is_matched(self):
        comp = creative.build_audio_composition(
            concept(audio_concept="Generated pink noise for calm focus."), 30.0)
        self.assertEqual(comp["layers"][0]["params"]["color"], "pink")

    def test_rain_keyword_selects_rain_provider(self):
        comp = creative.build_audio_composition(
            concept(audio_concept="Steady rain bed for background focus."), 30.0)
        self.assertEqual(comp["layers"][0]["provider"], "rain")

    def test_drone_keyword_selects_tone_provider(self):
        comp = creative.build_audio_composition(
            concept(audio_concept="A low synthesised drone."), 30.0)
        self.assertEqual(comp["layers"][0]["provider"], "tone")

    def test_tts_required_uses_narration_text(self):
        comp = creative.build_audio_composition(
            concept(audio_source_requirement="tts_required"), 30.0,
            narration_text="Once upon a time.")
        self.assertEqual(comp["layers"][0]["provider"], "tts")
        self.assertEqual(comp["layers"][0]["params"]["text"], "Once upon a time.")

    def test_tts_required_without_narration_text_is_not_fabricated(self):
        comp = creative.build_audio_composition(
            concept(audio_source_requirement="tts_required"), 30.0, narration_text="")
        self.assertIsNone(comp)

    def test_partial_requirement_uses_rain_bed(self):
        comp = creative.build_audio_composition(
            concept(audio_source_requirement="licensed_or_recorded"), 30.0)
        self.assertEqual(comp["layers"][0]["provider"], "rain")

    def test_blocked_requirement_returns_none_not_a_fabrication(self):
        comp = creative.build_audio_composition(
            concept(audio_source_requirement="music_generation_or_licensed"), 30.0)
        self.assertIsNone(comp)

    def test_brown_wins_over_pink_when_both_are_mentioned(self):
        """Regression: experiments/concepts.json describes sleep-brown-noise-dark
        as 'brown/pink noise' - brown must win since the concept is
        specifically about brown noise, not whichever keyword sorts first."""
        comp = creative.build_audio_composition(
            concept(audio_concept="Generated brown/pink noise. Fully synthesisable "
                                  "with FFmpeg's anoisesrc today."), 30.0)
        self.assertEqual(comp["layers"][0]["params"]["color"], "brown")

    def test_composition_is_accepted_by_the_audio_composer(self):
        """The mapper's output must be valid input to audio.compose(), not
        just structurally plausible."""
        sys.path.insert(0, str(ROOT / "scripts"))
        import audio as audio_mod
        comp = creative.build_audio_composition(concept(), 2.0)
        target, layers = audio_mod.validate_plan(comp)
        self.assertEqual(target, 2.0)
        self.assertEqual(len(layers), 1)

    def test_no_mood_or_findings_stays_a_single_layer(self):
        """Regression: a project with neither must render exactly as it did
        before mood/findings-driven layering existed."""
        comp = creative.build_audio_composition(concept(), 30.0, mood=None, findings=None)
        self.assertEqual(len(comp["layers"]), 1)

    def test_a_mood_adds_a_second_pad_layer(self):
        """A concept naming an explicit noise texture keeps that texture as
        the bed; a mood only adds a second, quieter pad layer on top."""
        comp = creative.build_audio_composition(
            concept(audio_concept="brown noise for sleep"), 30.0,
            mood="cozy, warm evening rain")
        self.assertEqual(len(comp["layers"]), 2)
        self.assertEqual(comp["layers"][0]["provider"], "noise")
        pad = comp["layers"][1]
        self.assertEqual(pad["provider"], "pad")
        self.assertEqual(pad["params"]["chord"], "warm")

    def test_an_unrecognised_mood_word_still_adds_a_pad_with_the_default_chord(self):
        comp = creative.build_audio_composition(
            concept(audio_concept="brown noise for sleep"), 30.0, mood="something unusual")
        self.assertEqual(comp["layers"][1]["params"]["chord"], "calm")

    def test_findings_alone_can_choose_a_mood_when_none_was_given(self):
        findings = {"findings": [
            {"finding_id": "f1", "kind": "observation", "topic": "audio",
             "statement": "the ambience leans eerie and unsettling throughout",
             "source_url": "https://x.test", "source_title": "x", "confidence": "VERIFIED"},
        ]}
        comp = creative.build_audio_composition(
            concept(audio_concept="brown noise for sleep"), 30.0, mood=None, findings=findings)
        self.assertEqual(len(comp["layers"]), 2)
        self.assertEqual(comp["layers"][1]["params"]["chord"], "eerie")

    def test_unmatched_keyword_with_a_mood_leads_with_a_music_bed(self):
        """The exact case that used to default to raw noise: a concept
        whose audio_concept names no noise/tone/rain keyword ('calm bed')
        but whose brief calls it music. It is routed as music, so the bed
        is the generative-music provider, not a texture with a pad on top.
        """
        comp = creative.build_audio_composition(
            concept(audio_concept="Slow-tempo calm bed. Synthesisable or licensed."), 30.0,
            mood="soft ambient music, gentle")
        self.assertEqual(len(comp["layers"]), 1)
        self.assertEqual(comp["layers"][0]["provider"], "music")
        self.assertEqual(comp["layers"][0]["gain_db"], 0.0)
        self.assertEqual(comp["layers"][0]["params"]["mood"], "calm")
        self.assertNotIn("chord_seconds", comp["layers"][0]["params"])

    def test_long_form_music_bed_holds_each_chord_longer(self):
        comp = creative.build_audio_composition(
            concept(audio_concept="calm bed"), 4 * 3600.0, mood="calm, gentle")
        self.assertEqual(comp["layers"][0]["provider"], "music")
        self.assertEqual(comp["layers"][0]["params"]["chord_seconds"], 60.0)

    def test_long_form_texture_pad_layer_drifts_rather_than_freezing(self):
        """The secondary pad under a named texture is the one place a
        fixed root would sit unchanged for hours; audio.py's drift exists
        for it, so the plan has to ask for it."""
        comp = creative.build_audio_composition(
            concept(audio_concept="brown noise for sleep"), 4 * 3600.0, mood="cozy warm")
        self.assertEqual(comp["layers"][0]["provider"], "noise")
        self.assertEqual(comp["layers"][1]["params"]["root_drift_ratios"],
                         list(creative.PAD_DRIFT_RATIOS))

    def test_short_texture_pad_layer_holds_one_root(self):
        comp = creative.build_audio_composition(
            concept(audio_concept="brown noise for sleep"), 30.0, mood="cozy warm")
        self.assertNotIn("root_drift_ratios", comp["layers"][1]["params"])

    def test_unmatched_keyword_with_no_mood_or_findings_still_falls_back_to_noise(self):
        """With nothing at all to go on the fallback is unchanged: raw
        brown noise, exactly as before pad-led beds existed."""
        comp = creative.build_audio_composition(
            concept(audio_concept="calm bed"), 30.0, mood=None, findings=None)
        self.assertEqual(len(comp["layers"]), 1)
        self.assertEqual(comp["layers"][0]["provider"], "noise")

    def test_tts_required_is_never_given_a_pad_layer(self):
        """Mood only enriches the synthesisable_now bed - narration is
        unaffected, so a narrated video's audio contract does not change."""
        comp = creative.build_audio_composition(
            concept(audio_source_requirement="tts_required"), 30.0,
            narration_text="Once upon a time.", mood="cozy warm")
        self.assertEqual(len(comp["layers"]), 1)
        self.assertEqual(comp["layers"][0]["provider"], "tts")

    def test_blocked_requirement_ignores_mood_too(self):
        comp = creative.build_audio_composition(
            concept(audio_source_requirement="music_generation_or_licensed"), 30.0,
            mood="cozy warm")
        self.assertIsNone(comp)


class AudioRoutingTest(unittest.TestCase):
    """route_audio's decision record: what kind of audio the concept needs,
    every source that could have supplied it, and whether the chosen one is
    even capable of being production-grade."""

    def _direction(self, *args, **kwargs):
        return creative.route_audio(*args, **kwargs)["direction"]

    def test_a_named_texture_is_the_product_not_a_stand_in(self):
        direction = self._direction(concept(audio_concept="brown noise for sleep"), 30.0)
        self.assertEqual(direction["kind"], "texture")
        self.assertIn("the product itself", direction["kind_reasoning"])
        self.assertTrue(direction["chosen"]["production_grade_capable"])

    def test_a_concept_that_calls_its_audio_music_is_routed_as_music(self):
        direction = self._direction(
            concept(audio_concept="gentle piano and soft ambient music"), 30.0)
        self.assertEqual(direction["kind"], "music")
        self.assertIn("piano", direction["kind_reasoning"])

    def test_a_mood_alone_can_make_it_music_when_no_texture_is_named(self):
        direction = self._direction(concept(audio_concept="calm bed"), 30.0,
                                    mood="soft ambient music")
        self.assertEqual(direction["kind"], "music")

    def test_narration_is_its_own_kind(self):
        direction = self._direction(
            concept(audio_source_requirement="tts_required"), 30.0,
            narration_text="Once upon a time.")
        self.assertEqual(direction["kind"], "narration")
        self.assertEqual(direction["chosen"]["source"], "tts")

    def test_a_narrated_concept_with_no_script_yet_plans_nothing(self):
        routed = creative.route_audio(
            concept(audio_source_requirement="tts_required"), 30.0)
        self.assertIsNone(routed["composition"])
        self.assertFalse(routed["direction"]["chosen"]["available"])

    def test_synthesised_music_records_every_alternative_and_why(self):
        direction = self._direction(concept(audio_concept="soft ambient music"), 30.0)
        sources = {s["source"]: s for s in direction["considered"]}
        self.assertEqual(set(sources), {"licensed-library", "music-generation-api",
                                        "generative-music"})
        self.assertFalse(sources["licensed-library"]["available"])
        self.assertFalse(sources["music-generation-api"]["available"])
        self.assertEqual(direction["chosen"]["source"], "generative-music")
        for source in direction["considered"]:
            self.assertTrue(source["rights"])
            self.assertTrue(source["cost"])

    def test_synthesised_music_never_claims_it_could_be_production_grade(self):
        """The honest ceiling of the local path - project.gate_blockers
        turns this False into a human-listen requirement."""
        direction = self._direction(concept(audio_concept="soft ambient music"), 30.0)
        self.assertFalse(direction["chosen"]["production_grade_capable"])

    def test_a_licensed_library_track_is_preferred_over_the_synthesiser(self):
        track = {"id": "warm-piano", "path": "/tmp/warm-piano.wav",
                 "source": "test fixture", "license": "CC0", "commercial_use": True,
                 "creator": "a real musician", "crossfade_loop_seconds": 4.0}
        original = creative.audio_mod.select_library_track
        creative.audio_mod.select_library_track = lambda **kw: track
        try:
            routed = creative.route_audio(
                concept(audio_concept="soft ambient music"), 30.0, mood="warm")
        finally:
            creative.audio_mod.select_library_track = original
        self.assertEqual(routed["direction"]["chosen"]["source"], "licensed-library")
        self.assertTrue(routed["direction"]["chosen"]["production_grade_capable"])
        layer = routed["composition"]["layers"][0]
        self.assertEqual(layer["provider"], "file")
        self.assertEqual(layer["params"]["license"]["license"], "CC0")
        self.assertTrue(layer["params"]["license"]["commercial_use"])

    def test_a_blocked_requirement_plans_nothing_and_says_what_would_unblock_it(self):
        routed = creative.route_audio(
            concept(audio_source_requirement="music_generation_or_licensed"), 30.0)
        self.assertIsNone(routed["composition"])
        direction = routed["direction"]
        self.assertIsNone(direction["chosen"])
        self.assertIn("library.json", direction["blocked_reason"])
        synthesised = next(s for s in direction["considered"]
                           if s["source"] == "generative-music")
        self.assertFalse(synthesised["available"])

    def test_the_partial_requirement_still_gets_its_documented_texture(self):
        routed = creative.route_audio(
            concept(audio_source_requirement="licensed_or_recorded"), 30.0)
        self.assertEqual(routed["direction"]["kind"], "texture")
        self.assertEqual(routed["direction"]["chosen"]["source"], "procedural-texture")
        self.assertTrue(routed["composition"]["layers"])


class ResearchSectionTest(unittest.TestCase):

    def test_empty_when_neither_brief_nor_findings_exist(self):
        self.assertEqual(creative.research_section(None, None), "")

    def test_includes_intent_and_likes_and_dislikes(self):
        section = creative.research_section(
            {"creative_intent": "cozy rainy night", "likes": ["gentle pacing"],
             "dislikes": ["jump scares"]}, None)
        self.assertIn("cozy rainy night", section)
        self.assertIn("gentle pacing", section)
        self.assertIn("jump scares", section)

    def test_findings_lines_carry_their_topic_and_kind(self):
        findings = {"findings": [
            {"finding_id": "f1", "kind": "observation", "topic": "pacing",
             "statement": "cuts happen every few seconds", "source_url": "https://x.test",
             "source_title": "x", "confidence": "VERIFIED"},
        ]}
        section = creative.research_section({"niche": "n"}, findings)
        self.assertIn("[pacing/observation]", section)
        self.assertIn("cuts happen every few seconds", section)

    def test_no_findings_yet_says_so_rather_than_omitting_the_section(self):
        section = creative.research_section({"niche": "n", "creative_intent": "x"}, None)
        self.assertIn("No competitor research findings yet", section)


class ProceduralStyleTest(unittest.TestCase):

    def test_default_style_is_deep_night(self):
        self.assertEqual(creative.pick_procedural_style(concept()), "deep-night")

    def test_storm_keyword_maps_to_storm_slate(self):
        self.assertEqual(
            creative.pick_procedural_style(concept(visual_concept="A storm-grey sky.")),
            "storm-slate")

    def test_forest_keyword_maps_to_muted_forest(self):
        self.assertEqual(
            creative.pick_procedural_style(concept(visual_concept="Deep forest green.")),
            "muted-forest")

    def test_returned_style_is_always_a_real_style(self):
        import make_visuals
        for visual_concept in ("", "storm", "warm ember", "forest", "dusty archive", "nonsense xyz"):
            style = creative.pick_procedural_style(concept(visual_concept=visual_concept))
            self.assertIn(style, make_visuals.STYLES)


class GenerateBriefTestModeTest(unittest.TestCase):
    """TEST_MODE=1 must never touch the network - mirrors generate.py."""

    def setUp(self):
        self._prev = os.environ.get("TEST_MODE")
        os.environ["TEST_MODE"] = "1"

    def tearDown(self):
        if self._prev is None:
            os.environ.pop("TEST_MODE", None)
        else:
            os.environ["TEST_MODE"] = self._prev

    def test_mock_brief_has_every_required_key(self):
        brief = creative.generate_brief(concept(), 30.0)
        for key in ("title", "description", "narration_script", "image_prompt",
                    "negative_prompt", "audio_mood"):
            self.assertIn(key, brief)

    def test_mock_brief_is_json_serialisable(self):
        brief = creative.generate_brief(concept(), 30.0)
        json.dumps(brief)  # must not raise

    def test_mock_narration_is_empty_when_not_tts_required(self):
        brief = creative.generate_brief(concept(audio_source_requirement="synthesisable_now"), 30.0)
        self.assertEqual(brief["narration_script"], "")

    def test_mock_narration_is_present_when_tts_required(self):
        brief = creative.generate_brief(concept(audio_source_requirement="tts_required"), 30.0)
        self.assertNotEqual(brief["narration_script"], "")

    def test_requires_subject_research_refuses_without_facts(self):
        """A concept flagged requires_subject_research must not get a brief -
        mock or otherwise - without sourced facts, even under TEST_MODE."""
        with self.assertRaises(creative.CreativeError):
            creative.generate_brief(concept(requires_subject_research=True), 30.0)

    def test_requires_subject_research_succeeds_once_facts_are_supplied(self):
        facts = {"provider": "fixture", "facts": [
            {"statement": "The canal opened in 1869.", "source_url": "https://mock.test/1"},
        ]}
        brief = creative.generate_brief(
            concept(requires_subject_research=True), 30.0, subject_research=facts)
        self.assertIn("title", brief)


class SceneMotifsTestModeTest(unittest.TestCase):
    """TEST_MODE=1 must never touch the network - same convention as the
    creative brief above."""

    def setUp(self):
        self._prev = os.environ.get("TEST_MODE")
        os.environ["TEST_MODE"] = "1"

    def tearDown(self):
        if self._prev is None:
            os.environ.pop("TEST_MODE", None)
        else:
            os.environ["TEST_MODE"] = self._prev

    def _facts(self):
        return {"provider": "fixture", "facts": [
            {"statement": "The canal opened in 1869.", "source_url": "https://mock.test/1"},
            {"statement": "It has no locks.", "source_url": "https://mock.test/2"},
        ]}

    def test_refuses_without_facts(self):
        with self.assertRaises(creative.CreativeError):
            creative.generate_scene_motifs(concept(), {}, [{"scene_id": "s01", "section": "hook"}])

    def test_one_motif_per_scene_no_more_no_fewer(self):
        scenes = [
            {"scene_id": "s01", "section": "hook", "narration": "A canal in Egypt."},
            {"scene_id": "s02", "section": "body", "narration": "It opened in 1869."},
        ]
        motifs = creative.generate_scene_motifs(concept(), self._facts(), scenes)
        self.assertEqual(set(motifs), {"s01", "s02"})


class SceneEnvironmentsTestModeTest(unittest.TestCase):
    """TEST_MODE=1 must never touch the network - same convention as
    generate_scene_motifs above, for concepts with no facts to depict."""

    def setUp(self):
        self._prev = os.environ.get("TEST_MODE")
        os.environ["TEST_MODE"] = "1"

    def tearDown(self):
        if self._prev is None:
            os.environ.pop("TEST_MODE", None)
        else:
            os.environ["TEST_MODE"] = self._prev

    def _scenes(self, n):
        return [{"scene_id": f"s{i:02d}", "section": "body"} for i in range(n)]

    def test_every_scene_gets_a_motif(self):
        scenes = self._scenes(9)
        motifs = creative.generate_scene_environments(concept(), scenes)
        self.assertEqual(set(motifs), {s["scene_id"] for s in scenes})

    def test_environment_count_is_smaller_than_scene_count(self):
        """A handful of settings, not one per scene - the count itself is the
        live model's judgment call (see SCENE_ENVIRONMENT_PROMPT_TEMPLATE),
        which TEST_MODE cannot exercise; this only checks the mechanical
        cycling wiring stays sane for a long scene list.
        """
        scenes = self._scenes(24)
        motifs = creative.generate_scene_environments(concept(), scenes)
        self.assertLess(len(set(motifs.values())), len(scenes))

    def test_a_single_scene_still_gets_an_environment(self):
        scenes = self._scenes(1)
        motifs = creative.generate_scene_environments(concept(), scenes)
        self.assertEqual(len(motifs), 1)

    def test_scenes_cycle_across_environments_not_all_identical(self):
        scenes = self._scenes(9)
        motifs = creative.generate_scene_environments(concept(), scenes)
        self.assertGreater(len(set(motifs.values())), 1)

    def test_prompt_carries_duration_and_viewing_behavior_not_a_fixed_count(self):
        """The count is not pre-computed and handed to the model as a fixed
        requirement - the model is given the signals (duration, format,
        viewing behavior) and decides. This asserts those signals actually
        reach the prompt, since that is the only part of "let the model
        reason about it" this test suite can verify without a live LLM call.
        """
        prompt = creative.SCENE_ENVIRONMENT_PROMPT_TEMPLATE.format(
            content_format="ambient", niche="pets",
            target_audience="dog owners",
            duration_description=creative._duration_description(14400),
            viewing_behavior="owner presses play then leaves for hours",
            base_prompt="soft domestic scenes",
            direction_section="", research_section="")
        self.assertIn("4.0 hours", prompt)
        self.assertIn("owner presses play then leaves for hours", prompt)
        self.assertNotIn("Return exactly", prompt)

    def test_duration_description_formats_hours_and_minutes(self):
        self.assertEqual(creative._duration_description(None), "unknown")
        self.assertEqual(creative._duration_description(600), "10 minutes")
        self.assertIn("hours", creative._duration_description(14400))

    def test_sanitize_environments_never_exceeds_scene_count_or_the_ceiling(self):
        many = {f"env{i}": f"d{i}" for i in range(20)}
        self.assertEqual(len(creative._sanitize_environments(many, scene_count=3)), 3)
        self.assertLessEqual(
            len(creative._sanitize_environments(many, scene_count=100)),
            creative._ABSOLUTE_MAX_ENVIRONMENTS)


class SdkPythonTest(unittest.TestCase):
    """Regression: importlib.util.find_spec raises ModuleNotFoundError
    (rather than returning None) for a dotted name whose parent package
    isn't importable at all, e.g. 'google.genai' with no 'google' on the
    system interpreter. That must fall through to .venv, not crash."""

    def test_missing_dotted_parent_package_does_not_raise(self):
        try:
            creative._sdk_python("gemini")
        except ModuleNotFoundError:
            self.fail("_sdk_python must not raise when the parent package is absent")

    def test_missing_top_level_package_does_not_raise(self):
        try:
            creative._sdk_python("anthropic")
        except ModuleNotFoundError:
            self.fail("_sdk_python must not raise when the module is absent")


class ExtractJsonTest(unittest.TestCase):

    def test_extracts_plain_json(self):
        self.assertEqual(creative._extract_json('{"a": 1}'), {"a": 1})

    def test_extracts_json_wrapped_in_markdown_fence(self):
        text = "Here you go:\n```json\n{\"a\": 1}\n```\n"
        self.assertEqual(creative._extract_json(text), {"a": 1})

    def test_raises_creative_error_when_no_json_present(self):
        with self.assertRaises(creative.CreativeError):
            creative._extract_json("no json here at all")


if __name__ == "__main__":
    unittest.main()


class RulesFallbackTest(unittest.TestCase):
    """No model reachable (no SDK, no credit): un-narrated work still gets a
    brief and a usable direction; narration is never invented."""

    CONCEPT = {"id": "c", "working_title_pattern": "Rain on a Misty Forest Cabin",
               "visual_concept": "a misty forest cabin window at night in rain",
               "audio_concept": "rain, soft pads", "audio_source_requirement":
               "synthesisable_now", "content_format": "ambient sleep video"}

    def setUp(self):
        self._mode = os.environ.pop("TEST_MODE", None)
        self.addCleanup(lambda: self._mode is not None
                        and os.environ.__setitem__("TEST_MODE", self._mode))
        patcher = unittest.mock.patch.object(
            creative, "_call_llm", side_effect=creative.CreativeError("no credit"))
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_brief_falls_back_to_rules_for_unnarrated_video(self):
        brief = creative.generate_brief(self.CONCEPT, 9000)
        self.assertEqual(brief["narration_script"], "")
        self.assertIn("2.5 Hours", brief["title"])
        self.assertTrue(brief["written_by"].startswith("rules"))

    def test_narrated_video_still_fails_without_a_model(self):
        with self.assertRaises(creative.CreativeError):
            creative.generate_brief(dict(self.CONCEPT, audio_source_requirement="tts_required"), 600)

    def test_direction_falls_back_to_distinct_vantages(self):
        direction = creative.generate_visual_direction(self.CONCEPT, 20)
        self.assertTrue(creative.vd.is_usable(direction))
        descriptions = [e["description"] for e in direction["environments"]]
        self.assertGreaterEqual(len(set(descriptions)), 4)
        self.assertTrue(all("cabin" in d and "rain" in d for d in descriptions))
