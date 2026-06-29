# Core views shared across the project.
#
# Currently holds utility views that do not belong to any single domain app:
#   - robots_txt: serves /robots.txt as text/plain, dynamically building the
#     Sitemap absolute URL from the request host so it works on every
#     deployment (local dev, staging, production) without configuration.

import logging

from django.http import HttpRequest, HttpResponse
from django.views.decorators.http import require_GET

logger = logging.getLogger(__name__)


@require_GET
def robots_txt(request: HttpRequest) -> HttpResponse:
    """Serve /robots.txt as plain text.

    Allows all public content pages (home, how-it-works, faq, legal) and
    disallows authenticated / partial routes so crawlers do not index private
    or machine-facing endpoints.

    The Sitemap line uses ``request.build_absolute_uri`` so the host is always
    correct regardless of the deployment environment.

    Args:
        request: The incoming HTTP request.

    Returns:
        An ``HttpResponse`` with content-type ``text/plain`` containing the
        robots.txt directives.
    """
    sitemap_url = request.build_absolute_uri("/sitemap.xml")
    body = (
        "User-agent: *\n"
        "Disallow: /account/\n"
        "Disallow: /admin/\n"
        "Disallow: /match/\n"
        "Disallow: /register/confirm/\n"
        f"Sitemap: {sitemap_url}\n"
    )
    return HttpResponse(body, content_type="text/plain")
