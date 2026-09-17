---
title: "Claude Project Memory Is Not the Knowledge Store"
type: decision
confidence: VERIFIED
captured_at: 2026-09-15T13:49:42.091524+00:00
contributor: "claude-code"
evidence: "audited 2026-09-15: /root/.claude/projects/-root-projects-content-machine/memory/ held one durable note plus a headroom-exported shell-failure fragment; headroom memory list showed the same two rows in the gitignored project-local store .headroom/memory.db; the export is driven by the --memory and --learn flags on 'headroom wrap claude' in /root/.bashrc (cmclaude)"
tags:
  - knowledge/captured
  - knowledge/decision
---

# Claude Project Memory Is Not the Knowledge Store

**Durable project knowledge lives in the Obsidian vault; Claude's project memory dir holds only a pointer to it, because Headroom owns that directory's contents.**

Claude Code keeps an auto-memory directory at
`~/.claude/projects/-root-projects-content-machine/memory/`. It looks like a
place to accumulate project knowledge. It is not one, for two reasons.

**It is not ours to own.** Headroom runs as the context proxy on this host and
treats that directory as an export surface. Its memory store is the source of
truth: the gitignored `.headroom/memory.db` inside this repo, which takes
precedence over the global store when it exists (`~/.headroom/ccr_store.db` is
the compression store, not this one). `MEMORY.md` and every sibling
`headroom_*.md` are regenerated from it. The sync is *bidirectional*:
any `.md` file dropped in that directory (everything except the `MEMORY.md`
index) is imported into the store on the next `headroom wrap claude --memory`,
then re-exported. Hand-editing a file there does not stick, and hand-deleting
one restores within about a minute. Two mechanisms write to it:

| Flag on `headroom wrap claude` | What it writes |
|---|---|
| `--memory` | bidirectional DB ↔ memory-dir sync; `headroom_*.md` files and the `## Headroom Shared Memory` index section |
| `--learn` | the live traffic learner's `<!-- headroom:learn:start -->` block — auto-captured shell-failure and prompt fragments |

**It is the wrong shape.** The learner captures activity — a command that
failed, a path that got retried. That is the opposite of what
`knowledge/README.md` admits to the vault: durable, cross-cutting knowledge
that stays true after a run ends. Two stores of project knowledge also means
two stores that can disagree, with no rule for which wins.

## Consequence

- Durable knowledge goes to `knowledge/` via `./content-machine knowledge
  capture`, and is read back with `./content-machine knowledge query` or
  `... context`. Operational state still belongs to
  `experiments/concepts.json` and `projects/<id>/metadata.json`, unchanged.
- Claude's memory directory holds `MEMORY.md` and nothing else. `MEMORY.md`
  says where the real store is and stops there. An empty directory plus an
  empty Headroom store makes the sync a no-op, so there is nothing to fight.
- Never copy a vault note into Claude project memory. A duplicate there is not
  a cache, it is a second truth that Headroom will then import into its store.
- The enforcement is store-level and config-level, never a fight with the
  generated file: delete the entries from `.headroom/memory.db`
  (`headroom memory delete <id> --force`, after `headroom memory export`), and
  drop
  `--memory` and `--learn` from the `cmclaude` launcher in `/root/.bashrc`.
  Compression, savings and the MCP retrieve tool are unaffected by either
  flag — Headroom stays, it just stops writing memories.
- The traffic learner routes a pattern by the paths *inside* it, not by the
  proxy's working directory. A proxy started elsewhere with `--learn` can
  therefore still write content-machine patterns here; the flags have to be
  off wherever the proxy is started.

## Evidence

- audited 2026-09-15: /root/.claude/projects/-root-projects-content-machine/memory/ held one durable note plus a headroom-exported shell-failure fragment; headroom memory list showed the same two rows in the gitignored project-local store .headroom/memory.db; the export is driven by the --memory and --learn flags on 'headroom wrap claude' in /root/.bashrc (cmclaude)

## Related

- [[Knowledge Graph]]
- [[Graphify Backend Availability On This Host]]

*Captured 2026-09-15T13:49:42.091524+00:00 by claude-code. Confidence: VERIFIED.*
