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
