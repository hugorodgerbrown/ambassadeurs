# Base Django settings shared across all environments.
#
# Environment-specific modules (development.py, production.py) import everything
# from here and override what differs. Secrets are read via python-decouple;
# never hard-code credentials. See CLAUDE.md "Conventions" and "Authentication".

from pathlib import Path

from decouple import config

# Repo root: base.py -> settings -> config -> <root>.
BASE_DIR = Path(__file__).resolve().parent.parent.parent

# --- Applications ---------------------------------------------------------

DJANGO_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "django.contrib.sitemaps",
]

THIRD_PARTY_APPS = [
    "django_htmx",
    "django_countries",
]

LOCAL_APPS = [
    "core",
    "accounts",
    "matching",
    "public",
    "debug",
]

INSTALLED_APPS = DJANGO_APPS + THIRD_PARTY_APPS + LOCAL_APPS

# --- Middleware -----------------------------------------------------------

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.locale.LocaleMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "django_htmx.middleware.HtmxMiddleware",
]

ROOT_URLCONF = "config.urls"
WSGI_APPLICATION = "config.wsgi.application"
ASGI_APPLICATION = "config.asgi.application"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "django.template.context_processors.i18n",
                "django.template.context_processors.debug",
                "debug.context_processors.debug_panel",
            ],
        },
    },
]

# --- Matching / registration window ---------------------------------------
# The contact window, and the dates during which registration is open, are
# configured via environment variables. Dev defaults: window always open.
# AUTH_USER_MODEL is the default Django ``auth.User``; custom attributes live
# on ``matching.Registration`` (1:1 via OneToOneField to User). Admin users
# have a User but no Registration.

# Absolute base URL for the site (no trailing slash). Used to build absolute
# links in emails sent from background tasks where no request object is available
# (e.g. send_match_notification runs inside transaction.on_commit).
BASE_URL: str = config("BASE_URL", default="http://localhost:8000")

CONTACT_WINDOW_HOURS: int = config("CONTACT_WINDOW_HOURS", default=72, cast=int)
# Registration window bounds are dates (YYYY-MM-DD); time and timezone are
# ignored. Both bounds are inclusive — registration is open on the closing date.
REGISTRATION_OPENS_AT: str = config("REGISTRATION_OPENS_AT", default="2020-01-01")
REGISTRATION_CLOSES_AT: str = config("REGISTRATION_CLOSES_AT", default="2099-12-31")

# Deferred matching (VERB-81/82): registration can open well before matching
# begins, so the queue builds on both sides first. Dev default is far-past so
# matching is always open locally (current synchronous behaviour unchanged).
# Parsed as a full ISO 8601 datetime by matching.pricing_config.matching_opens_at.
MATCHING_OPENS_AT: str = config(
    "MATCHING_OPENS_AT", default="2020-01-01T00:00:00+00:00"
)

# Tiered prepaid registration fee (VERB-81/82): a comma-separated schedule of
# "YYYY-MM-DD:CHF" thresholds, each meaning "from this date onward the fee
# is N CHF". Empty default means always free in dev (matches today's free
# registration). Parsed by matching.pricing_config.fee_chf_for.
REGISTRATION_FEE_TIERS: str = config("REGISTRATION_FEE_TIERS", default="")

# External application-form PDF (hosted off-app by the 4 Vallées). The download
# view redirects here; kept in config so the URL can change without a deploy.
APPLICATION_FORM_URL: str = config(
    "APPLICATION_FORM_URL",
    default=(
        "https://verbier4vallees.ch/V4V-Website/Documents/Parrainage/"
        "AMBASSADOR_V4V_26_27.pdf"
    ),
)

# --- Authentication -------------------------------------------------------
# AUTH_USER_MODEL stays the default Django ``auth.User``.

AUTHENTICATION_BACKENDS = [
    "django.contrib.auth.backends.ModelBackend",
]

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": f"django.contrib.auth.password_validation.{name}"}
    for name in (
        "UserAttributeSimilarityValidator",
        "MinimumLengthValidator",
        "CommonPasswordValidator",
        "NumericPasswordValidator",
    )
]

# Magic-link login (VERB-46 — allauth removed).
# LOGIN_URL uses the named URL so @login_required redirects go to the new form.
LOGIN_URL = "accounts:login"
LOGIN_REDIRECT_URL = "accounts:detail"

# --- Internationalisation -------------------------------------------------

LANGUAGE_CODE = "en"
LANGUAGES = [("en", "English"), ("fr", "French")]
LOCALE_PATHS = [BASE_DIR / "locale"]
TIME_ZONE = "Europe/Zurich"
USE_I18N = True
USE_TZ = True

# --- Static files ---------------------------------------------------------

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STATICFILES_DIRS = [BASE_DIR / "static"]

STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
}

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# --- Cache ---------------------------------------------------------------
# django-ratelimit requires a usable cache. LocMemCache is per-process and
# is acceptable for the single-instance launch on Render. If the web service
# is ever scaled horizontally this must be replaced with a shared backend
# such as Redis or Memcached — otherwise rate-limit counters will not be
# shared across instances and the limits will be ineffective.
CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
    }
}

# --- GeoIP (MaxMind GeoLite2-City) ----------------------------------------
# Path to the local GeoLite2-City .mmdb file. The file is downloaded by
# build.sh when MAXMIND_LICENSE_KEY is set; absent locally unless you run
# the download script. When the file is missing, geolocation degrades
# gracefully: registration_country/region are stored as empty strings and a
# warning is logged. The raw client IP is NEVER persisted (in memory only).
GEOIP_DATABASE_PATH: str = config(
    "GEOIP_DATABASE_PATH",
    default=str(BASE_DIR / "geoip" / "GeoLite2-City.mmdb"),
)

DEFAULT_FROM_EMAIL = config(
    "DEFAULT_FROM_EMAIL", default="Ambassadeurs <noreply@example.com>"
)

# --- Logging --------------------------------------------------------------

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "verbose": {
            "format": "{asctime} {levelname} {name} {message}",
            "style": "{",
        },
    },
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
            "formatter": "verbose",
        },
    },
    "root": {"handlers": ["console"], "level": "INFO"},
    "loggers": {
        "django": {"handlers": ["console"], "level": "INFO", "propagate": False},
    },
}
