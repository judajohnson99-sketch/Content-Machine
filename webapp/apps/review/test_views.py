"""DRF endpoint tests for review decisions. Request/response contract
only - apps.engine.services.review is mocked throughout, per
apps.projects.test_views's convention; the domain layer itself (gate
re-check, digest staleness, production_grade regression, CLI/API parity)
is proven once in tests/test_project.py (architecture plan §5/§17).
"""
import uuid
from unittest.mock import patch

import pytest
from rest_framework.test import APIClient

import scripts.project as project


@pytest.fixture
def owner(django_user_model):
    return django_user_model.objects.create_user(
        username="owner", password="pw", email="owner@example.com")


@pytest.fixture
def client(owner):
    api = APIClient()
    api.force_authenticate(user=owner)
    return api


@pytest.mark.django_db
class TestReviewDecisionListCreateView:
    def test_unauthenticated_get_is_rejected(self):
        response = APIClient().get("/api/v1/projects/abc/review-decisions/")
        assert response.status_code == 403

    def test_unauthenticated_post_is_rejected(self):
        response = APIClient().post("/api/v1/projects/abc/review-decisions/", {}, format="json")
        assert response.status_code == 403

    def test_get_returns_404_for_an_unknown_project(self, client):
        with patch("apps.review.views.review_service.list_review_decisions",
                    return_value=None):
            response = client.get("/api/v1/projects/abc/review-decisions/")
        assert response.status_code == 404

    def test_get_returns_the_review_history(self, client):
        history = [{"utc": "2026-09-17T00:00:00Z", "reviewer": "owner@example.com",
                    "decision": "approved", "notes": "", "gate_digest": "abc123"}]
        with patch("apps.review.views.review_service.list_review_decisions",
                    return_value=history):
            response = client.get("/api/v1/projects/abc/review-decisions/")
        assert response.status_code == 200
        assert response.json() == history

    def test_post_requires_expected_digest(self, client):
        response = client.post(
            "/api/v1/projects/abc/review-decisions/",
            {"decision": "approved"}, format="json")
        assert response.status_code == 400

    def test_post_rejects_an_invalid_decision(self, client):
        response = client.post(
            "/api/v1/projects/abc/review-decisions/",
            {"decision": "maybe", "expected_digest": "abc123"}, format="json")
        assert response.status_code == 400

    def test_post_sources_the_reviewer_from_request_user_not_the_body(self, client):
        entry = {"utc": "2026-09-17T00:00:00Z", "reviewer": "owner@example.com",
                 "decision": "approved", "notes": "", "gate_digest": "abc123"}
        with patch("apps.review.views.review_service.record_decision",
                    return_value=entry) as record:
            response = client.post(
                "/api/v1/projects/abc/review-decisions/",
                {"decision": "approved", "expected_digest": "abc123",
                 "reviewer": "someone-else@evil.example"},
                format="json")
        assert response.status_code == 201
        record.assert_called_once_with(
            "abc", "owner@example.com", "approved", "", "abc123")

    def test_falls_back_to_username_when_the_user_has_no_email(self, django_user_model):
        user = django_user_model.objects.create_user(username="noemail", password="pw")
        api = APIClient()
        api.force_authenticate(user=user)
        entry = {"utc": "2026-09-17T00:00:00Z", "reviewer": "noemail",
                 "decision": "rejected", "notes": "", "gate_digest": "abc123"}
        with patch("apps.review.views.review_service.record_decision",
                    return_value=entry) as record:
            response = api.post(
                "/api/v1/projects/abc/review-decisions/",
                {"decision": "rejected", "expected_digest": "abc123"}, format="json")
        assert response.status_code == 201
        record.assert_called_once_with("abc", "noemail", "rejected", "", "abc123")

    def test_a_stale_digest_is_reported_as_409(self, client):
        with patch("apps.review.views.review_service.record_decision",
                    side_effect=project.ReviewDecisionError("expected_digest is stale")):
            response = client.post(
                "/api/v1/projects/abc/review-decisions/",
                {"decision": "approved", "expected_digest": "abc123"}, format="json")
        assert response.status_code == 409

    def test_unmet_blockers_are_reported_as_409(self, client):
        with patch("apps.review.views.review_service.record_decision",
                    side_effect=project.ReviewDecisionError("cannot approve: no title set")):
            response = client.post(
                "/api/v1/projects/abc/review-decisions/",
                {"decision": "approved", "expected_digest": "abc123"}, format="json")
        assert response.status_code == 409

    def test_an_unknown_project_is_reported_as_404(self, client):
        with patch("apps.review.views.review_service.record_decision",
                    side_effect=project.ProjectError(["not a project: abc"])):
            response = client.post(
                "/api/v1/projects/abc/review-decisions/",
                {"decision": "approved", "expected_digest": "abc123"}, format="json")
        assert response.status_code == 404

    def test_a_busy_project_is_reported_as_409(self, client):
        with patch("apps.review.views.review_service.record_decision",
                    side_effect=project.ProjectBusyError("abc")):
            response = client.post(
                "/api/v1/projects/abc/review-decisions/",
                {"decision": "approved", "expected_digest": "abc123"}, format="json")
        assert response.status_code == 409


class TestNoAutomatedCaller:
    """Extends architecture plan §5's static check to the web layer: the
    Celery dispatcher must never be able to record a review decision - only
    this app's own view may call into review_service.record_decision."""

    def test_stage_funcs_does_not_include_a_review_stage(self):
        from apps.engine.tasks import STAGE_FUNCS
        assert "review" not in STAGE_FUNCS
        assert "approve" not in STAGE_FUNCS
        assert "reject" not in STAGE_FUNCS

    def test_tasks_module_never_imports_review_service(self):
        import apps.engine.tasks as tasks_module
        assert not hasattr(tasks_module, "review")
