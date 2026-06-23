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
    "django.contrib.sites",
]

THIRD_PARTY_APPS = [
    "allauth",
    "allauth.account",
    "allauth.socialaccount",
    "allauth.socialaccount.providers.facebook",
    "django_htmx",
]

LOCAL_APPS = [
    "core",
    "accounts",
    "matching",
    "public",
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
    "allauth.account.middleware.AccountMiddleware",
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

CONTACT_WINDOW_HOURS: int = config("CONTACT_WINDOW_HOURS", default=72, cast=int)
REGISTRATION_OPENS_AT: str = config(
    "REGISTRATION_OPENS_AT", default="2020-01-01T00:00:00+00:00"
)
REGISTRATION_CLOSES_AT: str = config(
    "REGISTRATION_CLOSES_AT", default="2099-12-31T23:59:59+00:00"
)

# --- Authentication -------------------------------------------------------
# AUTH_USER_MODEL stays the default Django ``auth.User``.

AUTHENTICATION_BACKENDS = [
    "django.contrib.auth.backends.ModelBackend",
    "allauth.account.auth_backends.AuthenticationBackend",
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

SITE_ID = 1

# allauth — passwordless, email-first. The signed-link and Facebook flows are
# finalised in the auth tickets (VERB-2/3/4); these are valid baseline values.
ACCOUNT_ADAPTER = "accounts.adapters.AccountAdapter"
ACCOUNT_LOGIN_METHODS = {"email"}
ACCOUNT_SIGNUP_FIELDS = ["email*"]
ACCOUNT_UNIQUE_EMAIL = True
ACCOUNT_EMAIL_VERIFICATION = "mandatory"
ACCOUNT_LOGIN_BY_CODE_ENABLED = True
# The default User model carries a username we don't use; let allauth ignore it.
ACCOUNT_USER_MODEL_USERNAME_FIELD = None
SOCIALACCOUNT_PROVIDERS = {"facebook": {}}

LOGIN_REDIRECT_URL = "/"
LOGIN_URL = "account_login"

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

DEFAULT_FROM_EMAIL = "Ambassadeurs <noreply@example.com>"

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
