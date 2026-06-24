"""URL routing for the public site (namespace: ``public``)."""

from django.templatetags.static import static
from django.urls import path
from django.views.generic.base import RedirectView

from . import views

app_name = "public"

urlpatterns = [
    path("", views.home, name="home"),
    # Streamlined, verify-first registration flow (VERB-9).
    path("register/", views.register_start, name="register"),
    path("register/sent/", views.register_email_sent, name="register_email_sent"),
    path("register/verify/<str:token>/", views.register_verify, name="register_verify"),
    path("register/details/", views.register_details, name="register_details"),
    path(
        "register/details/form/",
        views.register_details_form,
        name="register_details_form",
    ),
    path("register/done/<slug:role>/", views.register_done, name="register_done"),
    path("legal/<slug:page>/", views.legal_page, name="legal"),
    path("how-it-works/", views.how_it_works, name="how_it_works"),
    path("application-form/", views.download_application_form, name="application_form"),
    # Match accept/decline flow (VERB-19). No @login_required — the signed
    # token IS the authentication for these views.
    path("match/<str:token>/", views.match_detail, name="match"),
    path("match/<str:token>/accept/", views.match_accept, name="match_accept"),
    path("match/<str:token>/decline/", views.match_decline, name="match_decline"),
    # Well-known root requests served to avoid excess 404s (VERB-7).
    path("sw.js", views.service_worker, name="service_worker"),
    path(
        "favicon.ico",
        RedirectView.as_view(url=static("favicon.svg"), permanent=True),
        name="favicon",
    ),
]
