"""DRF views for /api/v1/projects/.

Translation only - HTTP in, a call into apps.engine.services, JSON out. No
pipeline/gate logic here (architecture plan §8, "keep the web layer thin").
"""
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.engine.exceptions import ProjectNotFound
from apps.engine.services import projects as projects_service

from .serializers import ProjectSummarySerializer, StatusReportSerializer


class ProjectListView(APIView):
    """GET /api/v1/projects/ - live-computed, no cache (architecture plan §9)."""

    def get(self, request):
        summaries = projects_service.list_projects()
        return Response(ProjectSummarySerializer(summaries, many=True).data)


class ProjectDetailView(APIView):
    """GET /api/v1/projects/{id}/ - raw metadata.json."""

    def get(self, request, video_id):
        metadata = projects_service.get_metadata(video_id)
        if metadata is None:
            raise ProjectNotFound(f"no such project: {video_id}")
        return Response(metadata)


class ProjectStatusView(APIView):
    """GET /api/v1/projects/{id}/status/ - status_report(), not a re-derivation."""

    def get(self, request, video_id):
        report = projects_service.get_status(video_id)
        if report is None:
            raise ProjectNotFound(f"no such project: {video_id}")
        return Response(StatusReportSerializer(report).data)
