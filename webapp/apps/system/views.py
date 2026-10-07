"""DRF views for /api/v1/system/: what this host and its GPU worker can do.

Translation only (architecture plan §8). Readiness is
scripts.experiment.host_capabilities() - configuration plus the worker
registry's last heartbeat, no network probe - and the GPU job list/enqueue
are scripts.worker's own read model and queue entry point. Nothing here
decides whether work should start or transitions a job past QUEUED; POST
only calls scripts.worker.enqueue_manual(), the exact function the CLI's
``worker enqueue`` uses, so a dashboard render and a CLI one queue through
the same code. The one other write, requeue, is the operator's Retry on a
FAILED/CANCELLED job and goes through scripts.worker.requeue() the same way.
"""
from rest_framework import status
from rest_framework.exceptions import NotFound, ValidationError
from rest_framework.response import Response
from rest_framework.views import APIView

import scripts.worker as worker

from apps.engine.http import serve_file
from apps.engine.services import workers as workers_service

from .serializers import GpuJobEnqueueSerializer


class ReadinessView(APIView):
    """GET /api/v1/system/readiness/ - host capabilities incl. remote GPU."""

    def get(self, request):
        return Response(workers_service.readiness())


class GpuJobListView(APIView):
    """GET /api/v1/system/gpu-jobs/[?project=<id>] - remote GPU jobs, newest first.

    POST /api/v1/system/gpu-jobs/ - queue an ad-hoc render (the dashboard's
    Generate page). An offline worker is not an error: the job queues and
    waits, exactly like every other path into scripts.worker.enqueue().
    """

    def get(self, request):
        project_id = request.query_params.get("project") or None
        return Response(workers_service.list_jobs(project_id=project_id))

    def post(self, request):
        serializer = GpuJobEnqueueSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        try:
            job = workers_service.enqueue(
                data["prompt"], negative_prompt=data["negative_prompt"],
                width=data["width"], height=data["height"], count=data["count"],
                seed=data["seed"], model=data["model"])
        except worker.WorkerError as e:
            raise ValidationError({"detail": str(e)}) from e
        return Response(job, status=status.HTTP_201_CREATED)


class GpuJobAssetView(APIView):
    """GET /api/v1/system/gpu-jobs/{job_id}/assets/{index}/ - a rendered asset.

    Bounds-checked against the job's own recorded ``assets`` list
    (workers_service.job_asset -> worker.job_asset_path) - there is no path
    or filename parameter to traverse with.
    """

    def get(self, request, job_id, index):
        path = workers_service.job_asset(job_id, index)
        if path is None:
            raise NotFound(f"no such GPU job asset: {job_id}[{index}]")
        return serve_file(path, request)


class GpuJobRequeueView(APIView):
    """POST /api/v1/system/gpu-jobs/{job_id}/requeue/ - Retry a finished job.

    Only FAILED or CANCELLED jobs may be requeued; scripts.worker.requeue()
    refuses everything else with a 409, which is passed through unchanged
    so the UI can say "already running" rather than "something broke".
    """

    def post(self, request, job_id):
        try:
            job = workers_service.requeue(job_id)
        except worker.WorkerError as e:
            if e.status == 404:
                raise NotFound(str(e)) from e
            return Response({"detail": str(e)},
                            status=e.status if 400 <= e.status < 500 else 400)
        return Response(job)
