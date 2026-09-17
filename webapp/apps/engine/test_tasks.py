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

    def test_the_run_stage_is_never_merged_with_worker_py_job_state(self):
        """Ownership boundary (architecture plan §2): this dispatcher must
        never import or touch scripts.worker - that state machine is
        worker.py's alone."""
        import apps.engine.tasks as tasks_module
        assert not hasattr(tasks_module, "worker")
