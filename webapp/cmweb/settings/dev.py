from .base import *  # noqa: F401,F403

DEBUG = True
ALLOWED_HOSTS = ['localhost', '127.0.0.1', 'testserver']

# The Vite dev server's default origin.
CORS_ALLOWED_ORIGINS = [
    'http://localhost:5173',
    'http://127.0.0.1:5173',
]
CORS_ALLOW_CREDENTIALS = True  # session cookies for DRF SessionAuthentication

# Django's CSRF middleware checks the Origin header against this list for
# cross-origin unsafe requests (e.g. the Vite dev server posting to the API).
CSRF_TRUSTED_ORIGINS = [
    'http://localhost:5173',
    'http://127.0.0.1:5173',
]
