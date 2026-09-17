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


class StatusReportSerializer(serializers.Serializer):
    """Mirrors scripts.project.status_report()'s dict shape exactly."""
    video_id = serializers.CharField()
    recorded = serializers.CharField()
    verdict = serializers.CharField()
    blocking = serializers.ListField(child=serializers.CharField())
    stale = serializers.BooleanField(allow_null=True)
    digest_state = serializers.CharField(allow_null=True)
