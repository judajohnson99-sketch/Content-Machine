---
title: "Captured"
type: topic
tags:
  - knowledge/meta
---

# Captured

Notes written here by agents, via:

```bash
./content-machine knowledge capture \
  --type finding --title "..." --summary "..." \
  --evidence "projects/<id>/metadata.json" \
  --link "Adult Sleep" --confidence VERIFIED
```

The body is read from stdin. Frontmatter follows Graphify's ingest convention
(`type`, `confidence`, `captured_at`, `contributor`, `source_url`), so a
refresh carries that provenance onto the graph node.

The gate is fail-closed and narrow: a capture is refused unless it declares one
of the accepted types — decision, finding, insight, constraint, entity, topic —
cites its evidence, and carries enough substance to be worth re-reading.
Operational activity is not knowledge: render logs, QC verdicts, job status and
asset counts belong in `projects/<id>/metadata.json`.

See [[Knowledge Graph]] and the vault [[README]].
