---
name: web-design-guidelines
description: Review UI code against the Web Interface Guidelines (accessibility, focus, forms, animation, performance). Use when asked to review a UI, audit accessibility or UX, or check a page against best practices.
---

# Web interface review

Review the given files (ask for a file or glob if none) against the rules in
`references/web-interface-guidelines.md` and report terse `file:line` findings,
following the output format stated in that file.

The rules file is a pinned copy of `command.md` from
`vercel-labs/web-interface-guidelines` (MIT, Copyright (c) 2025 Vercel Labs;
licence text in `references/LICENSE-web-interface-guidelines.txt`). It is never
fetched at run time; refresh it only by reviewing a diff against upstream and
committing the result. Provenance: `.claude/skills/REGISTRY.md`.
