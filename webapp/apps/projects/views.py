"""DRF views for /api/v1/projects/.

Translation only - HTTP in, a call into apps.engine.services, JSON (or a
file) out. No pipeline/gate logic here (architecture plan §8, "keep the web
layer thin").
"""
from rest_framework import status
from rest_framework.exceptions import NotFound, ValidationError
from rest_framework.response import Response
from rest_framework.views import APIView

import scripts.project as project
import scripts.research as research

from apps.engine.exceptions import ProjectNotFound
from apps.engine.http import serve_file
from apps.engine.services import assets as assets_service
from apps.engine.services import concepts as concepts_service
from apps.engine.services import projects as projects_service
from apps.engine.services import research as research_service
from apps.engine.services import workers as workers_service

from .serializers import (
    ProductionGoalSerializer, ProjectCreateSerializer, ProjectDeleteSerializer,
    ProjectSummarySerializer, ResearchBriefSerializer,
    ProjectArchiveSerializer,
    StatusReportSerializer,
)


class ProjectListView(APIView):
    """GET /api/v1/projects/ - live-computed, no cache (architecture plan §9).

    POST /api/v1/projects/ - scaffold a new project from a concept, the
    exact operation `experiment.py scaffold` performs. 201 with the new
    project's summary; 409 if the id is taken; 400 for an unknown concept
    or malformed input. Nothing is produced yet - that is a separate,
    idempotent POST .../produce/, so creating and starting stay two
    explicit acts with their own failure modes.
    """

    def get(self, request):
        include_archived = str(request.query_params.get("include_archived", "")).lower() in (
            "1", "true", "yes")
        summaries = projects_service.list_projects(include_archived=include_archived)
        return Response(ProjectSummarySerializer(summaries, many=True).data)

    def post(self, request):
        serializer = ProjectCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        try:
            summary = concepts_service.create_project(
                data["video_id"], data["concept_id"], duration=data["duration"])
        except concepts_service.ScaffoldError as e:
            if e.code == "exists":
                return Response({"detail": str(e), "code": e.code},
                                status=status.HTTP_409_CONFLICT)
            raise ValidationError({"detail": str(e), "code": e.code}) from e
        return Response(ProjectSummarySerializer(summary).data, status=status.HTTP_201_CREATED)


class ProductionFromGoalView(APIView):
    """POST /api/v1/projects/from-goal/ - a production from a sentence.

    Derives a concept, scaffolds the project and writes its research brief,
    all through scripts.goal. Synchronous on purpose: it is one model call
    and the operator is looking at the result, so a job id they would then
    have to poll would be worse than a few seconds of waiting. Nothing is
    produced here - starting the run stays the separate, idempotent POST
    .../produce/ every other creation path uses.
    """

    def post(self, request):
        serializer = ProductionGoalSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        try:
            result = concepts_service.create_from_goal(
                data["goal"], video_id=data["video_id"], minutes=data["minutes"],
                excerpt_seconds=data["excerpt_seconds"])
        except concepts_service.ScaffoldError as e:
            if e.code == "exists":
                return Response({"detail": str(e), "code": e.code},
                                status=status.HTTP_409_CONFLICT)
            raise ValidationError({"detail": str(e), "code": e.code}) from e
        except Exception as e:  # noqa: BLE001 - derivation failure is a 400, not a 500
            raise ValidationError({
                "detail": f"could not derive a production from that goal: {e}",
                "code": "derivation_failed"}) from e
        result["project"] = ProjectSummarySerializer(result["project"]).data
        return Response(result, status=status.HTTP_201_CREATED)


class ProjectArchiveView(APIView):
    """POST /api/v1/projects/{id}/archive/ - {"archived": bool, "reason"?}.

    Hides development residue from the active views (or restores it). The
    actor is request.user, never a default, and nothing on disk is removed:
    permanent deletion stays a deliberate act outside this API.
    """

    def post(self, request, video_id):
        serializer = ProjectArchiveSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        actor = (getattr(request.user, "email", "") or request.user.get_username())
        try:
            summary = projects_service.set_archived(
                video_id, data["archived"], actor, reason=data["reason"])
        except project.ProjectError as e:
            if "no such project" in str(e):
                raise ProjectNotFound(str(e)) from e
            raise ValidationError({"detail": str(e)}) from e
        return Response(ProjectSummarySerializer(summary).data)


class ProjectDetailView(APIView):
    """GET /api/v1/projects/{id}/ - raw metadata.json.

    DELETE - permanently remove the production. The body must echo the id
    being deleted; scripts.project.delete_project decides what that removes
    and refuses a published project. There is no undo, which is why this is
    a different verb on a different body rather than a flag on archive.
    """

    def get(self, request, video_id):
        metadata = projects_service.get_metadata(video_id)
        if metadata is None:
            raise ProjectNotFound(f"no such project: {video_id}")
        return Response(metadata)

    def delete(self, request, video_id):
        serializer = ProjectDeleteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        if data["confirm_video_id"] != video_id:
            raise ValidationError({
                "detail": "confirm_video_id must match the project being deleted",
                "code": "confirmation_mismatch"})
        actor = (getattr(request.user, "email", "") or request.user.get_username())
        try:
            result = projects_service.delete(video_id, actor, reason=data["reason"])
        except project.ProjectError as e:
            if "no such project" in str(e):
                raise ProjectNotFound(str(e)) from e
            raise ValidationError({"detail": str(e)}) from e
        return Response(result)


