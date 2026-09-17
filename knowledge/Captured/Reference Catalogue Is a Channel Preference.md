---
title: "Reference Catalogue Is a Channel Preference"
type: decision
confidence: VERIFIED
captured_at: 2026-09-17T20:42:37.189540+00:00
contributor: "content-machine-agent"
evidence: "experiments/concepts.json (kind=reference, visual_direction), scripts/creative.py direction_section"
tags:
  - knowledge/captured
  - knowledge/decision
---

# Reference Catalogue Is a Channel Preference

**The eight kind=reference concepts (moonlit glasshouse, midnight forest, rainy observatory, thunderstorm library, misty garden, celestial drift, bioluminescent dreamscape, dream architecture) encode the channel's current visual vocabulary - indigo/sapphire/violet/aqua/silver, water, glass, mist, volumetric light - as data on the concept, never as a pipeline rule.**

Each reference concept carries a visual_direction (palette, motifs, avoid, prompt_core, negative) that creative.generate_brief hands to the model as guidance, plus a 60 s preview_seconds so a look can be judged before a multi-hour render. Technical concepts (dark-screen noise beds) are tagged kind=technical and kept for regression, out of the default New Production view. Changing the channel's taste means editing the concept data, not the code; see [[Depicted Imagery Defers to the GPU Queue]] for how these depicted-only concepts reach the GPU.

## Evidence

- experiments/concepts.json (kind=reference, visual_direction), scripts/creative.py direction_section

## Related

- [[Depicted Imagery Defers to the GPU Queue]]

*Captured 2026-09-17T20:42:37.189540+00:00 by content-machine-agent. Confidence: VERIFIED.*
