---
name: cm-audio-review
description: Audio production, mixing and mastering review for Content Machine tracks. Use when planning an audio layer stack, tuning loudness or mix, or judging whether a composed track is fit for review.
---

# Audio production, mixing and mastering

The mix/master chain is code (`scripts/audio.py`, `scripts/sound_design.py`): layered providers → per-layer reference loudness (-20 LUFS) → mix → fit to exact duration → `loudnorm` to target with a true-peak ceiling. Plans come from `creative.build_audio_composition` and `sound_design`. Do not hand-mix with ad-hoc ffmpeg.

## Tools
- `./content-machine audio <id>` builds the track; `python3 scripts/audio.py providers|voices|compose`.
- `audio.measure(path)` / `audio.assess(...)` give integrated LUFS, true peak, loudness range, brightness, clipping/silence findings against a **listening context** (`LISTENING_CONTEXTS` in `audio.py`: `background_sleep` -23 LUFS, `background_focus` -20, `ambient_watch` -18 (default), `narration` -16, all ±2, TP ≤ -1 dBFS).

## Review procedure
1. Pick the right listening context for the concept; do not loosen criteria to pass.
2. Read the findings: `block` = objectively broken (clipping, silence, far off target); `warn` = a person should listen; `info` = context.
3. Check structure: layers present as planned, no loop seam, enter/leave fades, occasional events spaced, narration intelligible over bed (speech ~ -16 LUFS context).
4. Check `audio_manifest.json`: every layer has a licence determination and `commercial_use_cleared` (see `cm-rights-verification`).
5. State plainly: passing = "nothing measurable is wrong", not "sounds good". Ask the human to listen before `audio-grade`.

## Boundaries
Never run `audio-grade`/set production-grade for a person. Synthesised layers are `generated-original`; music with unknown rights is refused, not substituted silently. Narration voices carry their own licence (non-commercial voices sit in `REJECTED_VOICES`).
