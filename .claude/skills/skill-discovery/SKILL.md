---
name: skill-discovery
description: Decide whether a task needs a skill that is not installed, then vet, vendor or create it. Use when work needs specialist expertise no listed skill covers, or the user asks to find, add, or create a skill. Skip for trivial tasks.
---

# Skill discovery

Gate before searching: only run this when the task is non-trivial, repeatable,
and no skill or built-in already covers it. Never search on routine edits.

1. **Check what exists.** Read the skill list already in context, then
   `.claude/skills/REGISTRY.md`. Prefer a built-in or installed skill, and an
   existing code path (`./content-machine ...`, `graphify query`, the vault).
2. **Search trusted sources only** (use `find-skills`): `anthropics/skills`,
   `vercel-labs/*`, other first-party vendor repos. Cap at two searches. A skill
   from an unknown author, under ~100 installs, or from a repo under ~100 stars
   is not a candidate.
3. **Vet before install.** Clone into a scratch directory outside the repo and
   read every file. Reject a skill that: runs install or network scripts,
   fetches instructions at runtime, reads credentials or env files, writes
   outside its own directory, or duplicates a built-in. Run no script from it.
4. **Reuse over create.** Vendor a vetted skill verbatim into
   `.claude/skills/<name>/` (no symlinks, no absolute paths, no `-g`). Patch only
   what policy requires and say so in the registry.
5. **Create only when justified:** the capability is missing, recurs, and is
   specific to this project's architecture. Use `skill-creator`. Keep the
   description to one or two sentences, SKILL.md under ~100 lines, detail in
   `references/`, and ground it in real files. An instructional skill must
   never restate or replace executable pipeline code.
6. **Validate:** `python3 .claude/skills/skill-creator/scripts/quick_validate.py .claude/skills/<name>`; paths resolve; no duplicate name or trigger overlap.
7. **Preserve:** add a row to `.claude/skills/REGISTRY.md` (source, commit,
   licence, tier, any patch). Commit only when asked.

Hard limits: no paid API calls, no credentials, no deploys, no autonomous
installs from the network.
