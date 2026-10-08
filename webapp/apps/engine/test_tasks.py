"""apps.engine.tasks.run_stage_task: the generic dispatcher (architecture
plan §8) - does it call the right STAGE_FUNCS entry and persist its
StageResult onto PipelineRun correctly? No domain logic is exercised for
real; that is tests/test_project.py's job (plan §17).
"""
from unittest.mock import MagicMock, patch

import pytest

import scripts.project as project
from apps.engine.tasks import STAGE_FUNCS, run_stage_task
from apps.pipeline.models import PipelineRun


@pytest.mark.django_db
class TestRunStageTask:
    def _run(self, stage="creative", **kwargs):
        return PipelineRun.objects.create(video_id="abc", stage=stage, **kwargs)

    def test_calls_the_stage_function_with_video_id_and_params(self):
        run = self._run()
        fake = MagicMock(return_value=project.StageResult(True, 0, "ok", {"x": 1}))
        with patch.dict(STAGE_FUNCS, {"creative": fake}):
            run_stage_task(run.id, "creative", "abc", {"force": True})
        fake.assert_called_once_with("abc", force=True)

    def test_a_successful_result_marks_the_run_succeeded(self):
        run = self._run()
        fake = MagicMock(return_value=project.StageResult(True, 0, "ok", {"x": 1}))
        with patch.dict(STAGE_FUNCS, {"creative": fake}):
            run_stage_task(run.id, "creative", "abc", {})
        run.refresh_from_db()
        assert run.status == PipelineRun.STATUS_SUCCEEDED
        assert run.exit_code == 0
        assert run.data == {"x": 1}
        assert run.started_at is not None
        assert run.finished_at is not None

    def test_exit_code_2_maps_to_needs_attention_not_failed(self):
        run = self._run(stage="run")
        fake = MagicMock(return_value=project.StageResult(False, 2, "needs attention"))
        with patch.dict(STAGE_FUNCS, {"run": fake}):
            run_stage_task(run.id, "run", "abc", {})
        run.refresh_from_db()
        assert run.status == PipelineRun.STATUS_NEEDS_ATTENTION
        assert run.exit_code == 2

    def test_a_non_zero_non_two_exit_code_maps_to_failed(self):
        run = self._run()
        fake = MagicMock(return_value=project.StageResult(False, 1, "boom"))
        with patch.dict(STAGE_FUNCS, {"creative": fake}):
            run_stage_task(run.id, "creative", "abc", {})
        run.refresh_from_db()
        assert run.status == PipelineRun.STATUS_FAILED

    def test_a_busy_lock_raised_during_execution_marks_the_run_failed(self):
        run = self._run()
        fake = MagicMock(side_effect=project.ProjectBusyError("abc"))
        with patch.dict(STAGE_FUNCS, {"creative": fake}):
            run_stage_task(run.id, "creative", "abc", {})
        run.refresh_from_db()
        assert run.status == PipelineRun.STATUS_FAILED
        assert "abc" in run.message

    def test_an_unexpected_crash_marks_the_run_failed_instead_of_stranding_it(self):
        """A stage that raises something other than ProjectBusyError used to
        leave the row at RUNNING forever, so the UI waited on a dead job."""
        run = self._run()
        fake = MagicMock(side_effect=RuntimeError("ffmpeg vanished"))
        with patch.dict(STAGE_FUNCS, {"creative": fake}):
            with pytest.raises(RuntimeError):
                run_stage_task(run.id, "creative", "abc", {})
        run.refresh_from_db()
        assert run.status == PipelineRun.STATUS_FAILED
        assert run.finished_at is not None
        assert "ffmpeg vanished" in run.message

    def test_the_run_stage_is_never_merged_with_worker_py_job_state(self):
        """Ownership boundary (architecture plan §2): this dispatcher must
        never import or touch scripts.worker - that state machine is
        worker.py's alone."""
        import apps.engine.tasks as tasks_module
        assert not hasattr(tasks_module, "worker")


@pytest.mark.django_db
class TestResumeWaitingProductions:
    """A production parked on the GPU queue resumes once its images land."""

    def _parked(self, video_id="vid", stage="produce", params=None, **kwargs):
        kwargs.setdefault("status", PipelineRun.STATUS_NEEDS_ATTENTION)
        kwargs.setdefault("data", {"waiting_for_gpu": True})
        return PipelineRun.objects.create(video_id=video_id, stage=stage,
                                          params=params or {"image_source": "automatic"},
                                          **kwargs)

    def _resume(self, resolved=True):
        from apps.engine.tasks import resume_waiting_productions
        fake = MagicMock(return_value=project.StageResult(True, 0, "done"))
        with patch.object(project, "gpu_wait_resolved", return_value=resolved), \
                patch.dict(STAGE_FUNCS, {"produce": fake}):
            resumed = resume_waiting_productions()
        return resumed, fake

    def test_resumes_with_the_same_stage_and_params_once_images_landed(self):
        self._parked()
        resumed, fake = self._resume()
        assert resumed == ["vid"]
        fake.assert_called_once_with("vid", image_source="automatic")
        latest = PipelineRun.objects.filter(video_id="vid").first()
        assert latest.status == PipelineRun.STATUS_SUCCEEDED

    def test_waits_while_gpu_jobs_are_still_pending(self):
        self._parked()
        resumed, fake = self._resume(resolved=False)
        assert resumed == []
        fake.assert_not_called()

    def test_ignores_a_run_that_needs_attention_for_another_reason(self):
        self._parked(data={"something": "else"})
        resumed, fake = self._resume()
        assert resumed == []

    def test_never_re_resumes_once_a_newer_run_exists(self):
        self._parked()
        PipelineRun.objects.create(video_id="vid", stage="produce",
                                   status=PipelineRun.STATUS_SUCCEEDED)
        resumed, fake = self._resume()
        assert resumed == []
        fake.assert_not_called()
