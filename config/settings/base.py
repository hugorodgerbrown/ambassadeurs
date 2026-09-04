# Base Django settings shared across all environments.
#
# Environment-specific modules (development.py, production.py) import everything
# from here and override what differs. Secrets are read via python-decouple;
# never hard-code credentials. See CLAUDE.md "Conventions" and "Authentication".

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any, NamedTuple

from decouple import config
from django.utils.translation import gettext_lazy as _

if TYPE_CHECKING:
    from django.db.models import QuerySet

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
    # django-csp-plus (SKI-170). Unlike django-csp this is a full app: it owns
    # the CspRule / CspReport tables and the admin that makes the policy
    # editable at runtime.
    "csp",
    "django_htmx",
    "django_countries",
    "side_effects",
    "utm_tracker",
]

LOCAL_APPS = [
    "core",
    "accounts",
    "matching",
    "billing",
    "public",
    "debug",
]

INSTALLED_APPS = DJANGO_APPS + THIRD_PARTY_APPS + LOCAL_APPS

# --- Middleware -----------------------------------------------------------

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    # Host-based URLconf routing: confine the Django admin to ADMIN_HOST when
    # set (ADR 0022). Must precede LocaleMiddleware/CommonMiddleware, which read
    # request.urlconf. A no-op when ADMIN_HOST is empty.
    "core.middleware.AdminHostMiddleware",
    # django-csp-plus (SKI-170): split in two — CspNonceMiddleware puts
    # request.csp_nonce on the request (read by the nonce'd inline scripts in
    # base.html and friends), CspHeaderMiddleware writes the header on the way
    # out. Both raise MiddlewareNotUsed when CSP_ENABLED is false.
    "csp.middleware.CspNonceMiddleware",
    "csp.middleware.CspHeaderMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.locale.LocaleMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    # Marketing attribution (VERB-147, ADR 0023): normalise-then-stash utm/
    # click-id querystring params into the session, then let the library
    # persist a durable LeadSource once the visitor is authenticated. Placed
    # after AuthenticationMiddleware (LeadSourceMiddleware reads request.user)
    # and before MessageMiddleware. Registered in base.py (all environments)
    # so attribution works wherever a visitor first lands.
    "core.middleware.MarketingSourceMiddleware",
    "utm_tracker.middleware.LeadSourceMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "django_htmx.middleware.HtmxMiddleware",
    # Markdown representations (SKI-155, ADR 0026): advertises the .md twin of
    # each content page and serves it when the client's Accept header asks for
    # it. Last in the list so it sees the fully-rendered HTML response and can
    # convert it without re-running the view.
    "core.middleware.MarkdownRepresentationMiddleware",
]

ROOT_URLCONF = "config.urls"
WSGI_APPLICATION = "config.wsgi.application"
ASGI_APPLICATION = "config.asgi.application"

# --- Marketing attribution (django-utm-tracker, VERB-147, ADR 0023) -------
# Custom querystring tags stashed alongside the library's own utm_*/click-id
# set (see utm_tracker.request.parse_qs). "utm_content" completes the
# standard UTM quintet (the library only extracts it if listed here);
# "gad_source" is Google Ads' own click-source tag, distinct from gclid.
UTM_TRACKER_CUSTOM_TAGS = ["utm_content", "gad_source"]

# --- Host routing ----------------------------------------------------------
# When set (e.g. "admin.skiparrainage.com"), core.middleware.AdminHostMiddleware
# serves the Django admin ONLY on this host (config.urls_admin) and the public
# site with no /admin/ on every other host (config.urls_public). The host must
# also appear in ALLOWED_HOSTS and (https://) in CSRF_TRUSTED_ORIGINS. Empty
# default means single-host behaviour: the combined config.urls serves both the
# admin (at /admin/) and the public site. See ADR 0022.
ADMIN_HOST: str = config("ADMIN_HOST", default="")

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
                "core.context_processors.notifications",
                "core.context_processors.markdown_alternate",
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

# Homepage queue snapshot (VERB-149): the live "who's in the queue" pictograph
# is mounted on the public homepage but gated behind this flag, off by default,
# so it can be switched on for launch without a code change. When false the
# homepage renders exactly as before and no queue query runs. The standalone
# /queue/ page is unaffected — it stays available for QA regardless.
SHOW_HOMEPAGE_QUEUE: bool = config("SHOW_HOMEPAGE_QUEUE", default=False, cast=bool)

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

