from rest_framework import serializers


class ProjectSummarySerializer(serializers.Serializer):
    """Mirrors scripts.project.list_projects()'s dict shape exactly - no
    transformation, no derived fields; the domain layer already decided
    what a summary contains."""
    video_id = serializers.CharField()
    selected_title = serializers.CharField(allow_null=True)
    concept_id = serializers.CharField(allow_null=True)
    niche = serializers.CharField(allow_null=True)
    overall_status = serializers.CharField()
    created_utc = serializers.CharField(allow_null=True)


class ProjectCreateSerializer(serializers.Serializer):
    """POST /api/v1/projects/ body. Shape only - scripts.experiment.
    scaffold_project() re-validates the id and concept; this just refuses
    obviously malformed input before the domain layer is reached."""
    video_id = serializers.RegexField(
        r"^[a-z0-9][a-z0-9-]{2,63}$",
        error_messages={"invalid": "use 3-64 lowercase letters, digits or hyphens"})
    concept_id = serializers.CharField(max_length=128)
    duration = serializers.FloatField(required=False, allow_null=True, default=None,
                                      min_value=1.0)


class StatusReportSerializer(serializers.Serializer):
    """Mirrors scripts.project.status_report()'s dict shape exactly."""
    video_id = serializers.CharField()
    recorded = serializers.CharField()
    verdict = serializers.CharField()
    blocking = serializers.ListField(child=serializers.CharField())
    stale = serializers.BooleanField(allow_null=True)
    digest_state = serializers.CharField(allow_null=True)
