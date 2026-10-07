"""DRF views for /api/v1/projects/{video_id}/review-decisions/.

Translation only (architecture plan §8) with one deliberate exception:
reviewer identity is sourced from request.user, never the request body -
"who is the reviewer" must never be a client-supplied claim, matching the
CLI's own required --reviewer flag (never a default). No gate re-check or
digest comparison happens here; that is entirely
scripts.project.record_review_decision()'s job.
"""
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

import scripts.project as project

from apps.engine.exceptions import ProjectNotFound, translate
from apps.engine.services import review as review_service

from .serializers import (
    AudioGradeRequestSerializer, AudioGradeSerializer,
    ReviewDecisionRequestSerializer, ReviewDecisionSerializer,
    VisualGradeRequestSerializer, VisualGradeSerializer,
)


class ReviewDecisionListCreateView(APIView):
    """GET lists metadata.json.review_history; POST records a new decision."""

    def get(self, request, video_id):
        history = review_service.list_review_decisions(video_id)
        if history is None:
            raise ProjectNotFound(f"no such project: {video_id}")
        return Response(ReviewDecisionSerializer(history, many=True).data)

    def post(self, request, video_id):
        serializer = ReviewDecisionRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        reviewer = request.user.email or request.user.get_username()

        try:
            entry = review_service.record_decision(
                video_id, reviewer,
                serializer.validated_data["decision"],
                serializer.validated_data["notes"],
                serializer.validated_data["expected_digest"])
        except project.ProjectError as e:
            translate(e)

        return Response(ReviewDecisionSerializer(entry).data, status=status.HTTP_201_CREATED)


class VisualGradeView(APIView):
    """POST /api/v1/projects/{video_id}/visual-grade/ - the human's
    production-grade claim for the project's visuals.

    Reviewer identity comes from request.user, never the body, exactly as
    for review decisions: only a person in a session can make this claim,
    and scripts.project.record_visual_grade() is the sole writer of it.
    """

    def post(self, request, video_id):
        serializer = VisualGradeRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        reviewer = request.user.email or request.user.get_username()
        try:
            entry = review_service.record_visual_grade(
                video_id, reviewer,
                serializer.validated_data["production_grade"],
                serializer.validated_data["notes"])
        except project.ProjectError as e:
            translate(e)
        return Response(VisualGradeSerializer(entry).data, status=status.HTTP_201_CREATED)


class AudioGradeView(APIView):
    """POST /api/v1/projects/{video_id}/audio-grade/ - the human's verdict
    on the project's audio.

    Exists so this decision is not CLI-only: the gate holds a synthesised
    music track until a person has listened, and the person doing the
    review is in the browser. Same rules as the visual claim - reviewer
    from request.user, scripts.project.record_audio_grade() the sole
    writer.
    """

    def post(self, request, video_id):
        serializer = AudioGradeRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        reviewer = request.user.email or request.user.get_username()
        try:
            entry = review_service.record_audio_grade(
                video_id, reviewer,
                serializer.validated_data["production_grade"],
                serializer.validated_data["notes"])
        except project.ProjectError as e:
            translate(e)
        return Response(AudioGradeSerializer(entry).data, status=status.HTTP_201_CREATED)
