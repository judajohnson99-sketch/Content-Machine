#!/usr/bin/env python3
"""Tests for the sound-design layer.

The design pass is the one part of the audio path that asks a model what
the video should sound like. These tests pin the properties that make that
safe: the catalogue is closed, the rights decision is never re-made, the
listening context comes from the concept's own words, and a design that is
unavailable degrades to the routed composition rather than to silence or
to a layer nobody chose.
"""
import os
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import audio  # noqa: E402
import sound_design  # noqa: E402

SLEEP_CONCEPT = {
    "id": "sleep-brown-noise-dark",
    "niche": "sleep_ambient",
    "content_format": "8 hours of brown noise with a dark screen",
    "audio_concept": "brown noise, unchanging, no music",
    "target_audience": "people who cannot fall asleep",
}

TALK_CONCEPT = {
    "id": "explainer",
    "niche": "explainers",
    "content_format": "a narrated idea explainer",
    "audio_concept": "a calm voice over a quiet bed",
    "target_audience": "curious commuters",
}

ROUTED_BED = {
    "target_seconds": 600.0,
    "layers": [{"id": "bed", "provider": "noise",
                "params": {"color": "brown"}, "gain_db": 0.0,
                "fade_in_seconds": 5.0, "fade_out_seconds": 5.0}],
}


class TestListeningContext(unittest.TestCase):
    """How the audio is heard, inferred from the concept's own language."""

    def test_sleep_language_selects_the_sleep_context(self):
        self.assertEqual(
            sound_design.infer_listening_context(SLEEP_CONCEPT), "background_sleep")

    def test_a_narrated_concept_is_a_narration_context_whatever_it_is_about(self):
        self.assertEqual(
            sound_design.infer_listening_context(SLEEP_CONCEPT, kind="narration"),
            "narration")

    def test_focus_language_selects_the_focus_context(self):
        self.assertEqual(
            sound_design.infer_listening_context(
                {"niche": "study", "content_format": "deep work session"}),
            "background_focus")

    def test_a_concept_with_nothing_recognisable_gets_the_neutral_default(self):
        self.assertEqual(
            sound_design.infer_listening_context({"niche": "woodworking"}),
            audio.DEFAULT_LISTENING_CONTEXT)

    def test_every_inferable_context_actually_exists_in_the_audio_module(self):
        """The two modules must not drift: a context named here with no
        criteria behind it would silently fall back to the default."""
        named = {context for _, context in sound_design._CONTEXT_KEYWORDS}
        named.add(audio.DEFAULT_LISTENING_CONTEXT)
        named.add("narration")
        self.assertTrue(named <= set(audio.LISTENING_CONTEXTS))


class TestCatalogueIsClosed(unittest.TestCase):
    """The design pass may only ask for what this build can render."""

    def test_the_catalogue_is_derived_from_the_audio_module(self):
        catalogue = sound_design.capability_catalogue()
        self.assertEqual(catalogue["ambience_elements"],
                         sorted(audio.AMBIENCE_ELEMENTS))
        self.assertEqual(catalogue["event_elements"],
                         sorted(audio.EVENT_ELEMENTS))

    def test_an_element_this_build_lacks_is_recorded_unmet_not_substituted(self):
        design = sound_design.sanitize_design({
            "intent": "a forest at dusk",
            "listening_context": "background_sleep",
            "ambience": [{"element": "cicadas", "gain_db": -20},
                         {"element": "night_air", "gain_db": -22}],
            "detail": [{"element": "owl", "every_seconds": 60, "gain_db": -26}],
        }, target_seconds=600)
        self.assertEqual([a["element"] for a in design["ambience"]], ["night_air"])
        self.assertEqual(design["detail"], [])
        unmet = " ".join(design["wanted_but_unavailable"])
        self.assertIn("cicadas", unmet)
        self.assertIn("owl", unmet)

    def test_gains_are_clamped_so_support_cannot_outrun_the_bed(self):
        design = sound_design.sanitize_design({
            "ambience": [{"element": "stream", "gain_db": 12.0}],
            "detail": [{"element": "chime", "gain_db": 6.0, "every_seconds": 30}],
        }, target_seconds=600)
        self.assertLessEqual(design["ambience"][0]["gain_db"],
                             sound_design.ROLE_GAIN_LIMITS["ambience"][1])
        self.assertLessEqual(design["detail"][0]["gain_db"],
                             sound_design.ROLE_GAIN_LIMITS["detail"][1])

    def test_event_intervals_are_held_inside_a_usable_range(self):
        design = sound_design.sanitize_design({
            "detail": [{"element": "drip", "every_seconds": 0.25, "gain_db": -30}],
        }, target_seconds=600)
        self.assertGreaterEqual(design["detail"][0]["every_seconds"],
                                sound_design.MIN_EVENT_INTERVAL_SECONDS)

    def test_layer_counts_are_capped(self):
        design = sound_design.sanitize_design({
            "ambience": [{"element": e, "gain_db": -20}
                         for e in ("stream", "night_air", "wind_low",
                                   "snowfall", "room_tone")],
        }, target_seconds=600)
        self.assertLessEqual(len(design["ambience"]),
                             sound_design.MAX_AMBIENCE_LAYERS)

    def test_fades_scale_with_the_piece_rather_than_being_fixed(self):
        short = sound_design.sanitize_design(
            {"dynamics": {"fade_in_seconds": 400, "fade_out_seconds": 400}},
            target_seconds=60)
        self.assertLessEqual(short["dynamics"]["fade_in_seconds"], 10.0)
        long = sound_design.sanitize_design(
            {"dynamics": {"fade_in_seconds": 400, "fade_out_seconds": 400}},
            target_seconds=28800)
        self.assertGreater(long["dynamics"]["fade_in_seconds"], 10.0)

    def test_a_reply_full_of_nonsense_still_yields_a_renderable_design(self):
        design = sound_design.sanitize_design({
            "intent": None, "listening_context": "shouting",
            "ambience": ["not an object"], "detail": None, "bed": "nope",
            "dynamics": 5,
        }, target_seconds=600)
        self.assertEqual(design["listening_context"],
                         audio.DEFAULT_LISTENING_CONTEXT)
        self.assertEqual(design["ambience"], [])
        self.assertIn("gain_db", design["bed"])


