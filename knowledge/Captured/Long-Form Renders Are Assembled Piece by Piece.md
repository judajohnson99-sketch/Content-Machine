---
title: "Long-Form Renders Are Assembled Piece by Piece"
type: decision
confidence: VERIFIED
captured_at: 2026-10-03T03:18:55.753172+00:00
contributor: "content-machine-agent"
evidence: "scripts/render.py::render_scenes_piecewise; tests/test_longform_production.py::PiecewiseRenderTest; a 120-scene 30-minute 1080p render on this host"
tags:
  - knowledge/captured
  - knowledge/decision
---

# Long-Form Renders Are Assembled Piece by Piece

**Above render.MAX_IMAGE_SLOTS scenes the renderer builds each scene and crossfade as its own clip and concatenates them without re-encoding, so scene count stops being bounded by memory and storyboarded scenes become the default for every format.**

A video's scene count is no longer bounded by memory. Above
`render.MAX_IMAGE_SLOTS` scenes the renderer switches to
`render_scenes_piecewise`: each scene is rendered once on its own and cut
into the overlap it hands backwards, its own body, and the overlap it hands
forwards; each crossfade is rendered once from the two overlaps that meet
there; the pieces are concatenated with `-c copy` and the audio bed muxed
over them. Two ffmpeg inputs are open at a time however long the video is,
and the timeline is encoded exactly once.

The arithmetic is the one [[Scene Prompts Compiled From Structured
Direction, Not Written]] already assumes: every crossfade overlaps two
scenes, so sum(bodies) + sum(crossfades) is the finished runtime. Every
boundary is rounded to a frame *on the finished timeline*, not within its
own scene - rounding each scene independently is fine for a dozen scenes and
produces seconds of systematic drift across three hundred, which QC would
rightly call a broken render.

This is what made storyboarded scenes the default for every format rather
than only narrated ones. Cycling a handful of plates on one global Ken Burns
move is a slideshow, and the only reason it was the default for long ambient
was that a scene used to be a concurrent ffmpeg input. With that gone, a
30-minute video is 120 distinct shots with their own moves and dissolves.
Shot length, absent research saying otherwise, now scales with runtime
(~one shot per two minutes, bounded 6-30s) instead of a flat 6 seconds.

Relates to [[Render Throughput]]: the piecewise path trades a single long
ffmpeg invocation for ~2N short ones, so process startup becomes a real
cost at high scene counts, while peak memory stops growing with the video.

## Evidence

- scripts/render.py::render_scenes_piecewise; tests/test_longform_production.py::PiecewiseRenderTest; a 120-scene 30-minute 1080p render on this host

## Related

- [[Render Throughput]]
- [[Adult Sleep]]
- [[Scene Prompts Compiled From Structured Direction, Not Written]]

*Captured 2026-10-03T03:18:55.753172+00:00 by content-machine-agent. Confidence: VERIFIED.*
