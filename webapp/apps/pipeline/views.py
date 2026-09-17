"""DRF views for triggering pipeline stages and polling their PipelineRun.

Translation only (architecture plan §8): validate the body, call
apps.engine.services.pipeline, return whatever comes back. No stage logic,
no gate/idempotency re-derivation lives here.
"""
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

import scripts.project as project

from apps.engine.exceptions import ProjectNotFound, translate
from apps.engine.services import pipeline as pipeline_service

from .serializers import STAGE_SERIALIZERS, PipelineRunSerializer


class StageActionView(APIView):
    """POST /api/v1/projects/{video_id}/<stage>/ - trigger one pipeline stage.

    202 for a newly-queued run, 200 when `client_request_id` matches one
    already recorded (architecture plan §6 idempotency) - the browser
    cannot distinguish "queued" from "already handled" any other way.
    """
    stage = None

    def post(self, request, video_id):
        serializer_class = STAGE_SERIALIZERS[self.stage]
        serializer = serializer_class(data=request.data)
        serializer.is_valid(raise_exception=True)
        params = dict(serializer.validated_data)
        client_request_id = params.pop("client_request_id")

        try:
            run, created = pipeline_service.trigger_stage(
                video_id, self.stage, client_request_id, params)
        except project.ProjectError as e:
            translate(e)

        code = status.HTTP_202_ACCEPTED if created else status.HTTP_200_OK
        return Response(PipelineRunSerializer(run).data, status=code)


class ResearchActionView(StageActionView):
    stage = "research"


class CreativeActionView(StageActionView):
    stage = "creative"


class StoryboardActionView(StageActionView):
    stage = "storyboard"


class ScenesActionView(StageActionView):
    stage = "scenes"


class AudioActionView(StageActionView):
    stage = "audio"


class VisualsActionView(StageActionView):
    stage = "visuals"


class RunActionView(StageActionView):
    stage = "run"


class ProduceActionView(StageActionView):
    stage = "produce"


class PipelineRunDetailView(APIView):
    """GET /api/v1/projects/{video_id}/pipeline-runs/{run_id}/ - progress poll target."""

    def get(self, request, video_id, run_id):
        run = pipeline_service.get_run(video_id, run_id)
        if run is None:
            raise ProjectNotFound(f"no such pipeline run: {run_id}")
        return Response(PipelineRunSerializer(run).data)


class PipelineRunListView(APIView):
    """GET /api/v1/projects/{video_id}/pipeline-runs/ - the latest run per
    stage, for the pipeline view (architecture plan §13's
    `PipelineStageGraph`). One extra read endpoint, not a new authority:
    it's a live query over PipelineRun, the same table every other pipeline
    view already reads."""

    def get(self, request, video_id):
        latest = pipeline_service.latest_runs(video_id)
        data = {
            stage: PipelineRunSerializer(run).data if run is not None else None
            for stage, run in latest.items()
        }
        return Response(data)