class TestCompilation(unittest.TestCase):
    """Design document + routed bed -> an executable plan."""

    def design(self, **overrides):
        base = {
            "intent": "a quiet room at the edge of the sea",
            "listening_context": "background_sleep",
            "ambience": [{"element": "ocean_surf", "gain_db": -18.0,
                          "reason": "the sea the concept names"}],
            "detail": [{"element": "wood_creak", "every_seconds": 120.0,
                        "gain_db": -30.0, "reason": "the house settling"}],
            "bed": {"gain_db": -2.0, "lowpass_hz": 1800.0, "highpass_hz": 0.0,
                    "width": 1.2, "swell": {"rate_hz": 0.02, "depth": 0.2}},
            "dynamics": {"fade_in_seconds": 8.0, "fade_out_seconds": 20.0},
            "wanted_but_unavailable": [],
        }
        base.update(overrides)
        return base

    def test_the_routed_bed_keeps_its_provider_and_params(self):
        plan = sound_design.compile_soundscape(self.design(), ROUTED_BED, 600.0)
        bed = plan["layers"][0]
        self.assertEqual(bed["provider"], "noise")
        self.assertEqual(bed["params"], {"color": "brown"})

    def test_design_shapes_the_bed_without_replacing_it(self):
        plan = sound_design.compile_soundscape(self.design(), ROUTED_BED, 600.0)
        bed = plan["layers"][0]
        self.assertEqual(bed["lowpass_hz"], 1800.0)
        self.assertEqual(bed["width"], 1.2)
        self.assertEqual(bed["swell"], {"rate_hz": 0.02, "depth": 0.2})
        self.assertEqual(bed["gain_db"], -2.0)
        self.assertEqual(bed["fade_in_seconds"], 8.0)

    def test_supporting_layers_are_appended_with_the_designed_elements(self):
        plan = sound_design.compile_soundscape(self.design(), ROUTED_BED, 600.0)
        providers = [l["provider"] for l in plan["layers"]]
        self.assertEqual(providers, ["noise", "ambience", "events"])
        self.assertEqual(plan["layers"][1]["params"]["element"], "ocean_surf")
        self.assertEqual(plan["layers"][2]["params"]["every_seconds"], 120.0)

    def test_the_master_bus_follows_the_listening_context(self):
        plan = sound_design.compile_soundscape(self.design(), ROUTED_BED, 600.0)
        criteria = audio.LISTENING_CONTEXTS["background_sleep"]
        self.assertEqual(plan["listening_context"], "background_sleep")
        self.assertEqual(plan["master"]["lufs"],
                         criteria["integrated_lufs_target"])
        self.assertTrue(plan["master"]["limiter"])

    def test_the_compiled_plan_validates_against_the_audio_module(self):
        plan = sound_design.compile_soundscape(self.design(), ROUTED_BED, 600.0)
        target, layers = audio.validate_plan(plan)
        self.assertEqual(target, 600.0)
        self.assertEqual(len(layers), 3)

    def test_everything_under_narration_ducks_for_it(self):
        routed = {"target_seconds": 300.0, "layers": [
            {"id": "narration", "provider": "tts", "params": {"text": "hello"}}]}
        plan = sound_design.compile_soundscape(self.design(), routed, 300.0)
        supporting = [l for l in plan["layers"] if l["provider"] != "tts"]
        self.assertTrue(supporting)
        for layer in supporting:
            self.assertEqual(layer["duck_under"], "narration")
        # And the plan the ducking describes is still a valid one.
        audio.validate_plan(plan)

    def test_a_narration_layer_is_never_treated_as_a_bed_to_be_filtered(self):
        routed = {"target_seconds": 300.0, "layers": [
            {"id": "narration", "provider": "tts", "params": {"text": "hello"}}]}
        plan = sound_design.compile_soundscape(self.design(), routed, 300.0)
        self.assertNotIn("lowpass_hz", plan["layers"][0])

    def test_no_routed_composition_compiles_to_nothing(self):
        self.assertIsNone(
            sound_design.compile_soundscape(self.design(), None, 600.0))
        self.assertIsNone(
            sound_design.compile_soundscape(self.design(), {"layers": []}, 600.0))

    def test_an_empty_design_is_a_real_answer_not_a_failure(self):
        plan = sound_design.compile_soundscape(
            self.design(ambience=[], detail=[]), ROUTED_BED, 600.0)
        self.assertEqual(len(plan["layers"]), 1)
        audio.validate_plan(plan)


