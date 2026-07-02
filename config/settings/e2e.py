# End-to-end (Playwright) settings — an ephemeral, production-shaped instance
# used only by the browser test suite in `e2e/`.
#
# The point of a dedicated module is fidelity WITHOUT the DEBUG-only shortcuts:
# DEBUG is False, so the `/debug/` test-data panel and the on-page magic-link
# shortcuts are OFF, exactly as in production. The suite therefore drives the
# real flows and must obtain confirmation / login / match links the same way a
# real user does — from email. Email is pointed at a Mailpit SMTP sink whose
# HTTP API the tests read (see e2e/README.md).
#
# Everything insecure here (HTTP cookies, no SSL redirect) is acceptable ONLY
# because this instance is thrown away at the end of a CI job. Never deploy it.

from decouple import config

from .base import *  # noqa: F403

# DEBUG off so the suite exercises production code paths (no debug panel, no
# on-page link surfacing). Links come from Mailpit instead.
DEBUG = False

SECRET_KEY = config("SECRET_KEY", default="django-insecure-e2e-key")  # noqa: S106

ALLOWED_HOSTS = ["localhost", "127.0.0.1", "0.0.0.0"]  # noqa: S104

# Postgres in CI (via DATABASE_URL), SQLite as a zero-setup local fallback so a
# developer can run the suite without a database container.
_database_url = config("DATABASE_URL", default="")
if _database_url:
    import dj_database_url

    DATABASES = {"default": dj_database_url.config(env="DATABASE_URL")}
else:
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.sqlite3",
            "NAME": BASE_DIR / "e2e.sqlite3",  # noqa: F405
        }
    }

# --- Email: send over SMTP to a Mailpit sink the tests can read back ---------
EMAIL_BACKEND = "django.core.mail.backends.smtp.EmailBackend"
EMAIL_HOST = config("EMAIL_HOST", default="localhost")
EMAIL_PORT = config("EMAIL_PORT", cast=int, default=1025)
EMAIL_USE_TLS = False
EMAIL_HOST_USER = ""
EMAIL_HOST_PASSWORD = ""

# --- Served over plain HTTP on localhost — relax the production hardening -----
SECURE_SSL_REDIRECT = False
SESSION_COOKIE_SECURE = False
CSRF_COOKIE_SECURE = False
SECURE_HSTS_SECONDS = 0

# Rate limiting off so a full suite run of repeated registrations is not
# throttled. The 429 behaviour is covered by the pytest suite, not the browser
# suite (see e2e/README.md → "What the suite deliberately does not cover").
RATELIMIT_ENABLE = False

# CSP in report-only mode: a violation is logged, nothing is blocked, so a
# policy slip never flakes an e2e run.
CONTENT_SECURITY_POLICY_REPORT_ONLY = {"DIRECTIVES": CSP_DIRECTIVES}  # noqa: F405

# The suite relies on the season being open and matching being synchronous with
# a free tier, so a second verified registration proposes a match immediately.
# These mirror the base defaults but are pinned here as documentation of the
# assumption; override via env in CI only for the closed-state scenarios.
REGISTRATION_OPENS_AT = config("REGISTRATION_OPENS_AT", default="2020-01-01")
REGISTRATION_CLOSES_AT = config("REGISTRATION_CLOSES_AT", default="2099-12-31")
MATCHING_OPENS_AT = config("MATCHING_OPENS_AT", default="2020-01-01T00:00:00+00:00")
REGISTRATION_FEE_TIERS = config("REGISTRATION_FEE_TIERS", default="")
