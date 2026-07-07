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
    # NB: the survey submit route must be declared before the <slug:role>
    # pattern below — otherwise "survey" matches as a role slug first and
    # register_done 404s on the unknown role.
    path(
        "register/done/survey/",
        views.register_survey_submit,
        name="register_survey_submit",
    ),
    path("register/done/<slug:role>/", views.register_done, name="register_done"),
    # Paid-tier deposit flow — Stripe hosted Checkout (VERB-86). The webhook
    # itself is mounted un-prefixed in config/urls.py, not here.
    path(
        "register/pay/",
        views.register_payment_start,
        name="register_payment_start",
    ),
    path(
        "register/pay/return/",
        views.register_payment_return,
        name="register_payment_return",
    ),
    path(
        "register/pay/cancelled/",
        views.register_payment_cancelled,
        name="register_payment_cancelled",
    ),
    # Standalone tip (voluntary contribution) page (VERB-110) — built in
    # isolation, not yet mounted in any journey; not linked from any nav.
    path("tip/", views.tip_page, name="tip_page"),
    path("tip/start/", views.tip_start, name="tip_start"),
    path("tip/return/", views.tip_return, name="tip_return"),
    path("tip/cancelled/", views.tip_cancelled, name="tip_cancelled"),
    path("legal/<slug:page>/", views.legal_page, name="legal"),
    path("how-it-works/", views.how_it_works, name="how_it_works"),
    path("faq/", views.faq, name="faq"),
    path("colophon/", views.colophon, name="colophon"),
    path("application-form/", views.download_application_form, name="application_form"),
    # Match accept/decline flow (VERB-19). No @login_required — the signed
    # token IS the authentication for these views.
    path("match/<str:token>/", views.match_detail, name="match"),
    path("match/<str:token>/accept/", views.match_accept, name="match_accept"),
    path("match/<str:token>/withdraw/", views.match_withdraw, name="match_withdraw"),
    path("match/<str:token>/decline/", views.match_decline, name="match_decline"),
    path(
        "match/<str:token>/report-no-show/",
        views.match_report_no_show,
        name="match_report_no_show",
    ),
    # Well-known root requests served to avoid excess 404s (VERB-7).
    path("sw.js", views.service_worker, name="service_worker"),
    path(
        "favicon.ico",
        RedirectView.as_view(url=static("favicon.svg"), permanent=True),
        name="favicon",
    ),
]
