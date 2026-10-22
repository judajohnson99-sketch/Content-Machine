---
name: cm-media-selection
description: Select and quality-control owner-provided media (stills, footage, audio) for Content Machine productions. Use when scanning, annotating, choosing, or staging catalogued owner media.
---

# Media asset selection and QC

Owner media is a first-class input via the reference-only catalog
(`scripts/media.py`, `ownermedia.py`, `media_sync.py`). Originals are never
modified or copied.

```bash
./content-machine media scan <dir>          # decode and inspect, no mutation
./content-machine media list --kind video --query '<terms>'
./content-machine media check               # recheck existence and content hashes
./content-machine owner-media ...           # use catalogued media for a production
./content-machine import-media ...          # new project from catalog IDs
```

- Select by **annotations and technical inspection**, never guessed filename
  meanings. Technical inspection and rights are recorded separately.
- Content hashes identify an asset across machines. A remote-source asset is
  *not* available locally; transfer a selected, hash-verified bundle
  (`media export` / `media receive`), never assume the path exists.
- Choose per role (visual, audio) and stage only what the production uses.
- Rights: `media provenance` records owner declarations and never clears rights
  automatically. `--allow-unverified-audio` is a private review bootstrap only
  and keeps the publication gate blocked.
- Perceptual fit (optional CLIP): `scripts/media_intelligence.py`; models are
  installed explicitly, never downloaded by scanning.

Reference: docs/editing-and-ingestion.md ("Local catalog workflow", "Getting
selected media to the VPS"). Test: `python3 -m unittest tests.test_media`.
