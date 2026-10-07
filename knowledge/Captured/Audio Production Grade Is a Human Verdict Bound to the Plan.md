---
title: "Audio Production Grade Is a Human Verdict Bound to the Plan"
type: decision
confidence: VERIFIED
captured_at: 2026-09-19T15:32:21.284147+00:00
contributor: "Claude Opus 5"
evidence: "scripts/creative.py route_audio direction record; scripts/project.py record_audio_grade + gate_blockers audio branch + project_assets.audio_provenance; webapp/apps/review AudioGradeView; frontend/src/components/ReviewPanel.tsx AudioGradeControl; tests/test_project.py AudioGradeTest; webapp/apps/review/test_views.py TestAudioGradeView; frontend ReviewPanel.test.tsx"
tags:
  - knowledge/captured
  - knowledge/decision
  - audio
  - gate
---

# Audio Production Grade Is a Human Verdict Bound to the Plan

**Whether a track is pleasant enough to publish is a human claim: provenance.audio.production_grade is set only by a named person, is required whenever the chosen source cannot be production-grade, and is void once the audio plan changes.**

**A technically valid mix is not listenable music. Where the chosen audio source cannot establish its own quality, review stays closed until a named human records a verdict — and that verdict dies with the plan it was given for.**

[[Technical Validity Is Not Production Quality]] left this open: whether pleasant audio deserves its own human-confirmed claim beside the images one. It does, but not everywhere — demanding a ceremonial click for every video teaches people to click through it.

`creative.route_audio` decides *what kind* of audio the concept needs, records every source it considered with availability, rights and cost, and marks whether the chosen one is `production_grade_capable`. Two cases follow:

- The source is the product — a brown-noise bed for a sleep video is the deliverable, not an approximation of one. No verdict is asked for.
- The source cannot judge itself — generative ambient music is technically valid by construction and says nothing about whether anyone would leave it on for four hours. `gate_blockers` holds review until `record_audio_grade` stores a verdict.

Only a person may set it, matching the images rule: code may establish the negative (`production_grade_capable: false`) and nothing more. The verdict stores the `plan_digest` of the exact composition it was given for, so re-routing or re-rendering the audio voids it rather than silently carrying an old "yes" onto a different track.

The claim is reachable where reviews actually happen, not only from the CLI: `POST /projects/{id}/audio-grade/` takes the reviewer from the session, and the review panel shows the routing record — kind, reasoning, and every source considered — beside a two-step confirm. A rejection needs no confirmation; only an approval does.

## Evidence

- scripts/creative.py route_audio direction record; scripts/project.py record_audio_grade + gate_blockers audio branch + project_assets.audio_provenance; webapp/apps/review AudioGradeView; frontend/src/components/ReviewPanel.tsx AudioGradeControl; tests/test_project.py AudioGradeTest; webapp/apps/review/test_views.py TestAudioGradeView; frontend ReviewPanel.test.tsx

## Related

- [[Technical Validity Is Not Production Quality]]
- [[Human Review Decision Design]]
- [[Music-Branded Ambient Concepts Lead With Pad, Not Raw Noise]]

*Captured 2026-09-19T15:32:21.284147+00:00 by Claude Opus 5. Confidence: VERIFIED.*
