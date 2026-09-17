"""Maps scripts.project's domain exceptions onto DRF responses.

Kept here, not in individual views, so every app raises the same domain
exceptions and gets the same HTTP mapping - the web layer stays thin (no
view re-derives what a ProjectBusyError should mean over HTTP).
"""
from rest_framework.exceptions import APIException
from rest_framework import status

import scripts.project as project


class ProjectNotFound(APIException):
    status_code = status.HTTP_404_NOT_FOUND
    default_detail = "No such project."
    default_code = "not_found"


class ProjectBusy(APIException):
    status_code = status.HTTP_409_CONFLICT
    default_detail = "Another operation is already in progress for this project."
    default_code = "project_busy"


def translate(exc):
    """Re-raise a scripts.project exception as its DRF equivalent.

    Domain functions raise plain scripts.project exceptions - they must
    stay importable and usable from the CLI with zero knowledge of Django.
    This function is the one place that translation happens.
    """
    if isinstance(exc, project.ProjectBusyError):
        raise ProjectBusy(str(exc)) from exc
    if isinstance(exc, project.ProjectError):
        raise ProjectNotFound(str(exc)) from exc
    raise exc
