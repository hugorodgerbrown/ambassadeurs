# Development settings — local dev and the test suite.
#
# Insecure defaults are acceptable here; never reuse them in production.

from decouple import config

from .base import *  # noqa: F403

DEBUG = True

SECRET_KEY = config(
    "SECRET_KEY",
    default="django-insecure-dev-key-not-for-production",  # noqa: S106
)

ALLOWED_HOSTS = ["localhost", "127.0.0.1", "0.0.0.0"]  # noqa: S104

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": BASE_DIR / "db.sqlite3",  # noqa: F405
    }
}

# Signed-link / verification emails are written to the console in development.
EMAIL_BACKEND = "django.core.mail.backends.console.EmailBackend"
