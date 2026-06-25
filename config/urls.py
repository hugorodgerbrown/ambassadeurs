"""Root URL configuration.

Mounts the Django admin, allauth, the language switcher, and the public site.
HTMX fragment routes live under each app's ``partials/`` prefix.
"""

from django.contrib import admin
from django.urls import include, path

urlpatterns = [
    path("admin/", admin.site.urls),
    # allauth's auth flows live under ``accounts/``; our self-service profile
    # area lives under the singular ``account/`` to avoid colliding with them.
    path("accounts/", include("allauth.urls")),
    path("account/", include("accounts.urls")),
    path("i18n/", include("django.conf.urls.i18n")),
    # DEBUG-only test-data panel. Always mounted; every view raises Http404
    # when settings.DEBUG is false (via require_debug decorator).
    path("debug/", include("debug.urls")),
    path("", include("public.urls")),
]
