"""Settings for pytest.

CELERY_TASK_ALWAYS_EAGER runs `.delay()` synchronously in-process (still
through the exact same task function and STAGE_FUNCS dispatch), so the test
suite exercises real task/PipelineRun wiring without requiring a live
Redis/worker - the standard Celery testing pattern, not a stand-in for the
domain logic itself (that stays real, mocked or served locally, never
faked - see AGENTS.md/CLAUDE.md's testing convention).
"""
from .dev import *  # noqa: F401,F403

CELERY_TASK_ALWAYS_EAGER = True
CELERY_TASK_EAGER_PROPAGATES = True
