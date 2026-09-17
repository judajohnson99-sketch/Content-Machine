"""
Django settings shared by every environment (dev.py/prod.py import from here).

The one thing specific to this project: the repo root (the directory that
contains `scripts/`, `projects/`, `experiments/`) is added to `sys.path` so
`apps/engine`'s adapters can `import scripts.project` etc. in-process,
exactly as the architecture plan requires - Django never re-implements
pipeline logic, it imports the same modules the CLI does.
"""
import os
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent.parent           # webapp/
CONTENT_MACHINE_ROOT = BASE_DIR.parent                              # content-machine/

if str(CONTENT_MACHINE_ROOT) not in sys.path:
    sys.path.insert(0, str(CONTENT_MACHINE_ROOT))

SECRET_KEY = 'django-insecure-55^1i)5y=cxo@z9#3svfm*f19w3a#xf+@@&9s-*%hqjj@y56r8'

DEBUG = True

ALLOWED_HOSTS = []

INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'rest_framework',
    'corsheaders',
    'apps.engine',
    'apps.projects',
    'apps.pipeline',
]

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'corsheaders.middleware.CorsMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

ROOT_URLCONF = 'cmweb.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
            ],
        },
    },
]

WSGI_APPLICATION = 'cmweb.wsgi.application'

# Phase 2 adds PipelineRun (apps/pipeline/models.py) - the one genuinely new
# table the architecture plan allows (§3). It is coordination state only
# (Celery bookkeeping + client_request_id idempotency), never a mirror of
# project/gate truth, so SQLite remains a correct dev/test default; prod.py
# switches to Postgres via DB_ENGINE=postgres, which is what a real
# deployment should set (a lightly-used Postgres per the plan's topology).
DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.sqlite3',
        'NAME': BASE_DIR / 'db.sqlite3',
    }
}

# --- Celery / Redis (Phase 2, architecture plan §2/§7) ---------------------
# Same-host Redis, same-VPS Celery worker process. This is infrastructure
# Django/Celery need to talk to each other - not the COMFYUI_URL kind of
# endpoint the project's fail-closed convention forbids defaulting (that
# rule is about never assuming a *remote* machine is present; Redis here is
# a local service this same deployment owns and starts).
CELERY_BROKER_URL = os.environ.get("REDIS_URL", "redis://127.0.0.1:6379/0")
CELERY_RESULT_BACKEND = CELERY_BROKER_URL
CELERY_TASK_SERIALIZER = "json"
CELERY_RESULT_SERIALIZER = "json"
CELERY_ACCEPT_CONTENT = ["json"]
CELERY_TASK_TRACK_STARTED = True
# Modest concurrency to start (architecture plan §16 migration risk: "size
# Postgres/Redis conservatively; single Celery worker process with modest
# concurrency"). One project's mutating stages are already serialized by
# project_lock regardless of this value.
CELERY_WORKER_CONCURRENCY = int(os.environ.get("CELERY_WORKER_CONCURRENCY", "2"))

AUTH_PASSWORD_VALIDATORS = [
    {'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator'},
    {'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator'},
    {'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator'},
    {'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator'},
]

LANGUAGE_CODE = 'en-us'
TIME_ZONE = 'UTC'
USE_I18N = True
USE_TZ = True

STATIC_URL = 'static/'

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

REST_FRAMEWORK = {
    # Single-owner app (see architecture plan §10/constraint 10): session
    # auth only, no OAuth/token/multi-tenant machinery.
    'DEFAULT_AUTHENTICATION_CLASSES': [
        'rest_framework.authentication.SessionAuthentication',
    ],
    'DEFAULT_PERMISSION_CLASSES': [
        'rest_framework.permissions.IsAuthenticated',
    ],
}
