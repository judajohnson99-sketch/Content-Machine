---
title: "Rights and Licensing"
type: decision
confidence: VERIFIED
tags:
  - knowledge/doctrine
---

# Rights and Licensing

**Licensing is verified, not assumed. Audio whose rights cannot be established
is refused, not silently used.**

Rights must be supplied explicitly for a local audio asset — the pipeline never
guesses. `commercial_use_cleared` and any required attributions are recorded in
the audio manifest, which is deliberately kept out of the "regenerable" bucket:
a track can be re-synthesised from its plan, but a licence record cannot be
recovered by inspecting a waveform.

This is the reason several concepts are blocked on sourcing rather than on
capability. Rain, wave and cafe beds all need a provably clean source before
they can be produced ([[Adult Sleep]], [[Focus Ambience]]), and every
music-generation route in [[Kids Sleep]] and [[Focus Ambience]] carries an
unresolved question about whether its output is licence-clean for monetized
commercial use.

Synthesised sources sidestep the problem entirely, which is why brown/pink noise
concepts moved first — see [[Experiment Log]]. The one voice in use is licensed
and attribution-bearing: see [[Local Narration Capability]].

Source of truth: `scripts/audio.py` (rights gate),
`projects/<id>/audio/audio_manifest.json`.

## Related
- [[Publication Gate]]
- [[Open Research Questions]]
