"""Context processors for the core app.

``notifications`` injects the list of currently active, audience-visible
Notification instances into every template context, so the notification
strip renders on any page extending ``templates/base.html`` without each
view passing the data explicitly (VERB-109).
"""

from __future__ import annotations

from django.http import HttpRequest
from django.utils import timezone

from core.models import Notification


def notifications(request: HttpRequest) -> dict[str, list[Notification]]:
    """Return the notifications the current request's user should see.

    Filters ``Notification.objects.active(now)`` (the display-window
    queryset) in Python by ``is_visible_to(request.user)`` — audience
    membership (especially the CUSTOM case) is not practical to express as a
    single queryset filter, and the active set is expected to be small.

    Args:
        request: The current HTTP request.

    Returns:
        A dict with one key, ``active_notifications``, ordered newest first
        (``Notification.Meta.ordering``).
    """
    active = Notification.objects.active(timezone.now())
    visible = [n for n in active if n.is_visible_to(request.user)]
    return {"active_notifications": visible}
