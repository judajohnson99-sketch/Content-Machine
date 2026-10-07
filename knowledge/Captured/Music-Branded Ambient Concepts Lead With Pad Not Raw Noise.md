---
title: "Music-Branded Ambient Concepts Lead With Pad, Not Raw Noise"
type: decision
confidence: VERIFIED
captured_at: 2026-09-19T10:32:37.924494+00:00
contributor: "Claude Sonnet 5"
evidence: "scripts/creative.py build_audio_composition, _resolve_mood_chord; scripts/audio.py provider_pad root_drift_ratios; tests/test_creative.py AudioCompositionTest (pad-led-bed cases)"
tags:
  - knowledge/captured
  - knowledge/decision
  - audio
  - pad-provider
  - creative
---

# Music-Branded Ambient Concepts Lead With Pad, Not Raw Noise

**build_audio_composition now leads with the pad provider as the primary 0dB bed (with slow root drift for target durations >=30 minutes) when a synthesisable_now concept names no explicit noise/tone/rain keyword but a mood or research finding calls it music; concepts that do name a texture keyword, or that give no mood/findings at all, are unaffected.**

Root cause: the ambient-dogs-home-alone-20260919 diagnostic render's audio_concept text ('Slow-tempo calm bed. Synthesisable or licensed.') matches none of build_audio_composition's noise/tone/rain keywords, so it fell through to _DEFAULT_SYNTHESISABLE (raw brown noise) even though the concept and its research brief both call this 'soft ambient music'. The result read as static, not music.

Fix: when no keyword matches, build_audio_composition now calls _resolve_mood_chord(mood, findings) (extracted from the existing _mood_pad_layer). If a chord is resolvable, the pad becomes the sole primary bed at 0dB gain instead of a quiet -14dB secondary layer under noise; for target_seconds >= PAD_LONGFORM_SECONDS (30 min) it also sets root_drift_ratios so a multi-hour pad is not frozen on one exact frequency (tonic / +P4 / tonic / -P4, same chord shape throughout so the drift is always consonant — implemented in scripts/audio.py::provider_pad via new _render_pad_segment/_crossfade_concat helpers).

**A pad is a musical layer, not a good one.** An early version of audio.py::_quality_check reported `has_musical_layer: true` whenever a pad provider was present, and that field was read as evidence the audio was actually pleasant. It was removed outright: _quality_check now reports only structural facts (layer count, distinct providers, has_variation) and says so explicitly, because nothing measurable there can judge whether a human finds the result relaxing — see [[Technical Validity Is Not Production Quality]]. Real improvements to how the pad *sounds* have to be real DSP: the mixed sine voices now pass through an ffmpeg chorus stage to widen them, within the stdlib+ffmpeg-only constraint of the render path.

Unaffected paths, preserved by keeping the keyword-match branch exactly as it was: concepts that name an explicit noise/tone/rain keyword still get that texture as the primary bed with the pad (if any) added as the existing quiet secondary layer; a concept with neither a keyword match nor a mood/finding falls back to raw brown noise exactly as before this existed.

## Evidence

- scripts/creative.py build_audio_composition, _resolve_mood_chord; scripts/audio.py provider_pad root_drift_ratios; tests/test_creative.py AudioCompositionTest (pad-led-bed cases)

## Related

- [[Research Brief Is the Projects Creative Input]]

*Captured 2026-09-19T10:32:37.924494+00:00 by Claude Sonnet 5. Confidence: VERIFIED.*
