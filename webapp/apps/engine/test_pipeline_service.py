"""apps.engine.services.pipeline: idempotency and lock-probe plumbing
(architecture plan §6). Filesystem-free - project.project_lock is mocked,
exactly like apps.engine.test_services keeps domain calls mocked.
"""
import uuid
from datetime import timedelta

import pytest
from unittest.mock import patch

from django.utils import timezone

import scripts.project as project
from apps.engine.services import pipeline as pipeline_service
from apps.pipeline.models import PipelineRun


@pytest.mark.django_db
class TestTriggerStage:
    def test_creates_a_queued_run_and_dispatches_the_task(self):
        crid = uuid.uuid4()
        with patch("apps.engine.services.pipeline.project.project_lock") as lock, \
                patch("apps.engine.services.pipeline.run_stage_task.delay") as delay:
            run, created = pipeline_service.trigger_stage(
                "abc", "creative", crid, {"force": False})

        assert created is True
        assert run.video_id == "abc"
        assert run.stage == "creative"
        assert run.client_request_id == crid
        assert run.status == PipelineRun.STATUS_QUEUED
        lock.assert_called_once_with("abc")
        delay.assert_called_once_with(run.id, "creative", "abc", {"force": False})

    def test_a_repeated_client_request_id_is_not_dispatched_again(self):
        crid = uuid.uuid4()
        with patch("apps.engine.services.pipeline.project.project_lock"), \
                patch("apps.engine.services.pipeline.run_stage_task.delay") as delay:
            first, first_created = pipeline_service.trigger_stage(
                "abc", "creative", crid, {"force": False})
            second, second_created = pipeline_service.trigger_stage(
                "abc", "creative", crid, {"force": False})

        assert first_created is True
        assert second_created is False
        assert first.id == second.id
        delay.assert_called_once()
        assert PipelineRun.objects.filter(video_id="abc", stage="creative").count() == 1

    def test_a_different_client_request_id_dispatches_a_second_run(self):
        with patch("apps.engine.services.pipeline.project.project_lock"), \
                patch("apps.engine.services.pipeline.run_stage_task.delay") as delay:
            pipeline_service.trigger_stage("abc", "creative", uuid.uuid4(), {})
            pipeline_service.trigger_stage("abc", "creative", uuid.uuid4(), {})

        assert delay.call_count == 2
        assert PipelineRun.objects.filter(video_id="abc", stage="creative").count() == 2

    def test_a_busy_project_lock_propagates_without_creating_a_run(self):
        with patch("apps.engine.services.pipeline.project.project_lock",
                    side_effect=project.ProjectBusyError("abc")):
            with pytest.raises(project.ProjectBusyError):
                pipeline_service.trigger_stage("abc", "creative", uuid.uuid4(), {})

        assert PipelineRun.objects.count() == 0


@pytest.mark.django_db
class TestGetRun:
    def test_returns_none_for_an_unknown_run(self):
        assert pipeline_service.get_run("abc", 999) is None

    def test_returns_the_run_scoped_to_its_video_id(self):
        run = PipelineRun.objects.create(video_id="abc", stage="creative")
        assert pipeline_service.get_run("abc", run.id) == run
        assert pipeline_service.get_run("other-video", run.id) is None


@pytest.mark.django_db
class TestLatestRuns:
    def test_a_stage_never_triggered_is_none(self):
        latest = pipeline_service.latest_runs("abc")
        assert latest["creative"] is None
        assert set(latest) == {stage for stage, _ in PipelineRun.STAGE_CHOICES}

    def test_returns_the_newest_run_per_stage(self):
        older = PipelineRun.objects.create(video_id="abc", stage="creative")
        PipelineRun.objects.filter(pk=older.pk).update(
            created_at=timezone.now() - timedelta(minutes=5))
        newer = PipelineRun.objects.create(video_id="abc", stage="creative")

        latest = pipeline_service.latest_runs("abc")
        assert latest["creative"].id == newer.id

    def test_other_videos_runs_are_excluded(self):
        PipelineRun.objects.create(video_id="other-video", stage="creative")
        latest = pipeline_service.latest_runs("abc")
        assert latest["creative"] is None
