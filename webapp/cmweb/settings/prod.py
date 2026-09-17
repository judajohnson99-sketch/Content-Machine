"""Production settings.

Postgres config is env-driven; Celery/Redis defaults already live in
base.py (same REDIS_URL env var applies here). This module exists so the
switch is a settings-module change (DJANGO_SETTINGS_MODULE=cmweb.settings.prod),
not a code change.
"""
import os

from .base import *  # noqa: F401,F403

DEBUG = False
ALLOWED_HOSTS = [h for h in os.environ.get("ALLOWED_HOSTS", "").split(",") if h]
SECRET_KEY = os.environ["DJANGO_SECRET_KEY"]

if os.environ.get("DB_ENGINE") == "postgres":
    DATABASES = {
        'default': {
            'ENGINE': 'django.db.backends.postgresql',
            'NAME': os.environ["DB_NAME"],
            'USER': os.environ["DB_USER"],
            'PASSWORD': os.environ["DB_PASSWORD"],
            'HOST': os.environ.get("DB_HOST", "127.0.0.1"),
            'PORT': os.environ.get("DB_PORT", "5432"),
        }
    }
