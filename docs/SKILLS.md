# Skills (Content Machine)

Project skills live in `.claude/skills/<name>/SKILL.md` (flat; Claude Code does not nest). Only name + description are always in context; bodies load on use. Skills are **instructions** - the executable pipeline is `scripts/`, and skills point at it rather than duplicate it.

## Installed

**Shared / skill management**
- `find-skills`, `skill-creator` - discover and author skills. `skill-discovery` - gated workflow (check existing -> trusted sources -> review -> reuse -> create only if justified -> validate -> record). Use it only on a real capability gap.

**Development**
- `frontend-design`, `web-design-guidelines`, `vercel-react-best-practices`, `vercel-composition-patterns` - for `frontend/` (React + TS + Vite; Next.js-only rules in the Vercel skill do not apply). `webapp-testing` - Playwright checks of the dashboard.
- Not installed: `mcp-builder` (no MCP server work here). Code review, security review, simplify, debugging and testing strategy already exist as built-in/user-level skills, so no replacements were added.

**Content Machine (`cm-*`, custom)**
- `cm-creative-research` - niche/competitor analysis within the research gate.
- `cm-visual-review` - visual direction, scene consistency, generation workflow, perceptual review.
- `cm-audio-review` - mix/master review against listening contexts.
- `cm-video-review` - post-QC render review before READY_FOR_REVIEW.
- `cm-rights-verification` - media selection, QC and rights evidence before publishing.
All `cm-*` skills only recommend; `production_grade`, approval and publishing stay human acts.

## Provenance of third-party skills

Reviewed file-by-file before copying; none executed; no installer used (`npx skills add` was not run).

| Skill | Source @ commit | License | Local change |
|---|---|---|---|
| `find-skills` | vercel-labs/skills @ a80e4c9 | MIT | local-policy note added under the title (no global installs; see `skill-discovery`) |
| `skill-creator` | anthropics/skills @ dbd4588 | Apache-2.0 | none |
| `frontend-design` | anthropics/skills @ dbd4588 | Apache-2.0 | none |
| `webapp-testing` | anthropics/skills @ dbd4588 | Apache-2.0 | none |
| `web-design-guidelines` | vercel-labs/agent-skills @ 063bee9 + vercel-labs/web-interface-guidelines @ 434b7f9 | MIT | guidelines vendored in `references/guidelines.md` instead of fetched from a mutable URL at review time |
| `vercel-react-best-practices` | vercel-labs/agent-skills @ 063bee9 (`react-best-practices/`) | MIT | directory renamed to match its `name` |
| `vercel-composition-patterns` | vercel-labs/agent-skills @ 063bee9 (`composition-patterns/`) | MIT | directory renamed to match its `name` |

Skills' bundled scripts (e.g. `skill-creator/scripts/run_eval.py`) invoke `claude -p` and can incur model usage; do not run them without the owner's say-so.

## Adding or updating a skill
Follow `.claude/skills/skill-discovery/SKILL.md`; validate with `python3 .claude/skills/skill-creator/scripts/quick_validate.py .claude/skills/<name>`; add a row above.
