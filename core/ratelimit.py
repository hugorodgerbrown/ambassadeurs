# Rate-limit response helper shared by the accounts and public apps.
#
# ``rate_limited_response`` returns a 429 response that is HTMX-aware:
# plain requests render the full 429.html page; HTMX requests return a
# minimal translated fragment that the hx-target can display in-place.

import logging

from django.http import HttpRequest, HttpResponse
from django.shortcuts import render
from django.utils.translation import gettext as _

logger = logging.getLogger(__name__)


def rate_limited_response(request: HttpRequest) -> HttpResponse:
    """Return a 429 response appropriate for the request type.

    For HTMX requests a minimal HTML fragment is returned so the caller's
    ``hx-target`` container shows the message without a full page reload.
    For plain HTTP requests the full ``429.html`` page is rendered.

    All user-facing copy is translated (Invariant 8).
    """
    logger.warning("Rate limit exceeded: ip=%s path=%s", _get_ip(request), request.path)
    message = _("Too many attempts. Please wait a moment and try again.")

    if getattr(request, "htmx", False):
        # Return a minimal fragment — status 429 so the caller can inspect
        # it, but small enough to slot into any hx-target container.
        html = f"<p class='text-error'>{message}</p>"
        return HttpResponse(html, status=429)

    return render(request, "429.html", {"message": message}, status=429)


def _get_ip(request: HttpRequest) -> str:
    """Return the best-guess client IP for logging (not for security decisions)."""
    forwarded: str = request.META.get("HTTP_X_FORWARDED_FOR", "")
    if forwarded:
        return forwarded.split(",")[0].strip()
    remote: str = request.META.get("REMOTE_ADDR", "")
    return remote
