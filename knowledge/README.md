---
title: "Knowledge Vault"
type: topic
tags:
  - knowledge/meta
---

# Knowledge Vault

This folder is an Obsidian vault and it is the **canonical human-readable
knowledge layer** for Content Machine. Open `knowledge/` as a vault; start at
[[Knowledge Graph]].

## What belongs here

Durable, cross-cutting knowledge — the things that stay true after a run ends:

- **Doctrine** — rules that bind future work, and why they exist.
- **Niches** — content territories, what each one is betting on.
- **Research** — verified findings from outside sources, with citations.
- **Constraints** — limits measured on our own hardware.
- **Log** — what we actually ran and what it taught us.
- **Captured** — artefacts written by agents via `knowledge capture`.

## What does not belong here

Operational state. These files stay authoritative and are **not** duplicated
into notes:

| Operational record | Owns |
|---|---|
| `experiments/concepts.json` | concept definitions, scores, kill/continue evidence |
| `projects/<id>/metadata.json` | per-video editorial record, provenance, status |
| `projects/<id>/logs/` | run logs |

A note *points at* those files. It does not restate them. Render logs, QC
verdicts, job status and asset counts are activity, not knowledge.

## The derived layer

`knowledge/graphify-out/` holds the Graphify graph built **from** these notes.
It is gitignored and regenerable. Nothing here may depend on it. If the graph
and the notes disagree, the notes win — re-run the refresh.
