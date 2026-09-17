"""Thin adapter over scripts.project's read functions.

No business logic lives here - every function is a direct call into the
shared domain layer (see the architecture plan, §1 "final shared-domain
boundary"). This module exists only so DRF views import `apps.engine`
rather than `scripts.*` directly, keeping "who calls the CLI's code" to one
file per concern.
"""
import json

import scripts.project as project


def list_projects():
    return project.list_projects()


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