# --- Stripe (billing) ------------------------------------------------------
# The prepaid registration deposit (VERB-81/82, ADR 0014) is collected via
# Stripe. Empty defaults (Invariant 9: no secrets in source) mean the billing
# app degrades safely in development — no live keys required to run the
# suite; billing.services.payments reads these lazily so @override_settings
# works in tests.
STRIPE_SECRET_KEY: str = config("STRIPE_SECRET_KEY", default="")
STRIPE_PUBLISHABLE_KEY: str = config("STRIPE_PUBLISHABLE_KEY", default="")
STRIPE_CURRENCY: str = config("STRIPE_CURRENCY", default="chf")
# Signing secret for the checkout.session.completed webhook (VERB-86). Empty
# default means the webhook always fails signature verification until
# configured — never accept an unverified event.
STRIPE_WEBHOOK_SECRET: str = config("STRIPE_WEBHOOK_SECRET", default="")

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

# Untranslated/fuzzy catalogue entries (summed across locales) at which the
# review machinery opens a dedicated "update translation catalogues" task. Read
# by `manage.py update_messages --check`. See ADR 0016.
I18N_UPDATE_MESSAGES_THRESHOLD: int = config(
    "I18N_UPDATE_MESSAGES_THRESHOLD", default=10, cast=int
)

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

# --- Notifications (VERB-109) ----------------------------------------------
# CUSTOM_NOTIFICATION_GROUPS backs Notification.Audience.CUSTOM: each value is
# a pure, zero-argument callable returning a User queryset, evaluated lazily at
# render time by core.models.Notification.is_visible_to(). Model imports are
# done LAZILY inside each function body — settings are imported before the app
# registry is ready, so importing matching.models at module level here would
# raise AppRegistryNotReady. Editing this dict is a code change, not a UI
# feature (out of scope for VERB-109 — see the plan).


def _ambassadors() -> QuerySet[Any]:
    """Return users with an ambassador registration."""
    from django.contrib.auth import get_user_model

    from matching.models import Registration

    return get_user_model().objects.filter(
        registration__role=Registration.Role.AMBASSADOR
    )


def _referees() -> QuerySet[Any]:
    """Return users with a referee registration."""
    from django.contrib.auth import get_user_model

    from matching.models import Registration

    return get_user_model().objects.filter(registration__role=Registration.Role.REFEREE)


CUSTOM_NOTIFICATION_GROUPS: dict[str, Callable[[], QuerySet[Any]]] = {
    "ambassadors": _ambassadors,
    "referees": _referees,
}

# --- Notification designs (VERB-123) ---------------------------------------
# NOTIFICATION_DESIGNS backs Notification.design: each key is a free-form
# string validated against this dict in core.admin.NotificationForm.clean()
# (never a model-level `choices=`, since Django evaluates those at import
# time, before settings are guaranteed configured — mirrors the
# CUSTOM_NOTIFICATION_GROUPS precedent above). Editing this dict is a code
# change, not a UI feature: adding, renaming, or removing a design is a
# settings edit with no model/migration change, and staff pick a design from
# the admin dropdown it populates. label/description are staff-facing copy
# (translated); css_classes is a developer-authored class name injected
# verbatim into the banner's class="…" attribute — not display copy, so it is
# not wrapped for translation. There is deliberately no css_styles/inline-style
# field: production's CSP style-src has no 'unsafe-inline', so an element-level
# style="…" attribute would be silently dropped by the browser. Each design's
# look is instead a component class defined in src/css/main.css.


class NotificationDesign(NamedTuple):
    """One selectable look for the notification strip banner.

    ``css_classes`` is injected into the banner's ``class="…"`` attribute (see
    ``templates/includes/notification_strip.html``) and names a component
    class defined in ``src/css/main.css`` — never an inline ``style="…"``,
    which production's CSP ``style-src`` (no ``'unsafe-inline'``) would strip.
    It is a plain string authored by developers, not by end users, so
    Django's normal auto-escaping when rendering it is sufficient (Invariant
    4 is not implicated).
    """

    label: str
    description: str
    css_classes: str


