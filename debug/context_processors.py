"""Context processors for the debug app.

``debug_panel`` injects the logged-in user's Registration, their active
proposed Match, and the counterpart Registration into every template context
when ``settings.DEBUG`` is true. The panel template uses these to render its
control forms on every page without an extra database query per view.
"""

from __future__ import annotations

import logging

from django.conf import settings
from django.http import HttpRequest

from matching.models import Match, Registration

logger = logging.getLogger(__name__)


def debug_panel(request: HttpRequest) -> dict[str, object]:
    """Return debug-panel context variables when ``settings.DEBUG`` is true.

    Returns an empty dict in production so the processor is a no-op.

    Populated keys:
        ``debug_registration``: the logged-in user's ``Registration``, or
            ``None`` if they have no registration or are anonymous.
        ``debug_match``: the active PROPOSED ``Match`` for that registration,
            or ``None``.
        ``debug_counterpart``: the other ``Registration`` in the proposed
            match, or ``None``.

    Args:
        request: The current HTTP request.
    """
    if not settings.DEBUG:
        return {}

    if not request.user.is_authenticated:
        return {
            "debug_registration": None,
            "debug_match": None,
            "debug_counterpart": None,
        }

    try:
        registration = Registration.objects.select_related("user").get(
            user=request.user
        )
    except Registration.DoesNotExist:
        return {
            "debug_registration": None,
            "debug_match": None,
            "debug_counterpart": None,
        }

    # Look up the active proposed match for this registration.
    match: Match | None
    if registration.role == Registration.Role.AMBASSADOR:
        match = registration.matches_as_ambassador.proposed().first()
    else:
        match = registration.matches_as_referee.proposed().first()

    counterpart: Registration | None = None
    if match is not None:
        if registration.role == Registration.Role.AMBASSADOR:
            counterpart = match.referee_registration
        else:
            counterpart = match.ambassador_registration

    return {
        "debug_registration": registration,
        "debug_match": match,
        "debug_counterpart": counterpart,
    }
