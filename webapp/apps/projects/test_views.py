"""DRF endpoint tests: request/response contract only. The service layer is
mocked, so these tests need no filesystem, no fixtures, and no ffmpeg -
domain correctness is proven once in the main repo's tests/ (architecture
plan §17).
"""
from unittest.mock import patch

import pytest
from rest_framework.test import APIClient


@pytest.fixture
def owner(django_user_model):
    return django_user_model.objects.create_user(username="owner", password="pw")


@pytest.fixture
def client(owner):
    api = APIClient()
    api.force_authenticate(user=owner)
    return api


@pytest.mark.django_db
class TestProjectListView:
    def test_returns_the_service_layers_summaries(self, client):
        fake = [{
            "video_id": "abc", "selected_title": "Title", "concept_id": None,
            "niche": None, "overall_status": "NEEDS_ATTENTION", "created_utc": None,
        }]
        with patch("apps.projects.views.projects_service.list_projects", return_value=fake):
            response = client.get("/api/v1/projects/")
        assert response.status_code == 200
        assert response.json() == fake

    def test_unauthenticated_request_is_rejected(self):
        response = APIClient().get("/api/v1/projects/")
        assert response.status_code == 403


@pytest.mark.django_db
class TestProjectStatusView:
    def test_returns_404_for_an_unknown_project(self, client):
        with patch("apps.projects.views.projects_service.get_status", return_value=None):
            response = client.get("/api/v1/projects/no-such-project/status/")
        assert response.status_code == 404

    def test_returns_the_status_report_unmodified(self, client):
        fake = {
            "video_id": "abc", "recorded": "READY_FOR_REVIEW",
            "verdict": "READY_FOR_REVIEW", "blocking": [], "stale": False,
            "digest_state": "MATCHES",
        }
        with patch("apps.projects.views.projects_service.get_status", return_value=fake):
            response = client.get("/api/v1/projects/abc/status/")
        assert response.status_code == 200
        assert response.json() == fake
