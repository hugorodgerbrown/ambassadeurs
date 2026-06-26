# Production settings — Render single web service + one Postgres database.
#
# All secrets come from the environment via python-decouple / dj-database-url.

import dj_database_url
from decouple import Csv, config

from .base import *  # noqa: F403

DEBUG = False

SECRET_KEY = config("SECRET_KEY")

ALLOWED_HOSTS = config("ALLOWED_HOSTS", cast=Csv())

DATABASES = {
    "default": dj_database_url.config(
        env="DATABASE_URL",
        conn_max_age=600,
        ssl_require=True,
    )
}

# WhiteNoise with hashed, compressed filenames for cache-busting.
STORAGES["staticfiles"] = {  # noqa: F405
    "BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage",
}

# --- Security -------------------------------------------------------------

SECURE_SSL_REDIRECT = True
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True
SECURE_HSTS_SECONDS = 60 * 60 * 24 * 365
SECURE_HSTS_INCLUDE_SUBDOMAINS = True
SECURE_HSTS_PRELOAD = True
SECURE_CONTENT_TYPE_NOSNIFF = True
CSRF_TRUSTED_ORIGINS = config("CSRF_TRUSTED_ORIGINS", cast=Csv(), default="")

EMAIL_HASH_SECRET = config("EMAIL_HASH_SECRET")

# --- Email ----------------------------------------------------------------

EMAIL_BACKEND = "django.core.mail.backends.smtp.EmailBackend"
EMAIL_HOST = config("EMAIL_HOST", default="")
EMAIL_PORT = config("EMAIL_PORT", cast=int, default=587)
EMAIL_HOST_USER = config("EMAIL_HOST_USER", default="")
EMAIL_HOST_PASSWORD = config("EMAIL_HOST_PASSWORD", default="")
EMAIL_USE_TLS = config("EMAIL_USE_TLS", cast=bool, default=True)
DEFAULT_FROM_EMAIL = config("DEFAULT_FROM_EMAIL", default=DEFAULT_FROM_EMAIL)  # noqa: F405
