# Static-ish informational pages: the landing page, legal documents, the
# "How it works" / FAQ / colophon / about pages, the application-form
# download redirect, and the no-op service worker.
#
# PostHog analytics (VERB-124): download_application_form fires a best-effort
# form_downloaded event; the anonymous registration success path lives in
# registration.register_form and calls alias_identities so pre-registration
# page-views (anonymous hash) merge into the resulting user in PostHog.
# Server-side page-view tracking for GET requests lives in
# core.middleware.PostHogPageviewMiddleware, not here.

from __future__ import annotations

from django.conf import settings
from django.http import Http404, HttpRequest, HttpResponse
from django.shortcuts import redirect, render

from core.observability import capture_event, distinct_id_for
from matching.services import is_registration_open
from public.models import FormDownload

# The legal documents, keyed by URL slug. Validating against this set keeps
# unknown pages out of the view (404) and out of template lookups.
LEGAL_PAGES = {"privacy", "cookies", "terms"}


def home(request: HttpRequest) -> HttpResponse:
    """Render the public landing page with the two role calls-to-action."""
    return render(
        request,
        "public/home.html",
        {"registration_open": is_registration_open()},
    )


def legal_page(request: HttpRequest, page: str) -> HttpResponse:
    """Render a static legal document (privacy / cookies / terms)."""
    if page not in LEGAL_PAGES:
        raise Http404("Unknown legal page.")
    return render(request, f"public/legal/{page}.html")


def how_it_works(request: HttpRequest) -> HttpResponse:
    """Render the 'How it works' informational page (no queries)."""
    return render(request, "public/how_it_works.html")


def faq(request: HttpRequest) -> HttpResponse:
    """Render the FAQ page (stub — content to be populated; no queries)."""
    return render(request, "public/faq.html")


def colophon(request: HttpRequest) -> HttpResponse:
    """Render the colophon page (technology credits; no queries)."""
    return render(request, "public/colophon.html")


def about(request: HttpRequest) -> HttpResponse:
    """Render the About page (who runs the service, why, and future plans; no
    queries).
    """
    return render(request, "public/about.html")


def download_application_form(request: HttpRequest) -> HttpResponse:
    """Record a form download and redirect to the application-form PDF.

    Creates one FormDownload row per request (the conversion metric) then
    issues a redirect to the externally-hosted PDF (``APPLICATION_FORM_URL``).
    No PII is stored. Additionally sends a best-effort ``form_downloaded``
    analytics event (VERB-124), attributed to the caller's own distinct_id
    (user pk if authenticated, else the anonymous hash).
    """
    FormDownload.objects.create()
    capture_event(distinct_id_for(request), "form_downloaded")
    return redirect(settings.APPLICATION_FORM_URL)


# A no-op service worker served at the origin root so browsers stop 404-ing on
# /sw.js. We intentionally register no fetch/cache handlers (VERB-7).
_SERVICE_WORKER_BODY = "/* 4 Vallées Ambassadors — intentionally minimal. */\n"


def service_worker(request: HttpRequest) -> HttpResponse:
    """Serve a minimal no-op service worker at /sw.js."""
    return HttpResponse(_SERVICE_WORKER_BODY, content_type="application/javascript")
