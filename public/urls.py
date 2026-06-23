"""URL routing for the public site (namespace: ``public``)."""

from django.templatetags.static import static
from django.urls import path
from django.views.generic.base import RedirectView

from . import views

app_name = "public"

urlpatterns = [
    path("", views.home, name="home"),
    path("register/<slug:role>/", views.register, name="register"),
    path("register/<slug:role>/done/", views.register_done, name="register_done"),
    path("legal/<slug:page>/", views.legal_page, name="legal"),
    # Well-known root requests served to avoid excess 404s (VERB-7).
    path("sw.js", views.service_worker, name="service_worker"),
    path(
        "favicon.ico",
        RedirectView.as_view(url=static("favicon.svg"), permanent=True),
        name="favicon",
    ),
]
