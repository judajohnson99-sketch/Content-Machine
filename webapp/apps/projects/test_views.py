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


@pytest.mark.django_db
class TestProjectAssetsView:
    def test_returns_404_for_an_unknown_project(self, client):
        with patch("apps.projects.views.assets_service.get_assets", return_value=None):
            response = client.get("/api/v1/projects/no-such-project/assets/")
        assert response.status_code == 404

    def test_returns_the_manifest_unmodified(self, client):
        fake = {"video_id": "abc", "video": None, "thumbnails": [], "images": [],
                "audio": None, "qc": None, "storyboard": None, "package": None, "logs": []}
        with patch("apps.projects.views.assets_service.get_assets", return_value=fake):
            response = client.get("/api/v1/projects/abc/assets/")
        assert response.status_code == 200
        assert response.json() == fake


@pytest.mark.django_db
class TestProjectFileView:
    """The servable-path rule itself is scripts.project.project_file_path's
    and is proven in tests/test_project.py; here only the HTTP contract on
    top of it is exercised, against a real temp file."""

    @pytest.fixture
    def asset(self, tmp_path):
        path = tmp_path / "candidate_1.jpg"
        path.write_bytes(b"0123456789" * 10)   # 100 bytes
        return path

    def test_every_refusal_is_a_404(self, client):
        import scripts.project as project
        with patch("apps.projects.views.assets_service.resolve_file",
                   side_effect=project.ProjectError(["resolves outside the project"])):
            response = client.get("/api/v1/projects/abc/files/output/../metadata.json")
        assert response.status_code == 404

    def test_unauthenticated_request_is_rejected(self):
        response = APIClient().get("/api/v1/projects/abc/files/thumbnail/candidate_1.jpg")
        assert response.status_code == 403

    def test_serves_the_whole_file_with_a_content_type(self, client, asset):
        with patch("apps.projects.views.assets_service.resolve_file", return_value=asset) as res:
            response = client.get("/api/v1/projects/abc/files/thumbnail/candidate_1.jpg")
        res.assert_called_once_with("abc", "thumbnail/candidate_1.jpg")
        assert response.status_code == 200
        assert response["Content-Type"] == "image/jpeg"
        assert response["Accept-Ranges"] == "bytes"
        assert b"".join(response.streaming_content) == b"0123456789" * 10

    def test_honours_a_byte_range(self, client, asset):
        with patch("apps.projects.views.assets_service.resolve_file", return_value=asset):
            response = client.get("/api/v1/projects/abc/files/thumbnail/candidate_1.jpg",
                                  HTTP_RANGE="bytes=10-19")
        assert response.status_code == 206
        assert response["Content-Range"] == "bytes 10-19/100"
        assert response["Content-Length"] == "10"
        assert b"".join(response.streaming_content) == b"0123456789"

    def test_open_ended_and_suffix_ranges(self, client, asset):
        with patch("apps.projects.views.assets_service.resolve_file", return_value=asset):
            tail = client.get("/api/v1/projects/abc/files/thumbnail/candidate_1.jpg",
                              HTTP_RANGE="bytes=95-")
            suffix = client.get("/api/v1/projects/abc/files/thumbnail/candidate_1.jpg",
                                HTTP_RANGE="bytes=-5")
        assert tail["Content-Range"] == "bytes 95-99/100"
        assert suffix["Content-Range"] == "bytes 95-99/100"
        assert b"".join(tail.streaming_content) == b"56789"

    def test_unsatisfiable_range_is_416(self, client, asset):
        with patch("apps.projects.views.assets_service.resolve_file", return_value=asset):
            response = client.get("/api/v1/projects/abc/files/thumbnail/candidate_1.jpg",
                                  HTTP_RANGE="bytes=500-600")
        assert response.status_code == 416
        assert response["Content-Range"] == "bytes */100"
