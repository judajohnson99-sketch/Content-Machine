---
title: "Owner Media Is Referenced by Content Across Machines"
type: decision
confidence: VERIFIED
captured_at: 2026-09-21T13:38:36.051464+00:00
contributor: "content-machine-agent"
evidence: "User source-location clarification 2026-09-21; scripts/media.py; tests/test_media.py; docs/editing-and-ingestion.md"
tags:
  - knowledge/captured
  - knowledge/decision
---

# Owner Media Is Referenced by Content Across Machines

**Keep owner originals on their source machine; merge host-bound content-hash inventories and stage only selected media for productions. Source inspection, content annotation, rights and quality are distinct claims.**

Owner media is a legitimate input alongside generated media. The owner library stays on its machine; catalog identity is the SHA-256 of the bytes, while source paths are locations bound to a host. An inventory can cross to the control plane without moving its media. A production must resolve and rehash selected bytes locally before using them. Only the selected subset needs staging for a VPS render that can continue when the source machine goes offline.

This extends the separation in [[Remote GPU Work Is a Queue Not a Provider]]: the VPS owns production state, and optional workers make outbound connections. Owner originals are durable data, unlike replaceable worker scratch space. A future media-ingestion worker needs an explicit capability and payload; owner files must not masquerade as generated ComfyUI results.

Inspection can establish technical streams and successful decoding. Content descriptions require inspection of the content; [[Rights and Licensing]] require source evidence and permission terms. Neither establishes the human judgment in [[Provenance and Production-Grade Claims]]. Imported sources remain subject to the [[Publication Gate]], including detecting changed bytes after selection.

The implemented catalog, request-scoped source/preview transfer, still-image
bootstrap and native Kdenlive/MLT lowering are documented in
docs/editing-and-ingestion.md. A real Dreamdrip production still depends on
the owner transferring the selected source bytes; the YouTube Audio Library
tracks remain individually unverified until track-level records are supplied.

## Evidence

- User source-location clarification 2026-09-21; scripts/media.py; tests/test_media.py; docs/editing-and-ingestion.md

*Captured 2026-09-21T13:38:36.051464+00:00 by content-machine-agent. Confidence: VERIFIED.*
