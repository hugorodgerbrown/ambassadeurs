"""Root URL configuration.

Mounts the Django admin, the account login/logout flow, the language switcher,
and the public site. HTMX fragment routes live under each app's ``partials/``
prefix. allauth has been removed (VERB-46); login is now first-party magic-link
under ``accounts/``.
"""

from django.contrib import admin
from django.urls import include, path

urlpatterns = [
    path("admin/", admin.site.urls),
    # First-party magic-link auth + account self-service (VERB-46).
    path("account/", include("accounts.urls")),
    path("i18n/", include("django.conf.urls.i18n")),
    # DEBUG-only test-data panel. Always mounted; every view raises Http404
    # when settings.DEBUG is false (via require_debug decorator).
    path("debug/", include("debug.urls")),
    path("", include("public.urls")),
]
