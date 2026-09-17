"""DRF views for /api/v1/system/: what this host and its GPU worker can do.

Translation only (architecture plan §8). Readiness is
scripts.experiment.host_capabilities() - configuration plus the worker
registry's last heartbeat, no network probe - and the GPU job list is
scripts.worker's own read model. Nothing here decides whether work should
start; it reports what the domain layer already derived.
"""
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.engine.services import workers as workers_service


class ReadinessView(APIView):
    """GET /api/v1/system/readiness/ - host capabilities incl. remote GPU."""

    def get(self, request):
        return Response(workers_service.readiness())


class GpuJobListView(APIView):
    """GET /api/v1/system/gpu-jobs/[?project=<id>] - remote GPU jobs, newest first."""

    def get(self, request):
        project_id = request.query_params.get("project") or None
        return Response(workers_service.list_jobs(project_id=project_id))
