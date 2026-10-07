---
title: "Owner Media Is Chosen Per Role and Staged Into the Production"
type: decision
confidence: VERIFIED
captured_at: 2026-10-07T19:05:16.614932+00:00
contributor: "content-machine-agent"
evidence: "scripts/ownermedia.py; scripts/project.py set_owner_media/run_scenes/run_audio/_use_owner_plates; tests/test_ownermedia.py (18 tests); dashboard run 2026-10-07: 3 owner stills + owner track -> 20s render, QC PASS 15/15, Kdenlive archive"
tags:
  - knowledge/captured
  - knowledge/decision
---

# Owner Media Is Chosen Per Role and Staged Into the Production

**A production takes the owner's own media per stage role - visuals, music, ambience, sfx - staged into the project by content identity; an unfilled role still generates, and nothing about choosing one's own media constitutes a production-grade claim.**

Selection is per role, not per file: `visuals` feeds the scene pictures (or
the cycled plate set where a production has no scene plan), while `music`,
`ambience` and `sfx` become layers in the audio plan. A role nobody filled is
produced exactly as before, so owner media and generated media mix in one
edit and the routing policy is untouched.

Three properties the rest of the pipeline leans on. The selection is a
reference plus evidence: each entry records the asset's content identity, the
rights record a person supplied, and the path its bytes were resolved from,
and the bytes themselves are hardlinked into the project so a later stage
cannot be defeated by the source machine being off. The staged copy is
re-hashed against that identity at the gate, so a file swapped after
selection blocks review instead of being quietly published. And an owner
layer inherits the mix of the generated layer it replaces, including anything
ducking under it, so choosing your own bed does not silently undo a
soundscape's balance.

What it deliberately does not do is grade anything. Owner media removes the
reason a visual is *known* to be a placeholder; it does not make it
production-grade, and [[Provenance and Production-Grade Claims]] still
requires a person to say so. Audio carrying an owner track reports that fact
in the blocker rather than describing a synthesised stand-in that is no
longer in the file, and an absent verdict still blocks.

Footage is a first-class scene source: a clip scene renders through the
piecewise path described in [[Long-Form Renders Are Assembled Piece by
Piece]], looped and trimmed to its shot, and the Kdenlive export writes that
loop out as repeated clips rather than an entry claiming footage that does
not exist. Where a production has no scene timeline at all, footage is
refused with that reason instead of being dropped.

## Evidence

- scripts/ownermedia.py; scripts/project.py set_owner_media/run_scenes/run_audio/_use_owner_plates; tests/test_ownermedia.py (18 tests); dashboard run 2026-10-07: 3 owner stills + owner track -> 20s render, QC PASS 15/15, Kdenlive archive

## Related

- [[Owner Media Is Referenced by Content Across Machines]]
- [[Provenance and Production-Grade Claims]]
- [[Publication Gate]]
- [[Audio Production Grade Is a Human Verdict Bound to the Plan]]
- [[Long-Form Renders Are Assembled Piece by Piece]]

*Captured 2026-10-07T19:05:16.614932+00:00 by content-machine-agent. Confidence: VERIFIED.*
