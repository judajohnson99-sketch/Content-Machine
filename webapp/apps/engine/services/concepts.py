"""Thin adapter over scripts.experiment's concept catalogue and scaffold.

No business logic lives here - direct calls into the shared domain layer
(architecture plan §1), the same functions `experiment.py scaffold` and
`experiment.py list` are built on. experiment.py imports scripts.project,
so this is the one web module that imports experiment rather than project.
"""
import scripts.experiment as experiment
import scripts.project as project

ScaffoldError = experiment.ScaffoldError


def concept_catalog():
    return experiment.concept_catalog()


def create_project(video_id, concept_id, duration=None):
    """Scaffold a project from a concept. Raises experiment.ScaffoldError.

    Returns the same summary shape GET /projects/ lists, so the client can
    drop the new project straight into its list without a second call.
    """
    experiment.scaffold_project(concept_id, video_id, duration=duration)
    return project.project_summary(video_id)


def create_from_goal(goal, video_id=None, minutes=None, excerpt_seconds=None):
    """Derive a whole production from a plain-language goal.

    One call into scripts.goal: it derives the concept, scaffolds the
    project and writes the research brief every production carries. Returns
    the same summary shape as create_project plus what was derived, so the
    dashboard can show the operator what it understood them to mean.
    """
    import scripts.goal as goal_mod

    result = goal_mod.derive_production(goal, video_id=video_id, minutes=minutes,
                                        excerpt_seconds=excerpt_seconds)
    return {
        "project": project.project_summary(result["video_id"]),
        "plan": result["plan"],
        "brief": result["brief"],
        "concept_id": result["concept"]["id"],
    }
