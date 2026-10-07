from rest_framework import serializers


class GpuJobEnqueueSerializer(serializers.Serializer):
    """POST /api/v1/system/gpu-jobs/ body - an ad-hoc render from the
    dashboard's Generate page. Shape only - scripts.worker.enqueue_manual()
    re-validates the prompt; the bounds here just refuse an obviously
    malformed request before the domain layer is reached. No workflow
    override: that stays a CLI-only capability."""
    prompt = serializers.CharField(max_length=2000)
    negative_prompt = serializers.CharField(
        max_length=2000, required=False, allow_null=True, allow_blank=True, default=None)
    width = serializers.IntegerField(required=False, min_value=64, max_value=2048, default=1920)
    height = serializers.IntegerField(required=False, min_value=64, max_value=2048, default=1080)
    count = serializers.IntegerField(required=False, min_value=1, max_value=4, default=1)
    model = serializers.CharField(max_length=256, required=False, allow_null=True, default=None)
    seed = serializers.IntegerField(required=False, default=20260827)
