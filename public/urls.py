"""URL routing for the public site (namespace: ``public``)."""

from django.templatetags.static import static
from django.urls import path
from django.views.generic.base import RedirectView

from . import views

app_name = "public"

urlpatterns = [
    path("", views.home, name="home"),
    # Combined single-step registration flow (VERB-24).
    path("register/", views.register, name="register"),
    path("register/sent/", views.register_email_sent, name="register_email_sent"),
    path(
        "register/confirm/<str:token>/",
        views.register_confirm,
        name="register_confirm",
    ),
    path(
        "register/details/form/",
        views.register_details_form,
        name="register_details_form",
    ),
    path("register/done/<slug:role>/", views.register_done, name="register_done"),
    path("legal/<slug:page>/", views.legal_page, name="legal"),
    path("how-it-works/", views.how_it_works, name="how_it_works"),
    path("application-form/", views.download_application_form, name="application_form"),
    # Well-known root requests served to avoid excess 404s (VERB-7).
    path("sw.js", views.service_worker, name="service_worker"),
    path(
        "favicon.ico",
        RedirectView.as_view(url=static("favicon.svg"), permanent=True),
        name="favicon",
    ),
]
