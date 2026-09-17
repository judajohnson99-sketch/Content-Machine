"""DRF endpoint tests: request/response contract only; readiness and the job
views are proven in the main repo's tests/test_worker.py and
tests/test_experiment.py."""
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
class TestReadinessView:
    def test_returns_host_capabilities_unmodified(self, client):
        fake = {"depicted_imagery": {"state": "worker_offline"}, "remote_gpu": {"workers": []}}
        with patch("apps.system.views.workers_service.readiness", return_value=fake):
            response = client.get("/api/v1/system/readiness/")
        assert response.status_code == 200
        assert response.json() == fake

    def test_unauthenticated_request_is_rejected(self):
        assert APIClient().get("/api/v1/system/readiness/").status_code == 403


@pytest.mark.django_db
class TestGpuJobListView:
    def test_lists_jobs_and_passes_the_project_filter_through(self, client):
        fake = [{"job_id": "abc", "state": "QUEUED", "project_id": "vid"}]
        with patch("apps.system.views.workers_service.list_jobs", return_value=fake) as lister:
            response = client.get("/api/v1/system/gpu-jobs/?project=vid")
        assert response.status_code == 200
        assert response.json() == fake
        lister.assert_called_once_with(project_id="vid")

    def test_no_filter_means_every_job(self, client):
        with patch("apps.system.views.workers_service.list_jobs", return_value=[]) as lister:
            assert client.get("/api/v1/system/gpu-jobs/").status_code == 200
        lister.assert_called_once_with(project_id=None)
