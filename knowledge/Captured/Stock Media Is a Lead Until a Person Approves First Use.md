---
title: "Stock Media Is a Lead Until a Person Approves First Use"
type: decision
confidence: VERIFIED
captured_at: 2026-10-22T21:49:39.990925+00:00
contributor: "content-machine-agent"
evidence: "scripts/asset_licensing.py (stock_rights, approve, decide_use, AUTONOMY_LEVELS); scripts/visual_assets.py select/ingest_stock; scripts/ownermedia.py verification_problems; tests/test_asset_ranking.py PolicyTests; tests/test_visual_assets.py SelectionTests, ProductionTests"
tags:
  - knowledge/captured
  - knowledge/decision
---

# Stock Media Is a Lead Until a Person Approves First Use

**A stock provider's published licence is recorded as evidence, but commercial_use stays false until a person approves first use; search never downloads, generates or spends.**

Stock photography and footage (Pexels, Pixabay, Unsplash, through their official
APIs) joins the same path as the owner's own media rather than creating a new
one: an approved stock file becomes a catalog asset pinned to a storyboard scene,
so [[Owner Media Is Chosen Per Role and Staged Into the Production]] already
describes how it is staged, re-hashed at the gate and carried into the render,
the package and the Kdenlive archive.

What is new is the rule about rights. A provider saying "free for commercial use"
is a lead, not a clearance, so a candidate's rights record carries the provider's
terms beside a top-level `commercial_use` that stays false. Only an approval by a
named person flips it, and an ambiguous record (no creator, no source page) needs
a written reason while terms that rule use out cannot be approved at all. This is
the same refusal to infer clearance recorded in [[Rights and Licensing]], applied
to pictures. Because `media.rights_record` already refuses `commercial_use: false`,
anything that reaches the catalog unapproved is blocked by the existing gate, and
`verification_problems` additionally rejects an external record that claims
commercial use without a genuine approval - an edited metadata file is not an
approval.

Search is therefore read-only: it ranks, records previews and licences, and
downloads nothing. The approval is the single step that fetches bytes, and the
rights shown are the ones stored at search time, never ones a client sends. An
asset approved once is an ordinary library asset and is reused without asking.

Generation is a candidate beside stock, not a competing mechanism. Choosing it
hands the scene to the existing scenes stage, so a GPU that is off is a deferral
under [[Remote GPU Work Is a Queue Not a Provider]] and [[Depicted Imagery Defers to the GPU Queue]], never a failed search. Approval levels live in a table; only
the supervised row is enabled, and the graduated row is described but cannot be
switched on, which keeps [[Autonomy Boundaries]] and the production-grade rule in
[[Provenance and Production-Grade Claims]] intact: nothing here grades a picture.

## Evidence

- scripts/asset_licensing.py (stock_rights, approve, decide_use, AUTONOMY_LEVELS); scripts/visual_assets.py select/ingest_stock; scripts/ownermedia.py verification_problems; tests/test_asset_ranking.py PolicyTests; tests/test_visual_assets.py SelectionTests, ProductionTests

## Related

- [[Rights and Licensing]]
- [[Owner Media Is Chosen Per Role and Staged Into the Production]]
- [[Provenance and Production-Grade Claims]]
- [[Remote GPU Work Is a Queue Not a Provider]]
- [[Depicted Imagery Defers to the GPU Queue]]
- [[Autonomy Boundaries]]

*Captured 2026-10-22T21:49:39.990925+00:00 by content-machine-agent. Confidence: VERIFIED.*

Owner decisions of 2026-10-25 tighten this. Reuse of an approved asset is not a
blanket pass: the approval records what it covers (licence, terms reference,
commercial and modified use, redistribution only inside the video), and a later
use is automatic only while those still hold and any required credit can be
given - the rule the disabled graduated level is defined by. The credits the
licences require are appended to the published description automatically, and a
credit that cannot be given blocks review rather than being skipped. Unsplash
stays off even with a key until its production access and usage terms are
verified and a person enables it.
