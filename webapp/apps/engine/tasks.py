"""The one generic Celery dispatcher (architecture plan §8).

STAGE_FUNCS looks up and calls the typed run_* domain function and persists
its StageResult onto the PipelineRun row - it contains no pipeline business
rules of its own.

Ownership boundary (architecture plan §2): this task runs local/host-side
pipeline stages off the HTTP request thread. For depicted-image stages it
calls run_visuals/run_scenes exactly as the CLI does, which may call
generation.Router.generate() -> worker.enqueue_project(...) internally,
unchanged. Once that handoff happens, the remote-GPU job's lifecycle
belongs exclusively to scripts/worker.py's own state machine - this task
never touches, polls, or duplicates that job JSON. Celery task state and a
worker.py job's state are two distinct, never-merged progress signals.
"""
import logging

from celery import shared_task
from django.utils import timezone

import scripts.project as project

from apps.pipeline.models import PipelineRun

log = logging.getLogger(__name__)


def _fail(run, message):
    """Land a terminal failure on the run row.

    Shared by the busy-project case and the unexpected-crash case so a row
    can never be left mid-flight: a RUNNING row with no live task is
    indistinguishable from real work in every surface that reads it.
    """
    run.status = PipelineRun.STATUS_FAILED
    run.exit_code = 1
    run.message = message
    run.log_tail = message
    run.finished_at = timezone.now()
    run.save(update_fields=["status", "exit_code", "message", "log_tail", "finished_at"])

STAGE_FUNCS = {
    "research": project.run_research,
    "creative": project.run_creative,
    "storyboard": project.run_storyboard,
    "scenes": project.run_scenes,
    "audio": project.run_audio,
    "visuals": project.run_visuals,
    "run": lambda video_id, **params: project.run_pipeline(video_id),
    "produce": project.run_produce,
    "editable": project.run_editable,
}


@shared_task(bind=True)
def run_stage_task(self, pipeline_run_id, stage, video_id, params):
    run = PipelineRun.objects.get(pk=pipeline_run_id)
    run.status = PipelineRun.STATUS_RUNNING
    run.started_at = timezone.now()
    run.celery_task_id = self.request.id
    run.save(update_fields=["status", "started_at", "celery_task_id"])

    func = STAGE_FUNCS[stage]
    try:
        result = func(video_id, **params)
    except project.ProjectBusyError as e:
        _fail(run, str(e))
        return
    except Exception as e:  # noqa: BLE001 - a crashed stage must not strand the row
        # Without this the row stays RUNNING forever and the UI waits on a
        # job that is already dead. The run is the only thing the operator
        # can see, so an unexpected failure has to land on it.
        log.exception("stage %s crashed for %s", stage, video_id)
        _fail(run, f"{stage} failed unexpectedly: {e.__class__.__name__}: {e}")
        raise

    if result.exit_code == 0:
        run.status = PipelineRun.STATUS_SUCCEEDED
    elif result.exit_code == 2:
        run.status = PipelineRun.STATUS_NEEDS_ATTENTION
    else:
        run.status = PipelineRun.STATUS_FAILED
    run.exit_code = result.exit_code
    run.message = result.message
    run.data = result.data
    run.log_tail = result.message
    run.finished_at = timezone.now()
    run.save(update_fields=["status", "exit_code", "message", "data", "log_tail", "finished_at"])


@shared_task
def resume_waiting_productions():
    """Re-run a stage that parked on the GPU queue once its images landed.

    Celery beat calls this on a short cadence. It is what turns "queued for
    the PC" into a one-click production: nobody has to come back and press
    Produce again when the workstation finishes. Only the latest run of a
    project is considered, and only when it parked waiting for the GPU
    (``data.waiting_for_gpu``) and the domain layer says nothing for it is
    still queued or in flight - so a resumed run that succeeds, fails or
    parks again is never re-triggered by its predecessor. The remote jobs
    themselves are only read, through project.gpu_wait_resolved.
    """
    import uuid

    from apps.engine.services.pipeline import trigger_stage

    resumed = []
    parked = (PipelineRun.objects
              .filter(status=PipelineRun.STATUS_NEEDS_ATTENTION)
              .values_list("video_id", flat=True).distinct())
    for video_id in set(parked):
        latest = PipelineRun.objects.filter(video_id=video_id).first()
        if latest is None or latest.status != PipelineRun.STATUS_NEEDS_ATTENTION:
            continue
        if not (latest.data or {}).get("waiting_for_gpu"):
            continue
        if not project.gpu_wait_resolved(video_id):
            continue
        params = dict(latest.params or {})
        try:
            run, created = trigger_stage(video_id, latest.stage, uuid.uuid4(), params)
        except project.ProjectBusyError:
            continue
        if created:
            log.info("resuming %s for %s: GPU images landed (run %s)",
                     latest.stage, video_id, run.pk)
            resumed.append(video_id)
    return resumed
