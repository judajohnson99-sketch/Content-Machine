---
name: cm-publish-gate
description: Publishing readiness, approval, and media-rights verification for Content Machine. Use when asking whether a project is cleared to publish, checking voice/audio/image licences and attribution, or reasoning about the publication gate and human approval.
---

# Publishing and rights verification

Pipeline ends at `READY_FOR_REVIEW`; nothing after it is automated. Publishing
and analytics are not built. Approval is a human act:
`./content-machine approve <id>` needs a fresh gate digest, `reject` records a
no. **Claude must not run `approve`, `reject`, `visual-grade` or `audio-grade`:** they record a named human's verdict. Never publish, upload, or spend.

Verify, do not assume:
1. **Gate state:** `./content-machine status <id>` then `gate_blockers` in
   `scripts/project.py`. Fail-closed: an absent claim blocks.
2. **Images:** `provenance.images.production_grade` explicitly `true` by a person;
   procedural plates are provenance-stamped and flat fills measured, so metadata
   edits cannot relabel a placeholder. Never claim generated plates are
   photographs.
3. **Audio:** `audio_manifest.json` `commercial_use_cleared`; each layer's
   source/license; CC BY voices (e.g. `en_US-libritts_r-medium`) require the
   attribution string that flows into the package. Unknown rights = refuse.
4. **Footage/stills from owner media:** rights declared via `media provenance`;
   unverified stays blocked.
5. **Checklist:** `metadata.review_checklist` travels with the package; add
   items like "fact-check the narration" there, not as blockers.
6. **Disclosure:** flag synthetic/AI-generated content for the human's platform
   disclosure decision; do not decide it.

Report findings as: blocker / needs human verdict / clear. Do not edit
`metadata.json` provenance to make a gate pass.
