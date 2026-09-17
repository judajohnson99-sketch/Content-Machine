"""Thin adapter over scripts.project's asset read functions.

No business logic lives here - direct calls into the shared domain layer
(architecture plan §1). In particular the "which files may be served" rule
is project_file_path()'s alone; this module never re-checks or relaxes it.
"""
import scripts.project as project


def get_assets(video_id):
    """The project_assets() read model, or None if the project doesn't exist."""
    return project.project_assets(video_id)


def resolve_file(video_id, relative):
    """Resolved Path of one servable asset. Raises project.ProjectError."""
    return project.project_file_path(video_id, relative)
