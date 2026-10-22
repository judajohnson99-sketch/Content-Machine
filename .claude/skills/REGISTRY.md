# Skill registry (Content Machine)

Claude Code discovers only flat `.claude/skills/<name>/SKILL.md`, so tiers are
recorded here and in name prefixes, not in subfolders. Only each skill's
name + description is loaded up front; bodies and `references/` load on demand.
Vendored skills carry no symlinks and no machine-specific paths.

## Tiers

| Tier | Purpose | Skills |
|---|---|---|
| Shared | Skill management, reusable across projects (same copies live in the MAGENTA repo) | `find-skills`, `skill-creator`, `skill-discovery` |
| Shared / web | UI work (Content Machine's `frontend/` is React 19 + Vite + Vitest) | `frontend-design`, `webapp-testing`, `web-design-guidelines`, `vercel-react-best-practices`, `vercel-composition-patterns` |
| Content Machine | Instructional only; each points at existing `scripts/` code, never replaces it | `cm-creative-research`, `cm-visual-direction`, `cm-image-workflow`, `cm-video-qc`, `cm-audio-production`, `cm-media-selection`, `cm-publish-gate` |

## Provenance

| Skill | Source | Commit | Licence | Local patch |
|---|---|---|---|---|
| find-skills | vercel-labs/skills `skills/find-skills` | a80e4c9 | MIT | Added project-policy note; replaced global `-g -y` install step with vendor-and-review |
| skill-creator | anthropics/skills `skills/skill-creator` | dbd4588 | Apache-2.0 | none |
| frontend-design | anthropics/skills `skills/frontend-design` | dbd4588 | Apache-2.0 | none |
| webapp-testing | anthropics/skills `skills/webapp-testing` | dbd4588 | Apache-2.0 | none |
| vercel-react-best-practices | vercel-labs/agent-skills `skills/react-best-practices` | 063bee9 | MIT (SKILL.md frontmatter) | directory renamed to match `name:` |
| vercel-composition-patterns | vercel-labs/agent-skills `skills/composition-patterns` | 063bee9 | MIT (SKILL.md frontmatter) | directory renamed to match `name:` |
| web-design-guidelines | vercel-labs/agent-skills `skills/web-design-guidelines` + vercel-labs/web-interface-guidelines `command.md` (fetched 2026-10-22) | 063bee9 | none declared upstream | Upstream fetched `command.md` live on every run; replaced with a reviewed, pinned copy in `references/` |
| skill-discovery, cm-* | written here with skill-creator conventions | n/a | project | n/a |

Upstream scripts were read before install (skill-creator: `claude -p`
subprocess and a localhost viewer; webapp-testing: local subprocess launcher;
no remote fetches, credentials or install hooks).

## Evaluated, not installed

| Skill | Why not |
|---|---|
| anthropics `mcp-builder` | Neither project builds an MCP server. Add via `skill-discovery` when one is planned. |
| vercel `deploy-to-vercel`, `vercel-cli-with-tokens`, `vercel-optimize` | Deploy/credential-handling; this project deploys on its own VPS and forbids deploys from agent sessions. |
| Code review, debugging, testing, security, simplify | Built-in `/code-review`, `/security-review`, `/simplify` and the `engineering:*` plugin skills already cover these; no replacements. |
| Third-party audio/video/image-quality skills | None from a first-party or high-trust source were found worth vetting; custom `cm-*` skills cover the gap and defer to existing code. |

## Maintenance

Validate: `for d in .claude/skills/*/; do python3 .claude/skills/skill-creator/scripts/quick_validate.py "$d"; done`.
Refresh a vendored skill by diffing upstream against the copy, reviewing, and
updating the commit column. Add new skills through `skill-discovery`.
