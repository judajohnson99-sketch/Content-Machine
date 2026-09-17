"""Thin adapter over scripts.project's record_review_decision.

No business logic lives here - a direct call into the shared domain layer,
the same function the CLI's cmd_approve/cmd_reject call (architecture plan
§1, §5). This module exists only so apps.review imports apps.engine rather
than scripts.* directly, matching every other app's boundary.
"""
import json

import scripts.project as project


def list_review_decisions(video_id):
    """metadata.json.review_history, or None if the project doesn't exist.

    Read live from the file, not a Postgres index - review decision history
    is explicitly not built as a cache in v1 (architecture plan §3).
    """
    pdir = project.project_dir(video_id)
    meta_path = pdir / "metadata.json"
    if not meta_path.is_file():
        return None
    metadata = json.loads(meta_path.read_text())
    return metadata.get("review_history", [])


def record_decision(video_id, reviewer, decision, notes, expected_digest):
    return project.record_review_decision(
        video_id, reviewer, decision, notes=notes, expected_digest=expected_digest)
