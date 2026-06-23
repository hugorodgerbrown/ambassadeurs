"""Root URL configuration.

Mounts the Django admin, allauth, the language switcher, and the public site.
HTMX fragment routes live under each app's ``partials/`` prefix.
"""

from django.contrib import admin
from django.urls import include, path

urlpatterns = [
    path("admin/", admin.site.urls),
    path("accounts/", include("allauth.urls")),
    path("i18n/", include("django.conf.urls.i18n")),
    path("", include("public.urls")),
]
