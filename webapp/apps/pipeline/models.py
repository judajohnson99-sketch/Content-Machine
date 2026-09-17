"""PipelineRun - the one Postgres table the architecture plan allows (§3).

Coordination state only: it exists so Celery-triggered work is pollable and
deduplicable from the browser. It carries no pipeline-correctness meaning -
gate_blockers()/metadata.json remain the only truth about a project's
state. Nothing here may be treated as a second authority; if this row
disagrees with the project's own files, the files win.
"""
import uuid

from django.db import models


class PipelineRun(models.Model):
    STAGE_CHOICES = [
        ("research", "research"),
        ("creative", "creative"),
        ("storyboard", "storyboard"),
        ("scenes", "scenes"),
        ("audio", "audio"),
        ("visuals", "visuals"),
        ("run", "run"),
        ("produce", "produce"),
    ]

    STATUS_QUEUED = "QUEUED"
    STATUS_RUNNING = "RUNNING"
    STATUS_SUCCEEDED = "SUCCEEDED"
    STATUS_NEEDS_ATTENTION = "NEEDS_ATTENTION"
    STATUS_FAILED = "FAILED"
    STATUS_CHOICES = [
        (STATUS_QUEUED, STATUS_QUEUED),
        (STATUS_RUNNING, STATUS_RUNNING),
        (STATUS_SUCCEEDED, STATUS_SUCCEEDED),
        (STATUS_NEEDS_ATTENTION, STATUS_NEEDS_ATTENTION),
        (STATUS_FAILED, STATUS_FAILED),
    ]
    TERMINAL_STATUSES = {STATUS_SUCCEEDED, STATUS_NEEDS_ATTENTION, STATUS_FAILED}

    video_id = models.CharField(max_length=255, db_index=True)
    stage = models.CharField(max_length=32, choices=STAGE_CHOICES)
    client_request_id = models.UUIDField(default=uuid.uuid4)
    celery_task_id = models.CharField(max_length=255, null=True, blank=True)
    status = models.CharField(max_length=16, choices=STATUS_CHOICES, default=STATUS_QUEUED)

    # What the view was asked to run - kept for visibility/debugging, not
    # re-parsed as a second source of truth for anything.
    params = models.JSONField(default=dict, blank=True)

    exit_code = models.IntegerField(null=True, blank=True)
    message = models.TextField(blank=True, default="")
    data = models.JSONField(default=dict, blank=True)
    # A short human-readable tail, not a full log - the per-run log file on
    # disk (attach_log_file) remains the authoritative log.
    log_tail = models.TextField(blank=True, default="")

    created_at = models.DateTimeField(auto_now_add=True)
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        app_label = "pipeline"
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["video_id", "stage", "client_request_id"],
                name="unique_pipeline_run_per_client_request",
            ),
        ]
        indexes = [
            models.Index(fields=["video_id", "stage"]),
        ]

    def __str__(self):
        return f"{self.video_id}/{self.stage} [{self.status}]"
