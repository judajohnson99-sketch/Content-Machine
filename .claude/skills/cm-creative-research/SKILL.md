---
name: cm-creative-research
description: Creative research and competitor analysis for Content Machine videos without copying anyone's expression. Use when researching a niche, studying competing videos, planning a concept, or preparing a research brief or subject research.
---

# Creative research and competitor analysis

The pipeline already does the mechanical parts. Use it; do not re-implement it.

- **Format intelligence** (`scripts/research.py`; run `python3 scripts/research.py providers|observe|profile|show|brief-show|brief-set|findings`): learns *structure and statistics* of a niche (length, image-change cadence, narration density, recurring sections, ambience). It refuses scripts, transcripts, thumbnail imagery and verbatim titles at the gate.
- **Subject research** (`./content-machine research <id>`): search-backed facts with sources, cached once at `research/subjects/<id>.json`, minimum 2 sources, fail-closed. Only for concepts with `requires_subject_research`. Never answer a factual claim from model memory.
- **Concept ranking** (`./content-machine experiment list|show|blockers`): expected information value and what blocks production.

## Competitor analysis protocol
1. Run `./content-machine knowledge context "<niche or question>"` once; open only the note it points to.
2. Record only *shape facts* you could state aloud about a category ("runs 8-12 min, image changes every ~6 s"). Never record or paraphrase a competitor's script, title wording, thumbnail composition or channel-specific hooks.
3. Each observation needs a source and date; separate observed fact from inference. Thin or single-source evidence is reported as thin, not rounded up.
4. Translate findings into *production directives* (shot length, movement style, dissolve length, audio layers) via a research brief; the storyboard and sound design consume those.
5. Durable, verified findings go to the vault: `./content-machine knowledge capture --type finding ... --confidence VERIFIED`. Search the vault first; no operational activity (job status, QC verdicts) in notes.

## Output
A short brief: niche shape, 3-5 directives with evidence, open questions, what would change the plan. No inline dumps of source text.
