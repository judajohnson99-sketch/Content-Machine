"""Request/response shapes for the pipeline-run API.

Each `<Stage>RequestSerializer`'s fields mirror its `run_*` domain
function's parameters exactly (see scripts/project.py) - no transformation,
no derived fields. `client_request_id` is the one field every stage shares
(architecture plan §6) and is stripped out by the view before the rest of
`validated_data` is passed through as **params.
"""
from rest_framework import serializers

from .models import PipelineRun


class PipelineRunSerializer(serializers.ModelSerializer):
    class Meta:
        model = PipelineRun
        fields = [
            "id", "video_id", "stage", "client_request_id", "celery_task_id",
            "status", "params", "exit_code", "message", "data", "log_tail",
            "created_at", "started_at", "finished_at",
        ]
        read_only_fields = fields


class StageRequestSerializer(serializers.Serializer):
    client_request_id = serializers.UUIDField()


class ResearchRequestSerializer(StageRequestSerializer):
    concept_id = serializers.CharField(required=False, allow_null=True, default=None)
    force = serializers.BooleanField(default=False)


class CreativeRequestSerializer(StageRequestSerializer):
    force = serializers.BooleanField(default=False)


class StoryboardRequestSerializer(StageRequestSerializer):
    niche = serializers.CharField(required=False, allow_null=True, default=None)
    scene_count = serializers.IntegerField(required=False, allow_null=True, default=None)
    source_width = serializers.IntegerField(required=False, allow_null=True, default=None)
    source_height = serializers.IntegerField(required=False, allow_null=True, default=None)
    force = serializers.BooleanField(default=False)


class ScenesRequestSerializer(StageRequestSerializer):
    force = serializers.BooleanField(default=False)
    depicted = serializers.BooleanField(default=False)


class AudioRequestSerializer(StageRequestSerializer):
    duration = serializers.FloatField(required=False, allow_null=True, default=None)


class VisualsRequestSerializer(StageRequestSerializer):
    prompt = serializers.CharField(required=False, allow_null=True, default=None)
    negative = serializers.CharField(required=False, allow_null=True, default=None)
    count = serializers.IntegerField(required=False, allow_null=True, default=None)
    width = serializers.IntegerField(required=False, allow_null=True, default=None)
    height = serializers.IntegerField(required=False, allow_null=True, default=None)
    seed = serializers.IntegerField(required=False, allow_null=True, default=None)
    model = serializers.CharField(required=False, allow_null=True, default=None)
    style = serializers.CharField(required=False, allow_null=True, default=None)
    depicted = serializers.BooleanField(default=False)


class RunRequestSerializer(StageRequestSerializer):
    pass


class ProduceRequestSerializer(StageRequestSerializer):
    concept_id = serializers.CharField(required=False, allow_null=True, default=None)
    duration = serializers.FloatField(required=False, allow_null=True, default=None)
    production_grade_visuals = serializers.BooleanField(
        required=False, allow_null=True, default=None)


STAGE_SERIALIZERS = {
    "research": ResearchRequestSerializer,
    "creative": CreativeRequestSerializer,
    "storyboard": StoryboardRequestSerializer,
    "scenes": ScenesRequestSerializer,
    "audio": AudioRequestSerializer,
    "visuals": VisualsRequestSerializer,
    "run": RunRequestSerializer,
    "produce": ProduceRequestSerializer,
}
