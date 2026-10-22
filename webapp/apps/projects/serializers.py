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
    archived = serializers.BooleanField(default=False)
    preview_image = serializers.CharField(allow_null=True, required=False)


class ProjectArchiveSerializer(serializers.Serializer):
    """POST .../archive/: true archives, false restores. Never deletes."""
    archived = serializers.BooleanField()
    reason = serializers.CharField(required=False, allow_blank=True, default="",
                                   max_length=500)


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
    # quality.summary(): whether a perceptual assessment exists, its verdict
    # and whether it still describes the render on disk.
    quality = serializers.DictField(allow_null=True)


class SeedReferenceSerializer(serializers.Serializer):
    type = serializers.ChoiceField(choices=("channel", "video"))
    value = serializers.CharField(max_length=500)


class ResearchBriefSerializer(serializers.Serializer):
    """PUT /api/v1/projects/{id}/research/brief/ body. Shape only -
    scripts.research.save_brief() re-validates; this refuses obviously
    malformed input before the domain layer is reached."""
    niche = serializers.CharField(max_length=200)
    creative_intent = serializers.CharField(
        max_length=2000, required=False, allow_null=True, allow_blank=True, default=None)
    likes = serializers.ListField(
        child=serializers.CharField(max_length=200), required=False, default=list)
    dislikes = serializers.ListField(
        child=serializers.CharField(max_length=200), required=False, default=list)
    seed_references = SeedReferenceSerializer(many=True, required=False, default=list)
    notes = serializers.CharField(
        max_length=2000, required=False, allow_null=True, allow_blank=True, default=None)


class ProductionGoalSerializer(serializers.Serializer):
    """POST /api/v1/projects/from-goal/ body.

    A goal is prose, so there is almost nothing to validate here beyond
    "there is one". scripts.goal re-reads the length out of the words; the
    optional ``minutes`` is the operator overriding that.
    """
    goal = serializers.CharField(min_length=8, max_length=2000, trim_whitespace=True)
    video_id = serializers.RegexField(
        r"^[a-z0-9][a-z0-9-]{2,63}$", required=False, allow_null=True, default=None,
        error_messages={"invalid": "use 3-64 lowercase letters, digits or hyphens"})
    minutes = serializers.FloatField(required=False, allow_null=True, default=None,
                                     min_value=0.5, max_value=720.0)
    # Build it short first. The concept still records the full length, so
    # the workspace can offer to produce it properly once the look is judged.
    excerpt_seconds = serializers.FloatField(required=False, allow_null=True,
                                             default=None, min_value=5.0,
                                             max_value=600.0)


class ProjectDeleteSerializer(serializers.Serializer):
    """DELETE /api/v1/projects/{id}/ body.

    ``confirm_video_id`` must echo the id in the URL. Typing the name of the
    thing being destroyed is the whole safeguard: there is no undo, and a
    stray click on a list row must not be able to reach this.
    """
    confirm_video_id = serializers.CharField(max_length=64)
    reason = serializers.CharField(required=False, allow_blank=True, default="",
                                   max_length=500)


class VisualAssetSearchSerializer(serializers.Serializer):
    """POST .../scenes/{scene}/assets/ - what to look for. All optional: with
    nothing given, the scene's own plan supplies the search words."""
    query = serializers.CharField(required=False, allow_blank=True, default="", max_length=200)
    kind = serializers.ChoiceField(choices=("image", "video"), required=False, allow_null=True,
                                   default=None)
    sources = serializers.ListField(
        child=serializers.ChoiceField(choices=("library", "stock", "generated")),
        required=False, allow_null=True, default=None)
    more = serializers.BooleanField(required=False, default=False)
    pages = serializers.DictField(child=serializers.IntegerField(min_value=1),
                                  required=False, allow_null=True, default=None)


class VisualAssetChoiceSerializer(serializers.Serializer):
    """POST .../assets/select/ and .../assets/save/. ``approve`` is the
    owner's explicit first-use approval of external media; it never defaults
    on, and the licence it approves is the one stored at search time."""
    candidate_id = serializers.CharField(max_length=300)
    approve = serializers.BooleanField(required=False, default=False)
    note = serializers.CharField(required=False, allow_blank=True, default="", max_length=1000)


class SceneRegenerateSerializer(serializers.Serializer):
    seed = serializers.IntegerField(required=False, allow_null=True, default=None)
    prompt = serializers.CharField(required=False, allow_blank=True, allow_null=True,
                                   default=None, max_length=4000)
    negative_prompt = serializers.CharField(required=False, allow_blank=True, allow_null=True,
                                            default=None, max_length=2000)
