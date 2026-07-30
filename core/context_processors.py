"""Context processors for the core app.

``notifications`` injects the list of currently active, audience-visible
Notification instances into every template context, so the notification
strip renders on any page extending ``templates/base.html`` without each
view passing the data explicitly (VERB-109).

``markdown_alternate`` injects the URL of the page's Markdown representation
so ``_meta.html`` can advertise it with a ``<link rel="alternate">`` tag
(SKI-155).
"""

from __future__ import annotations

from django.http import HttpRequest
from django.utils import timezone

from core.models import Notification
from public.content import page_for_view


def notifications(request: HttpRequest) -> dict[str, list[Notification]]:
    """Return the notifications the current request's user should see.

    Combines the two gating axes — the ``enabled`` kill switch and the
    display window (``active(now)``) — then filters the result in Python by
    ``is_visible_to(request.user)``. Audience membership (especially the
    CUSTOM case) is not practical to express as a single queryset filter, and
    the enabled + active set is expected to be small. A notification shows
    only when it is enabled, within its window, and matches the audience.

    Args:
        request: The current HTTP request.

    Returns:
        A dict with one key, ``active_notifications``, ordered highest
        priority first then newest (``Notification.Meta.ordering``).
    """
    candidates = Notification.objects.enabled().active(timezone.now())
    visible = [n for n in candidates if n.is_visible_to(request.user)]
    return {"active_notifications": visible}


def markdown_alternate(request: HttpRequest) -> dict[str, str]:
    """Return the URL of this page's Markdown twin, if it has one.

    Feeds the ``<link rel="alternate" type="text/markdown">`` tag in
    ``includes/_meta.html``. The HTTP ``Link`` header carrying the same URL is
    set by ``MarkdownRepresentationMiddleware`` — the header cannot serve both
    purposes, because it is added on the response, after the template has
    already rendered.

    Returns an empty dict for any route that is not a curated content page, so
    the tag is simply absent rather than pointing at a 404.

    Args:
        request: The current HTTP request.

    Returns:
        ``{"markdown_url": <absolute url>}`` on a content page, otherwise ``{}``.
    """
    match = request.resolver_match
    if match is None:
        return {}
    page = page_for_view(match.view_name, match.kwargs)
    if page is None:
        return {}
    return {"markdown_url": request.build_absolute_uri(f"/{page.slug}.md")}
