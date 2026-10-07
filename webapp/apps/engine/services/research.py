"""Thin adapter over scripts.research's brief/findings read-write model.

No business logic lives here - validation and the fail-open/fail-closed
split between "no brief" and "searched and came back thin" are
scripts.research's own (research.ResearchError). This module exists only so
DRF views import apps.engine rather than scripts.research directly.

Running research itself is not here: it is the existing "Research" pipeline
stage (apps.engine.tasks.STAGE_FUNCS), triggered the same generic way every
other stage is - a brief just gives that stage more to do.
"""
import scripts.research as research


def get_brief(video_id):
    return research.load_brief(video_id)


def save_brief(video_id, raw):
    """Validate and persist. Raises research.ResearchError on bad input."""
    return research.save_brief(video_id, raw)


def get_findings(video_id):
    return research.load_findings(video_id)


def get_directives(video_id):
    """What sourced findings asked this build to change, with their evidence."""
    return research.load_directives(video_id)


def get_influence(video_id):
    """What research actually changed about this production.

    Reads the record the storyboard stage wrote, and falls back to the
    directives alone when a project has been researched but not yet
    storyboarded - "research suggests this, nothing has applied it yet" is a
    real state and showing it is better than showing nothing.
    """
    import json

    import scripts.project as project

    path = project.project_dir(video_id) / "research_influence.json"
    if path.is_file():
        try:
            return json.loads(path.read_text())
        except (json.JSONDecodeError, OSError):
            pass
    directives = research.load_directives(video_id)
    if directives is None:
        return None
    return {
        "video_id": video_id,
        "recorded_utc": None,
        "researched": True,
        "findings_researched_utc": directives.get("findings_researched_utc"),
        "decisions": directives.get("decisions", []),
        "applied": {},
        "suggested_not_applied": sorted(directives.get("values", {})),
        "scene_count": None,
        "timeline_seconds": None,
    }
