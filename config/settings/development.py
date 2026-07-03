# Development settings — local dev and the test suite.
#
# Insecure defaults are acceptable here; never reuse them in production.
#
# Postgres in CI (via DATABASE_URL, VERB-98) so Postgres-only SQL constraints
# surface in the pytest suite, with SQLite as the zero-setup local default so a
# developer can run tests without a database container.

from decouple import config

from .base import *  # noqa: F403

DEBUG = True

SECRET_KEY = config(
    "SECRET_KEY",
    default="django-insecure-dev-key-not-for-production",  # noqa: S106
)

ALLOWED_HOSTS = ["localhost", "127.0.0.1", "0.0.0.0"]  # noqa: S104

# Required for django.template.context_processors.debug to set {{ debug }}=True.
# The test client sends requests from 127.0.0.1 by default.
INTERNAL_IPS = ["127.0.0.1"]

_database_url = config("DATABASE_URL", default="")
if _database_url:
    import dj_database_url

    DATABASES = {"default": dj_database_url.config(env="DATABASE_URL")}
else:
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.sqlite3",
            "NAME": BASE_DIR / "db.sqlite3",  # noqa: F405
        }
    }

# Signed-link / verification emails are written to the console in development.
EMAIL_BACKEND = "django.core.mail.backends.console.EmailBackend"

# Disable rate limiting in development and the test suite so repeated local
# requests (and the test runner's 200+ hits from 127.0.0.1) are never blocked.
# The rate-limit tests opt back in via @override_settings(RATELIMIT_ENABLE=True).
RATELIMIT_ENABLE = False

# Content-Security-Policy in report-only mode locally: violations are logged by
# the browser but nothing is blocked, so a policy slip surfaces before it can
# break a page. Production enforces the same directives. See base.CSP_DIRECTIVES.
CONTENT_SECURITY_POLICY_REPORT_ONLY = {"DIRECTIVES": CSP_DIRECTIVES}  # noqa: F405
