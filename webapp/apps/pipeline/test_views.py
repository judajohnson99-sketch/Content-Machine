"""DRF endpoint tests for pipeline-stage triggers. Request/response
contract only, per apps.projects.test_views's convention - the service
layer is mocked except in TestIdempotency, which is the one place the
architecture plan (§17) asks for a real end-to-end dedup proof.
"""
import uuid
from unittest.mock import MagicMock, patch

import pytest
from rest_framework.test import APIClient

import scripts.project as project
from apps.engine.tasks import STAGE_FUNCS
from apps.pipeline.models import PipelineRun


@pytest.fixture
def owner(django_user_model):
    return django_user_model.objects.create_user(username="owner", password="pw")


@pytest.fixture
def client(owner):
    api = APIClient()
    api.force_authenticate(user=owner)
    return api


@pytest.mark.django_db
class TestStageActionView:
    def test_unauthenticated_request_is_rejected(self):
        response = APIClient().post("/api/v1/projects/abc/creative/", {}, format="json")
        assert response.status_code == 403

    def test_missing_client_request_id_is_rejected(self, client):
        response = client.post("/api/v1/projects/abc/creative/", {}, format="json")
        assert response.status_code == 400

    def test_a_new_request_is_queued_and_returns_202(self, client):
        crid = str(uuid.uuid4())
        fake_run = MagicMock(id=1)
        with patch("apps.pipeline.views.pipeline_service.trigger_stage",
                    return_value=(fake_run, True)) as trig, \
                patch("apps.pipeline.views.PipelineRunSerializer") as ser:
            ser.return_value.data = {"id": 1, "status": "QUEUED"}
            response = client.post(
                "/api/v1/projects/abc/creative/",
                {"client_request_id": crid, "force": True}, format="json")
        assert response.status_code == 202
        trig.assert_called_once_with("abc", "creative", uuid.UUID(crid), {"force": True})

    def test_a_repeated_client_request_id_returns_200(self, client):
        crid = str(uuid.uuid4())
        fake_run = MagicMock(id=1)
        with patch("apps.pipeline.views.pipeline_service.trigger_stage",
                    return_value=(fake_run, False)), \
                patch("apps.pipeline.views.PipelineRunSerializer") as ser:
            ser.return_value.data = {"id": 1, "status": "SUCCEEDED"}
            response = client.post(
                "/api/v1/projects/abc/creative/",
                {"client_request_id": crid}, format="json")
        assert response.status_code == 200

    def test_a_busy_project_is_reported_as_409(self, client):
        crid = str(uuid.uuid4())
        with patch("apps.pipeline.views.pipeline_service.trigger_stage",
                    side_effect=project.ProjectBusyError("abc")):
            response = client.post(
                "/api/v1/projects/abc/creative/",
                {"client_request_id": crid}, format="json")
        assert response.status_code == 409

    def test_produce_accepts_scaffold_params(self, client):
        """produce's params (concept_id/duration/production_grade_visuals)
        must round-trip through its own request serializer, unlike every
        other stage."""
        crid = str(uuid.uuid4())
        fake_run = MagicMock(id=1)
        with patch("apps.pipeline.views.pipeline_service.trigger_stage",
                    return_value=(fake_run, True)) as trig, \
                patch("apps.pipeline.views.PipelineRunSerializer") as ser:
            ser.return_value.data = {"id": 1, "status": "QUEUED"}
            response = client.post(
                "/api/v1/projects/new-video/produce/",
                {"client_request_id": crid, "concept_id": "sleep-brown-noise-dark",
                 "duration": 60.0, "production_grade_visuals": True},
                format="json")
        assert response.status_code == 202
        trig.assert_called_once_with(
            "new-video", "produce", uuid.UUID(crid),
            {"concept_id": "sleep-brown-noise-dark", "duration": 60.0,
             "production_grade_visuals": True})


@pytest.mark.django_db
class TestPipelineRunDetailView:
    def test_returns_404_for_an_unknown_run(self, client):
        response = client.get("/api/v1/projects/abc/pipeline-runs/999/")
        assert response.status_code == 404

    def test_returns_the_run_scoped_to_its_video_id(self, client):
        run = PipelineRun.objects.create(video_id="abc", stage="creative",
                                          status=PipelineRun.STATUS_SUCCEEDED, exit_code=0)
        response = client.get(f"/api/v1/projects/abc/pipeline-runs/{run.id}/")
        assert response.status_code == 200
        assert response.json()["status"] == "SUCCEEDED"

        other = client.get(f"/api/v1/projects/other-video/pipeline-runs/{run.id}/")
        assert other.status_code == 404


@pytest.mark.django_db
class TestIdempotency:
    """architecture plan §17: posting the same client_request_id twice must
    create (and dispatch) exactly one PipelineRun - real HTTP view, real
    trigger_stage, real DB, real eager Celery task; only project_lock and
    the stage's own domain function are mocked, so no project fixture or
    filesystem is needed."""

    def test_posting_the_same_client_request_id_twice_creates_one_run(self, client):
        crid = str(uuid.uuid4())
        fake = MagicMock(return_value=project.StageResult(True, 0, "ok", {}))
        with patch("apps.engine.services.pipeline.project.project_lock"), \
                patch.dict(STAGE_FUNCS, {"creative": fake}):
            first = client.post("/api/v1/projects/abc/creative/",
                                 {"client_request_id": crid}, format="json")
            second = client.post("/api/v1/projects/abc/creative/",
                                  {"client_request_id": crid}, format="json")

        assert first.status_code == 202
        assert second.status_code == 200
        assert first.json()["id"] == second.json()["id"]
        assert PipelineRun.objects.filter(video_id="abc", stage="creative").count() == 1
        assert fake.call_count == 1


@pytest.mark.django_db
class TestPipelineRunListView:
    def test_every_stage_is_present_and_untriggered_stages_are_null(self, client):
        response = client.get("/api/v1/projects/abc/pipeline-runs/")
        assert response.status_code == 200
        body = response.json()
        assert set(body) == {stage for stage, _ in PipelineRun.STAGE_CHOICES}
        assert body["creative"] is None

    def test_a_triggered_stage_reports_its_latest_run(self, client):
        PipelineRun.objects.create(video_id="abc", stage="creative",
                                    status=PipelineRun.STATUS_SUCCEEDED, exit_code=0)
        response = client.get("/api/v1/projects/abc/pipeline-runs/")
        assert response.json()["creative"]["status"] == "SUCCEEDED"

    def test_scoped_to_its_own_video_id(self, client):
        PipelineRun.objects.create(video_id="other-video", stage="creative")
        response = client.get("/api/v1/projects/abc/pipeline-runs/")
        assert response.json()["creative"] is None

    def test_unauthenticated_request_is_rejected(self):
        response = APIClient().get("/api/v1/projects/abc/pipeline-runs/")
        assert response.status_code == 403
