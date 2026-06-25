"""DEBUG-only test-data helper views.

Each view is guarded by ``require_debug`` so that every route returns Http404
when ``settings.DEBUG`` is false — the URL conf is always mounted, making the
guard toggle testable via ``override_settings(DEBUG=False)`` without
reimporting the URL configuration.

Available actions:
- ``create_counterpart`` — create the opposite-role Registration for the
  logged-in user; optionally skip PENDING confirmation.
- ``counterpart_accept`` — force the counterpart to accept the current proposed
  match.
- ``counterpart_decline`` — force the counterpart to decline the current
  proposed match.
- ``counterpart_login`` — switch the session to the counterpart's user.
"""

from __future__ import annotations

import logging
import uuid
from typing import cast

from django.contrib.auth import login
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import User
from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST

from accounts.services import send_confirmation_email
from core.decorators import require_debug
from matching.models import Match, Registration
from matching.services import (
    accept_match,
    decline_match,
    register_participant,
)

logger = logging.getLogger(__name__)


def _safe_referer_redirect(
    request: HttpRequest, fallback: str = "accounts:detail"
) -> HttpResponse:
    """Redirect to the HTTP Referer if it is safe, otherwise to ``fallback``.

    Args:
        request: The current HTTP request.
        fallback: Named URL to fall back to when the referer is absent or unsafe.
    """
    referer = request.META.get("HTTP_REFERER", "")
    if referer and url_has_allowed_host_and_scheme(
        url=referer,
        allowed_hosts={request.get_host()},
        require_https=request.is_secure(),
    ):
        return redirect(referer)
    return redirect(fallback)


def _get_active_match(registration: Registration) -> Match | None:
    """Return the active PROPOSED match for ``registration``, or ``None``.

    Args:
        registration: The registration whose proposed match to look up.
    """
    if registration.role == Registration.Role.AMBASSADOR:
        return registration.matches_as_ambassador.proposed().first()
    return registration.matches_as_referee.proposed().first()


def _get_counterpart(match: Match, registration: Registration) -> Registration:
    """Return the other registration in ``match``.

    Args:
        match: A proposed match.
        registration: One side of the match (ambassador or referee).
    """
    if registration.role == Registration.Role.AMBASSADOR:
        return match.referee_registration
    return match.ambassador_registration


@require_POST
@login_required
@require_debug
def create_counterpart(request: HttpRequest) -> HttpResponse:
    """Create a counterpart Registration for the logged-in user's registration.

    The counterpart's role is the opposite of the logged-in user's. The
    ``state`` POST parameter controls whether the new registration enters the
    pool immediately (``WAITING``) or waits for email confirmation (``PENDING``).

    When ``WAITING`` is chosen the matching engine runs synchronously and may
    propose a match between the counterpart and the logged-in user's
    registration (if it is the best-ranked eligible candidate in the pool).

    When ``PENDING`` is chosen the confirmation URL is stashed in the session
    under ``debug_verify_url`` so the panel shortcut link can surface it.
    """
    user = cast(User, request.user)

    try:
        my_registration = Registration.objects.get(user=user)
    except Registration.DoesNotExist:
        logger.warning(
            "create_counterpart: logged-in user pk=%s has no registration", user.pk
        )
        return _safe_referer_redirect(request)

    # Derive the opposite role and appropriate prior_pass.
    if my_registration.role == Registration.Role.AMBASSADOR:
        counterpart_role = Registration.Role.REFEREE
        counterpart_prior_pass = Registration.PriorPass.NONE
    else:
        counterpart_role = Registration.Role.AMBASSADOR
        counterpart_prior_pass = Registration.PriorPass.SEASONAL

    state = request.POST.get("state", Registration.Status.WAITING)
    if state not in (Registration.Status.WAITING, Registration.Status.PENDING):
        state = Registration.Status.WAITING

    # Generate a unique synthetic identity for the counterpart.
    uid = uuid.uuid4().hex[:8]
    first_name = "Debug"
    last_name = f"Counterpart-{uid}"
    email = f"debug-counterpart-{uid}@example.com"

    counterpart = register_participant(
        role=counterpart_role,
        first_name=first_name,
        last_name=last_name,
        prior_pass=counterpart_prior_pass,
        email=email,
        preferred_language="en",
        status=state,
    )

    logger.info(
        "create_counterpart: pk=%s role=%s status=%s for user pk=%s",
        counterpart.pk,
        counterpart_role,
        state,
        user.pk,
    )

    if state == Registration.Status.PENDING:
        confirm_url = send_confirmation_email(request, counterpart)
        request.session["debug_verify_url"] = confirm_url
        logger.info(
            "create_counterpart: stashed confirm URL for counterpart pk=%s",
            counterpart.pk,
        )

    return _safe_referer_redirect(request)


