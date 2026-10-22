---
name: cm-creative-research
description: Creative research and competitor analysis for Content Machine concepts. Use when researching a niche, subject, or competing channels, or deciding which concept to produce next.
---

# Creative research and competitor analysis

Research is a pipeline stage with code, not a freehand task. Use it; do not
re-implement it.

- **Subject facts:** `./content-machine research <video-id>` runs
  `scripts/subject_research.py` (search-backed, cached once at
  `research/subjects/<id>.json`, fails closed on thin or unsourced results).
  Never answer subject questions from what a model "knows".
- **Format and niche doctrine:** `scripts/research.py` profiles;
  `./content-machine knowledge query "<niche or constraint>"` for measured
  production constraints and prior findings. Search the vault before researching
  again.
- **Competitor / reference analysis:** gather sourced observations only (title
  patterns, length, pacing, audio, thumbnail grammar), each with a URL and date.
  Separate observation from inference. Do not copy a competitor's title,
  thumbnail, script, or footage; extract the pattern, not the asset.
- **Ranking concepts:** `scripts/experiment.py` and `experiments/concepts.json`
  are authoritative for hypotheses and ranking.
- **Findings change production** through directives (shot length, movement,
  dissolve, audio layers) that storyboard and sound design apply and record in
  `research_influence.json`. Phrase findings so they can become a directive.
- **Persist** only durable knowledge:
  `./content-machine knowledge capture --type finding --evidence "<URL/measurement>" ...`
  (search for an existing note first). Never capture run activity.

If no search provider is configured, say so and stop; do not substitute guesses.
Details: README "Experiments", AGENTS.md "Pipeline stages".