class ProjectStatusView(APIView):
    """GET /api/v1/projects/{id}/status/ - status_report(), not a re-derivation."""

    def get(self, request, video_id):
        report = projects_service.get_status(video_id)
        if report is None:
            raise ProjectNotFound(f"no such project: {video_id}")
        return Response(StatusReportSerializer(report).data)


class ProjectAssetsView(APIView):
    """GET /api/v1/projects/{id}/assets/ - project_assets() verbatim.

    Like the detail view, the domain read model is the contract: this view
    adds no fields and derives nothing.
    """

    def get(self, request, video_id):
        manifest = assets_service.get_assets(video_id)
        if manifest is None:
            raise ProjectNotFound(f"no such project: {video_id}")
        return Response(manifest)


class ProjectGpuJobsView(APIView):
    """GET /api/v1/projects/{id}/gpu-jobs/ - this project's remote GPU jobs.

    scripts.worker.jobs_for_project() verbatim: the queue's own view of
    what is waiting, rendering, done or failed on the PC. Read-only; the
    job state machine is never driven from here.
    """

    def get(self, request, video_id):
        if projects_service.get_metadata(video_id) is None:
            raise ProjectNotFound(f"no such project: {video_id}")
        return Response(workers_service.project_jobs(video_id))


class ProjectFileView(APIView):
    """GET /api/v1/projects/{id}/files/{path} - one servable asset.

    Which paths are servable is decided entirely by
    scripts.project.project_file_path() - the same rule for every adapter.
    Supports a single byte range so the browser's <video> element can seek
    a long render without downloading all of it first; anything else about
    the request is refused with 416 rather than guessed at.
    """

    def get(self, request, video_id, relative):
        try:
            path = assets_service.resolve_file(video_id, relative)
        except project.ProjectError as e:
            # Every refusal - traversal, wrong directory, missing file - is
            # a 404, so a probe learns nothing about what exists.
            raise ProjectNotFound(str(e)) from e
        return serve_file(path, request)


class ProjectResearchInfluenceView(APIView):
    """GET /api/v1/projects/{id}/research/influence/ - what research changed.

    Three separate things the dashboard shows side by side: every directive
    sourced findings produced (with the finding ids and URLs behind it),
    what the build actually applied, and what it did not. 404 until research
    has run, because "no influence yet" and "research found nothing to
    change" are different states and must not look the same.
    """

    def get(self, request, video_id):
        if projects_service.get_metadata(video_id) is None:
            raise ProjectNotFound(f"no such project: {video_id}")
        influence = research_service.get_influence(video_id)
        if influence is None:
            raise NotFound(f"no research influence recorded for {video_id}")
        return Response(influence)


class ProjectResearchBriefView(APIView):
    """GET /api/v1/projects/{id}/research/brief/ - the project's research
    brief, or 404 if none has been written yet.

    PUT - validate and save. scripts.research.save_brief() re-validates
    and is the only writer; this view refuses obviously malformed input
    first so a bad request never reaches the domain layer.
    """

    def get(self, request, video_id):
        if projects_service.get_metadata(video_id) is None:
            raise ProjectNotFound(f"no such project: {video_id}")
        brief = research_service.get_brief(video_id)
        if brief is None:
            raise NotFound(f"no research brief for {video_id}")
        return Response(brief)

    def put(self, request, video_id):
        if projects_service.get_metadata(video_id) is None:
            raise ProjectNotFound(f"no such project: {video_id}")
        serializer = ResearchBriefSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            brief = research_service.save_brief(video_id, serializer.validated_data)
        except research.ResearchError as e:
            raise ValidationError({"detail": str(e)}) from e
        return Response(brief)


class ProjectResearchFindingsView(APIView):
    """GET /api/v1/projects/{id}/research/findings/ - cached brief-driven
    findings, or 404 if research has not run (or produced nothing) yet.

    Running research itself is not a separate endpoint: it is the existing
    "Research" pipeline stage (apps.engine.tasks.STAGE_FUNCS["research"] ->
    scripts.project.run_research), triggered the same way every other stage
    is - POST /api/v1/projects/{id}/pipeline/research/run/. A brief simply
    gives that stage more to do.
    """

    def get(self, request, video_id):
        if projects_service.get_metadata(video_id) is None:
            raise ProjectNotFound(f"no such project: {video_id}")
        findings = research_service.get_findings(video_id)
        if findings is None:
            raise NotFound(f"no research findings for {video_id}")
        return Response(findings)
