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

1. Read the vendored guidelines snapshot `references/guidelines.md`
2. Read the specified files (or prompt user for files/pattern)
3. Check against all rules in `references/guidelines.md`
4. Output findings in the terse `file:line` format

## Guidelines Source

Local modification: upstream fetches `command.md` from a mutable GitHub URL at
review time. This copy is pinned to `vercel-labs/web-interface-guidelines`
@ 434b7f9 and stored in `references/guidelines.md`, so no remote instructions
are loaded. Refresh deliberately by diffing the upstream file and committing.

## Usage

When a user provides a file or pattern argument:
1. Read `references/guidelines.md`
2. Read the specified files
3. Apply all rules from the fetched guidelines
4. Output findings using the format specified in the guidelines

If no files specified, ask the user which files to review.
