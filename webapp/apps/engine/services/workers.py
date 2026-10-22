"""Thin adapter over the remote-GPU worker's queue and read models.

No business logic lives here: readiness is scripts.experiment's host
capabilities (configuration + the worker registry on disk) and the job
views/queueing are scripts.worker's own. Nothing in the web layer may
transition a worker job past QUEUED - that state machine belongs to
scripts/worker.py alone (the agent and control plane drive it); this
module only reads, and the two writes it offers (``enqueue``, ``requeue``)
are the same entry points the CLI's ``worker enqueue`` / ``worker requeue``
use - both operator acts on a job, never a worker's report about one.
"""
import time

import scripts.experiment as experiment
import scripts.media as media
import scripts.worker as worker


def readiness():
    return experiment.host_capabilities()


def list_jobs(project_id=None):
    """Remote GPU jobs as job_view() dicts, newest first."""
    if project_id:
        return worker.jobs_for_project(project_id)
    now = time.time()
    workers = worker.load_workers()
    views = [worker.job_view(job, workers, now) for job in worker.load_jobs()]
    return sorted(views, key=lambda v: v.get("created_at") or "", reverse=True)


def project_jobs(video_id):
    return worker.jobs_for_project(video_id)


def enqueue(prompt, negative_prompt=None, width=1920, height=1080, count=1,
            seed=20260827, model=None):
    """Queue an ad-hoc render from the dashboard's Generate page.

    Never tied to a project and never accepts a workflow override - that
    stays a CLI-only capability (``worker enqueue --workflow``), so the
    dashboard can never point a real render at an untested template.
    """
    job = worker.enqueue_manual(prompt, negative_prompt=negative_prompt,
                                width=width, height=height, count=count,
                                seed=seed, model=model)
    return worker.job_view(job)


def job_asset(job_id, index):
    """The path to the ``index``-th asset of ``job_id``, or ``None``."""
    return worker.job_asset_path(job_id, index)


def save_asset(job_id, index):
    """Persist a completed Image Lab result in the shared media catalog."""
    job = worker.load_job(job_id)
    if job.get("state") != worker.SUCCEEDED:
        raise worker.WorkerError("only completed GPU results can be saved", 409)
    assets = job.get("assets") or []
    if index < 0 or index >= len(assets):
        raise worker.WorkerError("no such GPU job asset", 404)
    request = job.get("request") or {}
    try:
        return media.import_generated(
            assets[index], job_id=job_id, asset_index=index,
            prompt=request.get("prompt", ""), negative_prompt=request.get("negative_prompt"),
            model=request.get("model"), provider="comfyui",
            width=request.get("width"), height=request.get("height"),
            seed=request.get("seed"), settings=request.get("params"),
            label=job.get("label"), generated_utc=job.get("completed_at") or job.get("created_at"))
    except media.MediaError as exc:
        raise worker.WorkerError(str(exc), 422) from exc


def requeue(job_id):
    """Put a FAILED or CANCELLED job back in the queue (the Retry button).

    scripts.worker.requeue is the only path: it refuses any other state and
    appends the operator's decision to the job's audit trail.
    """
    return worker.job_view(worker.requeue(job_id, actor="operator"))