# The four seed entries reproduce the four looks previously hard-coded as
# Notification.Priority (NEUTRAL/LOW/NORMAL/HIGH) and
# .notification-banner[data-priority="…"] in src/css/main.css — now the
# .notification-info/-muted/-notice/-urgent component classes there. Historical
# priority -> design key mapping (see the core.migrations data migration
# that ports existing rows): 0 NEUTRAL -> INFO, 1 LOW -> MUTED,
# 2 NORMAL -> NOTICE, 3 HIGH -> URGENT.
NOTIFICATION_DESIGNS: dict[str, NotificationDesign] = {
    "INFO": NotificationDesign(
        _("Info"),
        _("Calm, neutral tone for routine announcements."),
        "notification-info",
    ),
    "MUTED": NotificationDesign(
        _("Muted"),
        _("Low-key tone for background/secondary information."),
        "notification-muted",
    ),
    "NOTICE": NotificationDesign(
        _("Notice"),
        _("Standard tone for notices worth a second look."),
        "notification-notice",
    ),
    "URGENT": NotificationDesign(
        _("Urgent"),
        _("Attention-grabbing tone for urgent/critical notices."),
        "notification-urgent",
    ),
    "BRAND": NotificationDesign(
        _("Brand"),
        _("Solid alpine-red block with white text — the boldest look."),
        "notification-brand",
    ),
}

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

# --- Content-Security-Policy ----------------------------------------------
#
# Defence-in-depth behind Django's template auto-escaping (VERB-71, audit M2).
# Served by django-csp-plus (SKI-170), which builds the header from two layers:
# this static baseline, plus any enabled ``csp.CspRule`` rows, which EXTEND it
# at runtime. A blocked third-party origin is therefore an admin edit, not a
# deploy — and the violation reports that drive those edits are collected by
# the report endpoint mounted in config/urls_public.py. See ADR 0027.
#
# Origins in use: same-origin CSS/JS (WhiteNoise; htmx is self-hosted per
# VERB-70), the Google Fonts stylesheet (fonts.googleapis.com) and font files
# (fonts.gstatic.com), and Stripe hosted Checkout as a form-action target.
#
# form-action carries checkout.stripe.com (SKI-170) because Chrome and Safari
# re-check form-action against the target of a redirect that follows a form
# POST. Both money flows POST to a local view which 302s to Checkout
# (public.views._shared._redirect_to_checkout), so 'self' alone blocks the hop
# — and the browser reports the local action URL, not the Stripe one.
#
# Two placeholders are substituted per request by csp.policy: {nonce} becomes
# the response's nonce (base.html's font-swap script and friends read the same
# value from request.csp_nonce), and {report_uri} becomes the local report URL.
# Neither is a literal to be quoted — the library adds the quoting.
#
# NB values are normalised through csp.models.CspRule.clean_value, which
# lowercases them. A base64 hash source (e.g. 'sha256-…') does NOT survive
# that, which is why templates/500.html links a static stylesheet instead of
# carrying a hashed inline <style> block.
CSP_ENABLED = True

# Whether the header is advisory or enforced. Report-only here so development
# and the test suite observe without blocking; production.py flips it.
# NB django-csp-plus reads this at import time, so @override_settings cannot
# change it in a test — assert against the configured environment instead.
CSP_REPORT_ONLY = True

# How long a built policy is cached. The cache is per-process LocMemCache and
# Gunicorn runs several workers, so this is also the worst-case lag before an
# admin rule change is live on every worker. Kept short for that reason: the
# policy is cheap to rebuild (one indexed query) and the point of the runtime
# rules is that they take effect promptly.
CSP_CACHE_TIMEOUT = 60

CSP_DEFAULTS = {
    "default-src": ["'self'"],
    "script-src": ["'self'", "{nonce}"],
    "style-src": ["'self'", "https://fonts.googleapis.com"],
    "font-src": ["https://fonts.gstatic.com"],
    "img-src": ["'self'", "data:"],
    "connect-src": ["'self'"],
    "base-uri": ["'self'"],
    "form-action": ["'self'", "https://checkout.stripe.com"],
    "frame-ancestors": ["'none'"],
    "report-uri": ["{report_uri}"],
}
