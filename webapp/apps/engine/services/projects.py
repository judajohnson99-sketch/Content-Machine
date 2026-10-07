"""Thin adapter over scripts.project's read functions.

No business logic lives here - every function is a direct call into the
shared domain layer (see the architecture plan, §1 "final shared-domain
boundary"). This module exists only so DRF views import `apps.engine`
rather than `scripts.*` directly, keeping "who calls the CLI's code" to one
file per concern.
"""
import json

import scripts.project as project


def list_projects(include_archived=False):
    """Active projects by default; archived ones only on request.

    The domain lists everything (an archived project is still a project);
    hiding it from the operator's active view is presentation, so the
    filter lives here rather than in scripts.project.
    """
    summaries = project.list_projects()
    if include_archived:
        return summaries
    return [s for s in summaries if not s.get("archived")]


def set_archived(video_id, archived, actor, reason=""):
    """Archive or restore - scripts.project.set_archived, nothing deleted."""
    return project.set_archived(video_id, archived, actor, reason=reason)


def get_status(video_id):
    """The status_report dict, or None if the project doesn't exist."""
    return project.status_report(video_id)


def get_metadata(video_id):
    """Raw metadata.json contents, or None if the project doesn't exist."""
    pdir = project.project_dir(video_id)
    meta_path = pdir / "metadata.json"
    if not meta_path.is_file():
        return None
    return json.loads(meta_path.read_text())


def delete(video_id, actor, reason=""):
    """Permanently remove a production. Raises project.ProjectError."""
    return project.delete_project(video_id, actor, reason=reason)