@require_POST
@login_required
@require_debug
def counterpart_accept(request: HttpRequest) -> HttpResponse:
    """Force the counterpart to accept the current proposed match.

    Looks up the logged-in user's proposed match, identifies the counterpart
    registration, and calls ``accept_match`` on their behalf.
    """
    user = cast(User, request.user)

    try:
        my_registration = Registration.objects.get(user=user)
    except Registration.DoesNotExist:
        logger.warning("counterpart_accept: user pk=%s has no registration", user.pk)
        return _safe_referer_redirect(request)

    match = _get_active_match(my_registration)
    if match is None:
        logger.warning(
            "counterpart_accept: no proposed match found for registration pk=%s",
            my_registration.pk,
        )
        return _safe_referer_redirect(request)

    counterpart = _get_counterpart(match, my_registration)
    accept_match(match, counterpart)
    logger.info(
        "counterpart_accept: match pk=%s accepted on behalf of counterpart pk=%s",
        match.pk,
        counterpart.pk,
    )

    return _safe_referer_redirect(request)


@require_POST
@login_required
@require_debug
def counterpart_decline(request: HttpRequest) -> HttpResponse:
    """Force the counterpart to decline the current proposed match.

    Looks up the logged-in user's proposed match, identifies the counterpart
    registration, and calls ``decline_match`` on their behalf.
    """
    user = cast(User, request.user)

    try:
        my_registration = Registration.objects.get(user=user)
    except Registration.DoesNotExist:
        logger.warning("counterpart_decline: user pk=%s has no registration", user.pk)
        return _safe_referer_redirect(request)

    match = _get_active_match(my_registration)
    if match is None:
        logger.warning(
            "counterpart_decline: no proposed match found for registration pk=%s",
            my_registration.pk,
        )
        return _safe_referer_redirect(request)

    counterpart = _get_counterpart(match, my_registration)
    decline_match(match, counterpart)
    logger.info(
        "counterpart_decline: match pk=%s declined on behalf of counterpart pk=%s",
        match.pk,
        counterpart.pk,
    )

    return _safe_referer_redirect(request)


@require_POST
@login_required
@require_debug
def counterpart_login(request: HttpRequest) -> HttpResponse:
    """Switch the current session to the counterpart's user.

    Logs out the current user and logs in as the counterpart — useful for
    inspecting the other side of a proposed match without opening a second
    browser. Redirects to ``accounts:match`` when the counterpart has a
    proposed match, otherwise to ``accounts:detail``.
    """
    user = cast(User, request.user)

    try:
        my_registration = Registration.objects.select_related("user").get(user=user)
    except Registration.DoesNotExist:
        logger.warning("counterpart_login: user pk=%s has no registration", user.pk)
        return _safe_referer_redirect(request)

    match = _get_active_match(my_registration)
    if match is None:
        logger.warning(
            "counterpart_login: no proposed match for registration pk=%s",
            my_registration.pk,
        )
        return _safe_referer_redirect(request)

    counterpart = _get_counterpart(match, my_registration)
    counterpart_user = counterpart.user

    login(
        request, counterpart_user, backend="django.contrib.auth.backends.ModelBackend"
    )
    logger.info(
        "counterpart_login: session switched from user pk=%s to counterpart user pk=%s",
        user.pk,
        counterpart_user.pk,
    )

    # Redirect to the match page if the counterpart has a proposed match,
    # otherwise to the account detail page.
    counterpart_match = _get_active_match(counterpart)
    if counterpart_match is not None:
        return redirect("accounts:match")
    return redirect("accounts:detail")
