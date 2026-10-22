---
name: skill-discovery
description: Decide whether a task needs a new or better skill and acquire one safely. Use when a task needs specialized expertise that no installed skill or built-in tool covers, or when asked to find, install, vet, create or improve a skill. Skip for routine work.
---

# Skill discovery (gated, repo-local)

Run only when there is a real capability gap. Trivial or well-covered tasks never trigger a search.

1. **Check what exists.** Installed skills (`.claude/skills/`, catalog in `docs/SKILLS.md`), built-in/user-level skills (`/code-review`, `/security-review`, `/simplify`, debug, testing), and the repo's own CLI/scripts. If one covers the task, use it and stop.
2. **Search trusted sources only.** Order: `anthropics/skills`, `vercel-labs/*` (official), then `find-skills` / skills.sh leaderboard for leads. A lead from an unknown author is a lead, not an install.
3. **Evaluate** (record in the PR/commit message): usefulness for this repo, license, install count/source reputation, compatibility with the repo's rules (`CLAUDE.md`), and security review below.
4. **Reuse** an existing skill when it fits. Prefer adopting as-is over editing; if edited, note the local change in `docs/SKILLS.md`.
5. **Create custom only if justified:** the gap is recurring, no trusted skill covers it, and it can be grounded in this repo's real commands and invariants. A skill is instructions; do not use one to re-implement something the code already does. Build it with `skill-creator`.
6. **Validate:** `python3 .claude/skills/skill-creator/scripts/quick_validate.py .claude/skills/<name>` (needs PyYAML), description concise (one or two sentences), SKILL.md short with detail in `references/`, no absolute or machine-specific paths, no symlinks, no name collisions.
7. **Preserve:** commit the skill and add one row to `docs/SKILLS.md` (source, commit, license, local changes).

## Security review before copying anything
Read every file. Reject or strip: installers (`npx skills add`, `curl | sh`), network fetches of instructions at runtime (vendor a pinned snapshot instead), credential or env access, obfuscated or base64 payloads, writes outside the repo, `-g`/global installs. Do not execute the skill's scripts to "test" them. Copy into `.claude/skills/<name>/` by hand; never auto-install.

## Boundaries
Never spend money, publish, or touch production credentials to evaluate a skill. Repo-specific governance, if any (`docs/SKILL_GOVERNANCE.md`), overrides this workflow.
