"""Adapter for triggering a Celery-backed pipeline stage.

Idempotency and concurrency (architecture plan §6): in-flight duplicates
are rejected fail-fast via the domain layer's own project_lock;
post-completion retries are deduplicated on
(video_id, stage, client_request_id), which PipelineRun enforces with a
unique constraint. No logic here re-derives what either of those primitives
already decides - it only wires the response.
"""
from django.db import IntegrityError, transaction

import scripts.project as project

from apps.engine.tasks import run_stage_task
from apps.pipeline.models import PipelineRun


def trigger_stage(video_id, stage, client_request_id, params):
    """Idempotently trigger `stage` for `video_id`.

    Returns (PipelineRun, created). `created` is False for both kinds of
    duplicate the architecture plan names: a matching client_request_id
    already recorded (post-completion retry) or a fresh row that lost a
    creation race to a concurrent identical request (still returns the
    winner's row, not an error - the request's intent was already honored).

    Raises project.ProjectBusyError if another mutating operation already
    holds this project's lock - the same fail-fast primitive the CLI uses,
    surfaced by the view as HTTP 409 via apps.engine.exceptions.translate.
    """
    existing = PipelineRun.objects.filter(
        video_id=video_id, stage=stage, client_request_id=client_request_id,
    ).first()
    if existing:
        return existing, False

    # Fail-fast probe using the domain layer's own lock, acquired and
    # released immediately: a genuinely concurrent mutating operation on
    # this project is rejected right now rather than only surfacing later
    # as a FAILED PipelineRun once a worker gets to it. run_stage_task
    # acquires the same lock again for the actual work - this probe is
    # advisory, not a reservation, so run_* itself remains the source of
    # truth for correctness.
    with project.project_lock(video_id):
        pass

    try:
        with transaction.atomic():
            run = PipelineRun.objects.create(
                video_id=video_id, stage=stage,
                client_request_id=client_request_id, params=params,
            )
    except IntegrityError:
        return PipelineRun.objects.get(
            video_id=video_id, stage=stage, client_request_id=client_request_id,
        ), False

    run_stage_task.delay(run.id, stage, video_id, params)
    return run, True


def get_run(video_id, run_id):
    return PipelineRun.objects.filter(video_id=video_id, pk=run_id).first()


def latest_runs(video_id):
    """The most recent PipelineRun per stage for `video_id`, or None where a
    stage has never been triggered from the web layer.

    This is the one place the frontend's pipeline view gets stage progress
    from - a plain read of PipelineRun (already ordered newest-first, see
    its Meta.ordering), never a re-derivation of what a stage's state means.
    A stage with no row here simply hasn't been run through the API yet; it
    says nothing about whether the CLI has run it directly on disk.
    """
    runs = PipelineRun.objects.filter(video_id=video_id)
    latest = {stage: None for stage, _ in PipelineRun.STAGE_CHOICES}
    for run in runs:
        if latest[run.stage] is None:
            latest[run.stage] = run
    return latest
