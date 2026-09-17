"""Thin adapter over the remote-GPU worker's read models.

No business logic lives here: readiness is scripts.experiment's host
capabilities (configuration + the worker registry on disk) and the job
views are scripts.worker's own. Nothing in the web layer may transition a
worker job - that state machine belongs to scripts/worker.py alone (the
agent and control plane drive it); this module only reads.
"""
import time

import scripts.experiment as experiment
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
