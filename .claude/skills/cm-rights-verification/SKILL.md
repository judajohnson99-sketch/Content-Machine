---
name: cm-rights-verification
description: Media asset selection, quality control and rights/licence verification before Content Machine publishing. Use when choosing owner or discovered media, recording rights evidence, auditing licences or attribution, or checking whether a package is clear to publish.
---

# Media selection, QC and rights verification

Principle: **licensing is verified, not assumed.** Audio or media whose rights cannot be established is refused. This skill is a checklist over existing gates; it never clears rights.

## Components
- Catalog (`./content-machine media scan|list|annotate|check|provenance|merge|export|receive`): reference-only, SHA-256 identity, technical inspection separate from annotations and rights. Never modifies originals.
- Owner selection (`scripts/ownermedia.py`, `owner-media`, `import-media`): a selection is a reference plus rights evidence, hash-verified when staged.
- Discovery (`scripts/discovery.py`, Openverse): indexed licensing is a **lead**, not a clearance; imports stay blocked until the creator confirms actual terms.
- Audio gate: `run` refuses READY_FOR_REVIEW when `audio_manifest.json` has `commercial_use_cleared: false`. `audio.provider_file` requires `source`, `license`, `commercial_use` and refuses `commercial_use=false`.

## Rights record (see `config/media-rights.example.json`, deliberately uncleared)
`source`, `creator`, `license`, `commercial_use`, `attribution_required`, `attribution_text`, `evidence` (terms page, receipt, permission), `retrieved_utc`. Names like "royalty-free" are not evidence.

## Checklist
1. Every asset used has a rights record with real evidence; unknown stays unknown.
2. `commercial_use` true for monetised publishing; CC-BY style licences have attribution text carried into the package.
3. Platform-specific tracks (e.g. YouTube Audio Library) need track-level confirmation; do not generalise from one track.
4. Selected media hashes still match the catalog (`media check`); changed sources invalidate the record.
5. Voices: check the voice's own licence (`audio.py voices`); non-commercial voices are listed in `REJECTED_VOICES`.
6. Output a table: asset, source, licence, commercial-use, attribution, evidence, status (cleared / unverified / blocked).

## Boundaries
Do not use `--allow-unverified-audio` except for a private review bootstrap, and say so. Never publish, upload or mark rights cleared on a person's behalf.
