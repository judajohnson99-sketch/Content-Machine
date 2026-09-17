"""Request/response shapes for the review-decision API.

Mirrors scripts.project.record_review_decision()'s inputs/outputs exactly -
no transformation, no derived fields (architecture plan §8).
"""
from rest_framework import serializers

DECISION_CHOICES = ("approved", "rejected")


class ReviewDecisionRequestSerializer(serializers.Serializer):
    decision = serializers.ChoiceField(choices=DECISION_CHOICES)
    notes = serializers.CharField(required=False, allow_blank=True, default="")
    expected_digest = serializers.CharField()


class ReviewDecisionSerializer(serializers.Serializer):
    """Mirrors the entry shape record_review_decision() appends to
    metadata.json.review_history - a plain Serializer, not a
    ModelSerializer, since review history lives in a file, not a table
    (architecture plan §3: not built in Postgres for v1)."""
    utc = serializers.CharField()
    reviewer = serializers.CharField()
    decision = serializers.ChoiceField(choices=DECISION_CHOICES)
    notes = serializers.CharField(allow_blank=True)
    gate_digest = serializers.CharField()
