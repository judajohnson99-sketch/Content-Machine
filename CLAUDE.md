# Content Machine — project instructions

A deterministic pipeline turning images + audio into a YouTube-ready package,
stopping at a human review gate. `AGENTS.md` is the architecture map.
`README.md` is the user manual (900+ lines: open a section by heading, never
the whole file).

## Start of session: resume, don't rediscover

1. Read `.paul/STATE.md`. It is the only resume file: current milestone, what
   is done, what is blocked, the next task.
2. For substantive work run `./content-machine knowledge context "<task>"`
   once. It ranks the vault, both graphs, STATE.md and project metadata under
   a token budget; open only the one or two paths it points at.
3. No preflight beyond that: no `git status`/`log`/`diff`, no directory
   listings, no reading AGENTS.md, README.md or `GRAPH_REPORT.md` "to get
   oriented". Read them only when the task needs a specific section.

At every task boundary (done, blocked, before `/compact`, before a model
switch) update only the `## Current Position` and `## Session Continuity`
blocks of `.paul/STATE.md`: milestone, done, blockers, next. Plain sentences,
under 40 lines, never logs, diffs or test output. Feature handoffs such as
`WORKER_HANDOFF.md` are written once when a feature ships, not per session.

## Model routing

- Sonnet 5 is the project default (`.claude/settings.json`): coding, tests,
  refactors, docs, UI, and routine debugging.
- `/model claude-fable-5-1` only for architecture decisions, the worker state
  machine, cross-system reasoning, or a bug two Sonnet attempts did not
  crack. Switch back when that step ends.
- `ANTHROPIC_MODEL` in the environment overrides both settings files. Keep it
  unset when working here.

## Reading and searching

- Locate, then read the range: `graphify query "<question>"` for code
  structure, `./content-machine knowledge query "<q>"` for doctrine, niches
  and constraints, then `sed -n 'A,Bp'`. `cat` only files under ~100 lines.
- Never `grep -r` the repo root, `cat` the vault, or read
  `graphify-out/obsidian/` (800+ derived notes).
- Batch independent reads and edits into one call. Don't re-read a file you
  just edited.
- Generated and runtime trees are never context: `graphify-out/`, `jobs/`,
  `research/`, `output/`, `projects/*/output|logs`, `node_modules/`, `.venv/`,
  `.playwright*/`, `webapp/staticfiles/`, `frontend/dist/`, archives, logs.
- Bash, Read, Edit, Write and `graphify` cover nearly everything here. Do not
  load or call Serena onboarding, Headroom, Unabyss, Playwright, browser or
  other MCP/plugin tools unless the task itself needs that tool. Context7 is
  for an external library API question only.

## Testing and hygiene

- While working, run only the suite you touched:
  `python3 -m unittest tests.test_<x>`, `cd webapp && pytest apps/<app>`,
  `cd frontend && npx vitest run <file>`.
- Once at the end: `./content-machine test 2>&1 | tail -20` (420 tests,
  ~2 min); add `webapp` pytest and frontend `npx tsc -b` + `npx vitest run`
  only if you touched those trees. Never repeat a pass that no later change
  could have invalidated.
- Graphify refresh is milestone-based, not per edit. When a feature lands or
  a module's structure changes: `graphify update .` (AST-only, free); if
  vault notes changed: `./content-machine knowledge refresh`. If `update`
  strands community labels, `graphify label . --backend=claude-cli`. The
  CLI-owned `## graphify` section below says "after modifying code"; this
  rule supersedes it.
- Git hygiene once, at the end: `git status --short` and `git diff --stat`.
  Commit only when asked.

## Output discipline

- No narration of routine tool calls, no restated plans, no preflight
  summaries. Ask only when different readings would lead to materially
  different work.
- Completion report is exactly four parts: implemented, verification (what
  ran, result), blockers, next step.
- No new helper scripts, indexes, caches, state files or memory files for
  workflow purposes; STATE.md, the vault and the two graphs are sufficient.

## The two graphs

| Graph | Corpus | Location | Refresh |
|---|---|---|---|
| Code | this repo's source | `graphify-out/` | `graphify update .` |
| Knowledge | the `knowledge/` vault | `knowledge/graphify-out/` | `./content-machine knowledge refresh` |

- Everything under any `graphify-out/` is gitignored and regenerable. If a
  graph disagrees with its corpus, the corpus wins: re-run, don't trust it.
- `graphify update .` is AST-only; docs and images need a full `/graphify`
  re-run. The code graph's `obsidian/` export is derived from code and
  unrelated to `knowledge/`.
- `knowledge/` is an Obsidian vault and the canonical human-readable layer;
  its graph is derived one-way, notes → graph. Agents use
  `./content-machine knowledge capture` and `... query` (AGENTS.md →
  "Knowledge layer"). Search vault and graph before creating a note, reuse
  canonical notes, use `knowledge/templates/knowledge-note.md`, and express
  3–8 meaningful [[links]] in prose. Never hand-edit Graphify-owned output.
  Full linking rules: `.claude/rules/knowledge-linking.md` (auto-loaded).
- The PreToolUse hook in `.claude/settings.json` adds a query-first hint on
  Grep and Glob; it never blocks. Remove with `graphify claude uninstall`.

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
- **The vault is the long-term memory; Claude project memory is not.**
  Durable knowledge belongs in `knowledge/` via `./content-machine knowledge
  capture`, and is recalled with `... knowledge query` / `... context`. Claude's
  per-project memory dir holds one pointer file and nothing else — never a copy
  of a note, because Headroom owns that directory and syncs whatever sits in it
  into its own store. See `knowledge/Captured/Claude Project Memory Is Not the
  Knowledge Store.md`.

## Conventions

- Standard library only for the render/generation path; ffmpeg does the media
  work. Do not add an image or HTTP dependency to it.
- Providers are adapters: vendor specifics stay inside the provider class.
- New behaviour needs a test in `tests/`; external services are mocked or
  served by a local stand-in, never contacted for real.
- Pipeline LLM economy: research a subject once per video and cache it
  (`research/subjects/<id>.json`); one visual-direction document per video
  from which scene prompts derive; extra scene-level research only when
  factual accuracy requires it; stages hand each other compact structured
  artefacts (ids, beats, visual requirements), never the full script or
  conversation; batch similar calls (scene motifs are one call per video) and
  route routine calls to the cheapest capable model.

## graphify

This project has a knowledge graph at graphify-out/ with god nodes, community structure, and cross-file relationships.

Rules:
- For codebase questions, first run `graphify query "<question>"` when graphify-out/graph.json exists. Use `graphify path "<A>" "<B>"` for relationships and `graphify explain "<concept>"` for focused concepts. These return a scoped subgraph, usually much smaller than GRAPH_REPORT.md or raw grep output.
- If graphify-out/wiki/index.md exists, use it for broad navigation instead of raw source browsing.
- Read graphify-out/GRAPH_REPORT.md only for broad architecture review or when query/path/explain do not surface enough context.
- After modifying code, run `graphify update .` to keep the graph current (AST-only, no API cost).
