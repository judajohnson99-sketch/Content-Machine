---
name: web-design-guidelines
description: Review UI code for Web Interface Guidelines compliance. Use when asked to "review my UI", "check accessibility", "audit design", "review UX", or "check my site against best practices".
metadata:
  author: vercel
  version: "1.0.0"
  argument-hint: <file-or-pattern>
---

# Web Interface Guidelines

Review files for compliance with Web Interface Guidelines.

## How It Works

1. Read the pinned guidelines in `references/web-interface-guidelines.md`
2. Read the specified files (or prompt user for files/pattern)
3. Check against all rules in that file
4. Output findings in the terse `file:line` format

## Guidelines Source

Pinned copy of `vercel-labs/web-interface-guidelines` `command.md`, reviewed and
committed (see `.claude/skills/REGISTRY.md`). Upstream originally fetched this
file live at each run; it is pinned here so remote text cannot change the
review rules unreviewed. To refresh, diff upstream against the pinned file and
commit the reviewed result.

## Usage

When a user provides a file or pattern argument:
1. Read `references/web-interface-guidelines.md`
2. Read the specified files
3. Apply all rules from that file
4. Output findings using the format specified in that file

If no files specified, ask the user which files to review.
