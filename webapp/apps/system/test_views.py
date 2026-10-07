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

    def test_post_queues_a_manual_render_and_returns_the_job(self, client):
        fake = {"job_id": "abc", "state": "QUEUED", "wait_reason": "WAITING_FOR_CAPABLE_WORKER"}
        with patch("apps.system.views.workers_service.enqueue", return_value=fake) as enq:
            response = client.post("/api/v1/system/gpu-jobs/", {"prompt": "a lit window"},
                                   format="json")
        assert response.status_code == 201
        assert response.json() == fake
        enq.assert_called_once_with(
            "a lit window", negative_prompt=None, width=1920, height=1080,
            count=1, seed=20260827, model=None)

    def test_post_without_a_prompt_is_a_400(self, client):
        response = client.post("/api/v1/system/gpu-jobs/", {}, format="json")
        assert response.status_code == 400

    def test_post_rejects_an_out_of_bounds_dimension(self, client):
        response = client.post(
            "/api/v1/system/gpu-jobs/",
            {"prompt": "a lit window", "width": 8000}, format="json")
        assert response.status_code == 400

    def test_post_offline_worker_still_queues_the_job(self, client):
        """An offline PC is not an error - the job queues and waits, exactly
        like every other path into scripts.worker.enqueue()."""
        fake = {"job_id": "abc", "state": "QUEUED", "wait_reason": "WAITING_FOR_CAPABLE_WORKER"}
        with patch("apps.system.views.workers_service.enqueue", return_value=fake):
            response = client.post("/api/v1/system/gpu-jobs/", {"prompt": "a lit window"},
                                   format="json")
        assert response.status_code == 201
        assert response.json()["state"] == "QUEUED"

    def test_post_translates_a_worker_error_to_a_400(self, client):
        import scripts.worker as worker
        with patch("apps.system.views.workers_service.enqueue",
                   side_effect=worker.WorkerError("prompt is required")):
            response = client.post("/api/v1/system/gpu-jobs/", {"prompt": "a lit window"},
                                   format="json")
        assert response.status_code == 400

    def test_unauthenticated_post_is_rejected(self):
        response = APIClient().post("/api/v1/system/gpu-jobs/", {"prompt": "x"}, format="json")
        assert response.status_code == 403


@pytest.mark.django_db
class TestGpuJobRequeueView:
    def test_requeues_and_returns_the_job_view(self, client):
        fake = {"job_id": "abc", "state": "QUEUED", "attempt": 0}
        with patch("apps.system.views.workers_service.requeue", return_value=fake) as requeue:
            response = client.post("/api/v1/system/gpu-jobs/abc/requeue/")
        assert response.status_code == 200
        assert response.json() == fake
        requeue.assert_called_once_with("abc")

    def test_a_job_that_is_not_requeuable_is_a_409(self, client):
        import scripts.worker as worker
        with patch("apps.system.views.workers_service.requeue",
                   side_effect=worker.WorkerError("job abc is RUNNING", 409)):
            response = client.post("/api/v1/system/gpu-jobs/abc/requeue/")
        assert response.status_code == 409
        assert "RUNNING" in response.json()["detail"]

    def test_an_unknown_job_is_a_404(self, client):
        import scripts.worker as worker
        with patch("apps.system.views.workers_service.requeue",
                   side_effect=worker.WorkerError("no such job", 404)):
            assert client.post("/api/v1/system/gpu-jobs/nope/requeue/").status_code == 404

    def test_unauthenticated_post_is_rejected(self):
        assert APIClient().post("/api/v1/system/gpu-jobs/abc/requeue/").status_code == 403


@pytest.mark.django_db
class TestGpuJobAssetView:
    """The servable-index rule itself is scripts.worker.job_asset_path's and
    is proven in tests/test_worker.py; here only the HTTP contract on top of
    it is exercised, against a real temp file."""

    @pytest.fixture
    def asset(self, tmp_path):
        path = tmp_path / "gen_abc123_01.png"
        path.write_bytes(b"0123456789" * 10)   # 100 bytes
        return path

    def test_unknown_job_or_out_of_range_index_is_a_404(self, client):
        with patch("apps.system.views.workers_service.job_asset", return_value=None):
            response = client.get("/api/v1/system/gpu-jobs/does-not-exist/assets/0/")
        assert response.status_code == 404

    def test_serves_the_asset_with_a_content_type(self, client, asset):
        with patch("apps.system.views.workers_service.job_asset", return_value=asset) as ja:
            response = client.get("/api/v1/system/gpu-jobs/abc/assets/0/")
        ja.assert_called_once_with("abc", 0)
        assert response.status_code == 200
        assert response["Content-Type"] == "image/png"
        assert b"".join(response.streaming_content) == b"0123456789" * 10

    def test_unauthenticated_request_is_rejected(self):
        response = APIClient().get("/api/v1/system/gpu-jobs/abc/assets/0/")
        assert response.status_code == 403
