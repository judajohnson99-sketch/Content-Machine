---
title: "A Layer Gain Is Only a Mix Balance After Gain Staging"
type: decision
confidence: VERIFIED
captured_at: 2026-09-20T02:17:21.269678+00:00
contributor: "content-machine-agent"
evidence: "projects/ambient-dogs-home-alone-20260919/audio/audio_manifest.json, 2026-09-20: the music bed measured -32.4 LUFS while the 'ambience' layers meant to sit 22 dB beneath it measured -28.9 LUFS - natively 3.5 dB louder than the bed."
tags:
  - knowledge/captured
  - knowledge/decision
  - audio
  - mixing
---

# A Layer Gain Is Only a Mix Balance After Gain Staging

**Every layer of a designed soundscape is normalised to one reference loudness before its authored gain is applied, because provider output levels differ by more than 20 dB and an un-staged 'gain_db' is relative to nothing.**

A sound design document states a balance: a bed at 0 dB, an ambience layer at -22 dB, an occasional event at -35 dB. Those numbers only describe a balance if every layer starts from the same level, and ffmpeg-synthesised sources do not: the `music` provider, the `ambience` provider and the `events` provider each land wherever their synthesis happens to land, and on the first real run they differed by more than 25 dB in the wrong direction.

`audio.stage_gain` measures each rendered layer with ebur128 and computes the trim that puts it at `LAYER_REFERENCE_LUFS` (-20). `_mix` applies that trim *before* the authored gain, and the trim, the measured loudness and the resulting mix level are all recorded per layer in the manifest, so the balance a reviewer reads is the balance that was rendered. The trim is capped at 18 dB: a layer needing more than that is not the layer the plan thought it was, and silently applying 30 dB of makeup gain would hide that rather than show it.

Staging is opt-in (`plan.gain_staging`) and `sound_design.compile_soundscape` turns it on, so hand-authored plans keep their previous behaviour exactly.

This is why a designed soundscape now sounds like a mix rather than like several generated files played at once - the thing the milestone was actually judged on.

## Evidence

- projects/ambient-dogs-home-alone-20260919/audio/audio_manifest.json, 2026-09-20: the music bed measured -32.4 LUFS while the 'ambience' layers meant to sit 22 dB beneath it measured -28.9 LUFS - natively 3.5 dB louder than the bed.

## Related

- [[Audio Production Grade Is a Human Verdict Bound to the Plan]]
- [[Technical Validity Is Not Production Quality]]

*Captured 2026-09-20T02:17:21.269678+00:00 by content-machine-agent. Confidence: VERIFIED.*
