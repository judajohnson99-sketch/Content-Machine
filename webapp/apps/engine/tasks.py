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
from celery import shared_task
from django.utils import timezone

import scripts.project as project

from apps.pipeline.models import PipelineRun

STAGE_FUNCS = {
    "research": project.run_research,
    "creative": project.run_creative,
    "storyboard": project.run_storyboard,
    "scenes": project.run_scenes,
    "audio": project.run_audio,
    "visuals": project.run_visuals,
    "run": lambda video_id, **params: project.run_pipeline(video_id),
    "produce": project.run_produce,
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
        run.status = PipelineRun.STATUS_FAILED
        run.exit_code = 1
        run.message = str(e)
        run.log_tail = str(e)
        run.finished_at = timezone.now()
        run.save(update_fields=["status", "exit_code", "message", "log_tail", "finished_at"])
        return

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
