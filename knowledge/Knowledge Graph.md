---
title: "Knowledge Graph"
type: topic
tags:
  - knowledge/meta
---

# Knowledge Graph

The entry point to the relationship layer over this vault.

Graphify reads every note in `knowledge/` and derives a graph of how topics,
entities, research and decisions relate. The notes are the source of truth;
the graph is a lens. It is rebuilt from scratch on every refresh and is safe
to delete.

## Open the artefacts

Three artefacts are produced by every refresh, all inside
`knowledge/graphify-out/`:

| Artefact | What it is | How to open |
|---|---|---|
| [graph.html](graphify-out/graph.html) | interactive force-directed graph | click — Obsidian hands it to your browser |
| [GRAPH_REPORT.md](graphify-out/GRAPH_REPORT.md) | human-readable analysis: hubs, communities, orphans | click — opens as a note |
| [graph.json](graphify-out/graph.json) | machine-readable graph, for agents and `graphify query` | for tooling, not reading |

> [!note] Why links and not an embed
> Obsidian does not render a local HTML file inside a note — an `<iframe>`
> pointing at a vault file is blocked. A relative link is the mechanism that
> actually works: Obsidian opens `graph.html` in the system browser. From a
> terminal, `./content-machine knowledge open` prints and opens the same file.

## Refresh it

```bash
./content-machine knowledge status     # notes, graph size, staleness
./content-machine knowledge refresh    # rebuild from the current vault
./content-machine knowledge open       # open the interactive graph
```

`refresh` runs Graphify's extraction and clustering over `knowledge/` using
the `claude-cli` backend — the local Claude Code session, so no extra Python
package and no paid API call. `status` reports STALE when any note is newer
than the graph.

## Query it

```bash
./content-machine knowledge query "what blocks the kids_sleep niche?"
```

That is a thin wrapper over `graphify query --graph knowledge/graphify-out/graph.json`.
`graphify path "A" "B"` and `graphify explain "X"` accept the same `--graph`
flag and work against this graph too.

## Capture into it

Agents persist new knowledge with `./content-machine knowledge capture`, which
writes into [[Captured]] with Graphify-native frontmatter (`type`, `confidence`,
`captured_at`, `contributor`, `source_url`). The next refresh folds those notes
into the graph like any other note. See the vault [[README]] for what qualifies.

## Two graphs, don't confuse them

| Graph | Corpus | Location | Question it answers |
|---|---|---|---|
| **Code graph** | this repo's source | `graphify-out/` at the repo root | how does the pipeline work? |
| **Knowledge graph** | this vault | `knowledge/graphify-out/` | how do topics, research and decisions relate? |

## Map

- Doctrine: [[Publication Gate]] · [[Provenance and Production-Grade Claims]] · [[Rights and Licensing]] · [[Autonomy Boundaries]]
- Niches: [[Adult Sleep]] · [[Kids Sleep]] · [[Bedtime Stories]] · [[Focus Ambience]] · [[Non-Sleep Alternatives]]
- Research: [[YouTube Inauthentic Content Policy]] · [[YouTube Synthetic Content Disclosure]] · [[Open Research Questions]]
- Constraints: [[Dark Visual Luminance Floor]] · [[Render Throughput]] · [[Local Narration Capability]]
- Log: [[Experiment Log]]
