#!/usr/bin/env python3
"""Tests for the creative brief: the deterministic audio/style mappers and
the LLM brief under TEST_MODE (no network is ever contacted here).
"""
import json
import os
import sys
import unittest
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
        for key in ("title", "description", "narration_script", "image_prompt", "negative_prompt"):
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
