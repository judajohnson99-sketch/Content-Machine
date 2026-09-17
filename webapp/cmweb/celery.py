"""The Celery application for the web control center.

Runs the same typed run_* domain functions the CLI calls, off the HTTP
request thread (architecture plan §2/§7). This is the only place Celery is
configured; it never touches, replaces, or duplicates scripts/worker.py's
remote-GPU job state machine - see apps/engine/tasks.py for the dispatcher
and the ownership boundary it documents.
"""
import os

from celery import Celery

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "cmweb.settings.dev")

app = Celery("cmweb")
app.config_from_object("django.conf:settings", namespace="CELERY")
app.autodiscover_tasks()
