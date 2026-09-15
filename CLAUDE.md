# Content Machine — project instructions

A deterministic pipeline turning images + audio into a YouTube-ready package,
stopping at a human review gate. Read `README.md` for the full picture and
`AGENTS.md` for architecture and conventions.

## Graphify — project specifics

The query-first workflow is in the `## graphify` section below, which
`graphify claude install` owns and rewrites on upgrade — edit that section
only through the CLI. This section holds what is specific to this repo.

**There are two graphs. Do not confuse them.**

| Graph | Corpus | Location | Refresh |
|---|---|---|---|
| Code | this repo's source | `graphify-out/` | `graphify update .` |
| Knowledge | the `knowledge/` vault | `knowledge/graphify-out/` | `./content-machine knowledge refresh` |

- **Everything under any `graphify-out/` is gitignored and regenerable.**
  Nothing there is a source of truth. If a graph disagrees with its corpus,
  the corpus wins — re-run rather than trusting the graph.
- **Code graph.** Built 2026-08-30 over the full 101-file corpus: 804 nodes,
  1310 edges, 40 communities. Refresh code changes with `graphify update .`
  (AST-only, no API cost); docs and images need a full `/graphify` re-run.
  Its `obsidian/` directory is a derived export of *code*, not the knowledge
  vault, and is unrelated to `knowledge/`. Regenerate it with
  `graphify export obsidian` (also `export html|wiki|svg|graphml`); the
  exporters read `graphify-out/graph.json`, so they never re-extract.
- **`graphify update .` re-clusters, which can strand the community labels.**
  When the community set changes it falls back to naming each community after
  its hub node and prints a warning. Restore real names with
  `graphify label . --backend=claude-cli` — same free backend the knowledge
  graph uses — then re-run any export, since the labels are what the
  `_COMMUNITY_*.md` notes and the graph-view colouring are built from.
- **Knowledge graph.** `knowledge/` is an Obsidian vault and the canonical
  human-readable knowledge layer; the graph beneath it is derived. Flow is
  one-way, notes → graph. Refresh defaults to the `claude-cli` backend, so it
  adds no dependency and spends nothing through a metered API. Agents persist
  durable knowledge with `./content-machine knowledge capture` and read
  relationships with `./content-machine knowledge query`. See `AGENTS.md`
  → "Knowledge layer".
- A PreToolUse hook (`.claude/settings.json`) enforces query-first on Bash,
  Grep, Read, and Glob. It spawns a subprocess per matching tool call.
  Remove with `graphify claude uninstall`.

## Non-negotiables

These encode decisions that took real work to get right. Do not "simplify"
them away.

- **Never claim an asset is production-grade on a human's behalf.**
  `provenance.images.production_grade` must be set explicitly by a person.
  Code may only establish the negative (an abstract-only provider means
  `false`). This is what makes `READY_FOR_REVIEW` mean something.
- **The gate is fail-closed.** An absent claim blocks review. "Nobody
  decided" is not "no objection".
- **Trust artefacts over metadata.** Procedural plates are provenance-stamped
  at generation and flat fills are measured, so editing `metadata.json`
  cannot relabel a placeholder as a real asset. Keep it that way.
- **Never describe generated plates as photographs.** They are abstracts.
- **Licensing is verified, not assumed.** Audio whose rights cannot be
  established is refused, not silently used.
- **The paid provider stays last and off by default.** An unattended run must
  not be able to start spending money because something local hiccuped.
- **No hard-coded endpoints or secrets.** `COMFYUI_URL` unset means "the PC
  is off" — never default it to localhost.
- **The remote GPU worker is a worker, not an authority.** The VPS owns the
  queue and the truth; the PC dials out and is replaceable. A queued job
  waits when no capable worker is online - it must never fail or consume a
  retry attempt for a machine that is merely off. Worker liveness stays
  derived from heartbeat age, never stored. A worker may report progress but
  may not declare its own job succeeded, and an uploaded manifest is
  re-verified from the staged bytes before anything is published. Every
  transition is table-driven, fail-closed, and appended to the job's audit
  log. Nothing on this path may set `production_grade`.
- **The knowledge graph is a lens, never the operational database.**
  `experiments/concepts.json` and `projects/<id>/metadata.json` stay
  authoritative for concepts and per-video state. A note points at them; it
  does not restate them, and no code path may read run state out of
  `graph.json`. Capture durable knowledge only — never operational activity.

## Conventions

- Standard library only for the render/generation path; ffmpeg does the media
  work. Do not add an image or HTTP dependency to it.
- Providers are adapters: vendor specifics stay inside the provider class.
- New behaviour needs a test in `tests/`; external services are mocked or
  served by a local stand-in, never contacted for real.
- Run `./content-machine test` before declaring anything done (319 tests, ~85s).

## graphify

This project has a knowledge graph at graphify-out/ with god nodes, community structure, and cross-file relationships.

Rules:
- For codebase questions, first run `graphify query "<question>"` when graphify-out/graph.json exists. Use `graphify path "<A>" "<B>"` for relationships and `graphify explain "<concept>"` for focused concepts. These return a scoped subgraph, usually much smaller than GRAPH_REPORT.md or raw grep output.
- If graphify-out/wiki/index.md exists, use it for broad navigation instead of raw source browsing.
- Read graphify-out/GRAPH_REPORT.md only for broad architecture review or when query/path/explain do not surface enough context.
- After modifying code, run `graphify update .` to keep the graph current (AST-only, no API cost).
