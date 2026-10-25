---
name: cm-audio-production
description: Audio production, mixing, and mastering for Content Machine videos. Use when designing or debugging an audio plan, layer gains, loudness, narration, or audio grading.
---

# Audio production, mixing, mastering

Everything goes through `scripts/audio.py` (`compose`) and
`scripts/sound_design.py`; build with `./content-machine audio <id>`.

- **Plan** lives at `metadata.json` -> `audio_plan.composition`: layers
  (`provider`, `params`, `gain_db`, `start_seconds`), `normalize`,
  `target_lufs` (-20 in the README example), fades. Output is 48 kHz stereo PCM.
- **Providers:** `noise`, `tone`, `rain`, `silence` (generated-original), `file`
  (must declare source/license/commercial_use), `tts` (Piper, per-voice licence
  from its MODEL_CARD).
- **Mixing:** gain-stage layers first; a layer gain is only a mix balance after
  gain staging (vault note of that name). Generated sources are synthesised at
  exact length (no loop seam); file sources loop with `crossfade_loop_seconds`.
  Mastering is the loudness normalisation step plus duration/silence check.
- **Deterministic:** seeded noise, zeroed Piper noise scales; same plan, same
  bytes. Keep it so.
- **Rights:** audio whose rights cannot be established is refused. `lessac` and
  `hfc_female` voices are blocked in code; do not unblock them.
  `commercial_use_cleared: false` blocks READY_FOR_REVIEW.
- **Quality:** a valid mix is not pleasant audio. Where the source cannot judge
  itself, only a named person sets `provenance.audio.production_grade`
  (`./content-machine audio-grade`, human-run after listening; Claude must not run it); it is voided when the plan
  changes. Never set it.

Check with `python3 -m unittest tests.test_audio`.