class TestDesignPass(unittest.TestCase):
    """The live path. TEST_MODE short-circuits to a canned design before any
    provider is reached, so these tests must turn it off to exercise the
    prompt and the reply handling at all."""

    def setUp(self):
        self._test_mode = os.environ.pop("TEST_MODE", None)
        if self._test_mode is not None:
            self.addCleanup(os.environ.__setitem__, "TEST_MODE", self._test_mode)

    def test_the_prompt_offers_only_elements_that_exist(self):
        captured = {}

        def fake_llm(prompt):
            captured["prompt"] = prompt
            return ('{"intent": "x", "listening_context": "background_sleep",'
                    ' "ambience": [], "detail": [], "bed": {},'
                    ' "dynamics": {}, "wanted_but_unavailable": []}')

        sound_design.design_soundscape(SLEEP_CONCEPT, 600.0, llm=fake_llm)
        for element in audio.AMBIENCE_ELEMENTS:
            self.assertIn(element, captured["prompt"])
        for element in audio.EVENT_ELEMENTS:
            self.assertIn(element, captured["prompt"])

    def test_the_prompt_states_the_inferred_listening_context(self):
        captured = {}

        def fake_llm(prompt):
            captured["prompt"] = prompt
            return '{"intent": "x", "ambience": [], "detail": []}'

        sound_design.design_soundscape(TALK_CONCEPT, 600.0, kind="narration",
                                       llm=fake_llm)
        self.assertIn("narration", captured["prompt"])

    def test_an_unreadable_reply_raises_rather_than_inventing_a_design(self):
        with self.assertRaises(sound_design.SoundDesignError):
            sound_design.design_soundscape(
                SLEEP_CONCEPT, 600.0, llm=lambda prompt: "I'm sorry Dave")


if __name__ == "__main__":
    unittest.main()


class RulesDesignFallbackTest(unittest.TestCase):
    def test_unreachable_model_designs_layers_from_the_concepts_words(self):
        concept = {"audio_concept": "rain, soft ambient music",
                   "visual_concept": "a misty forest cabin at night",
                   "niche": "adult_sleep", "content_format": "sleep video"}
        old = os.environ.pop("TEST_MODE", None)
        try:
            def broken(prompt):
                raise RuntimeError("no credit")
            design = sound_design.design_soundscape(concept, 9000, llm=broken)
        finally:
            if old is not None:
                os.environ["TEST_MODE"] = old
        elements = [a["element"] for a in design["ambience"]]
        self.assertIn("rain_on_glass", elements)
        self.assertIn("cabin_hum", elements)
        self.assertEqual(design["listening_context"], "background_sleep")
        self.assertEqual(design["detail"], [])
