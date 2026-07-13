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
from datetime import datetime, timedelta
from typing import cast

from django.contrib.auth import login
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import User
from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect, render
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST

from accounts.services import send_confirmation_email
from core.decorators import require_debug
from matching.models import Match, Registration
from matching.selectors import build_queue_context, status_pill_for
from matching.services import (
    accept_match,
    decline_match,
    register_participant,
)
from public.views import _match_context

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
    """Return the active non-terminal match for ``registration``, or ``None``.

    Considers PROPOSED and PENDING matches (both are active, non-terminal).

    Args:
        registration: The registration whose active match to look up.
    """
    if registration.role == Registration.Role.AMBASSADOR:
        return registration.matches_as_ambassador.filter(
            status__in=[Match.Status.PROPOSED, Match.Status.PENDING]
        ).first()
    return registration.matches_as_referee.filter(
        status__in=[Match.Status.PROPOSED, Match.Status.PENDING]
    ).first()


def _get_counterpart(match: Match, registration: Registration) -> Registration:
    """Return the other registration in ``match``.

    Both FKs are non-null on PROPOSED matches (the only state this is called
    from); assertions satisfy mypy's nullability check.

    Args:
        match: A proposed match.
        registration: One side of the match (ambassador or referee).
    """
    assert match.ambassador_registration is not None
    assert match.referee_registration is not None
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
    pool immediately (``VERIFIED``) or waits for email confirmation
    (``UNVERIFIED``).

    When ``VERIFIED`` is chosen the matching engine runs synchronously and may
    propose a match between the counterpart and the logged-in user's
    registration (if it is the best-ranked eligible candidate in the pool).

    When ``UNVERIFIED`` is chosen the confirmation URL is stashed in the session
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

    state = request.POST.get("state", Registration.Status.VERIFIED)
    if state not in (Registration.Status.VERIFIED, Registration.Status.UNVERIFIED):
        state = Registration.Status.VERIFIED

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

    if state == Registration.Status.UNVERIFIED:
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


# ---------------------------------------------------------------------------
# Match-page state preview (visual QA of every match.html combination)
# ---------------------------------------------------------------------------

# The match page's design views, in display order, with a human label for the
# state-switcher pills. The viewer is modelled as the Referee, so the partner is
# the Ambassador ("Léa") — mirroring the design handoff's single perspective.
_PREVIEW_VIEWS: list[tuple[str, str]] = [
    ("proposed", "Proposed"),
    ("you_accepted", "You accepted"),
    ("partner_accepted", "Partner accepted"),
    ("confirmed", "Confirmed"),
    ("declined_you", "You declined"),
    ("declined_partner", "Partner declined"),
    ("expired", "Expired"),
    ("cancelled_you", "No-show reported"),
    ("cancelled_partner", "Reported (you)"),
]


def _build_preview_match(view_key: str) -> tuple[Match, Registration, Match.Side]:
    """Build unsaved in-memory objects so ``MatchDisplay`` derives ``view_key``.

    Returns ``(match, registration, side)`` for the Referee's perspective. The
    objects are never saved; they only need the fields the match page reads
    (names, role, phone/email for the confirmed contact card, the response
    timestamps and status that drive the derived view, and ``expires_at`` for
    the deadline strip). Synthetic primary keys are set so the foreign-key
    descriptors return the cached instances without a database query.
    """
    now = timezone.now()

    ambassador_user = User(
        first_name="Léa",
        last_name="Maret",
        email="lea.maret@example.com",
    )
    ambassador_user.pk = 9001
    referee_user = User(
        first_name="Sam",
        last_name="Visitor",
        email="sam.visitor@example.com",
    )
    referee_user.pk = 9002

    ambassador_reg = Registration(
        user=ambassador_user,
        role=Registration.Role.AMBASSADOR,
        phone="+41 79 482 16 03",
        prior_pass=Registration.PriorPass.SEASONAL,
    )
    ambassador_reg.pk = 8001
    referee_reg = Registration(
        user=referee_user,
        role=Registration.Role.REFEREE,
        phone="+41 79 111 22 33",
        prior_pass=Registration.PriorPass.NONE,
    )
    referee_reg.pk = 8002

    match = Match(
        ambassador_registration=ambassador_reg,
        referee_registration=referee_reg,
        status=Match.Status.PROPOSED,
        expires_at=now + timedelta(days=2),
    )
    match.pk = 7001

    if view_key == "you_accepted":
        match.referee_accepted_at = now
    elif view_key == "partner_accepted":
        match.ambassador_accepted_at = now
    elif view_key == "confirmed":
        match.status = Match.Status.ACCEPTED
        match.ambassador_accepted_at = now
        match.referee_accepted_at = now
    elif view_key == "declined_you":
        match.status = Match.Status.DECLINED
        match.declined_by = Match.Side.REFEREE
        match.declined_at = now
    elif view_key == "declined_partner":
        # VERB-74: the partner declined and is now PAUSED (not deleted); the
        # FK is retained, so partner_name comes from the registration as usual.
        match.status = Match.Status.DECLINED
        match.declined_by = Match.Side.AMBASSADOR
        match.declined_at = now
    elif view_key == "expired":
        match.status = Match.Status.EXPIRED
        match.expires_at = now - timedelta(days=1)
    elif view_key == "cancelled_you":
        match.status = Match.Status.CANCELLED
        match.ambassador_accepted_at = now
        match.referee_accepted_at = now
        match.no_show_reported_by = Match.Side.REFEREE
        match.no_show_reported_at = now
    elif view_key == "cancelled_partner":
        match.status = Match.Status.CANCELLED
        match.ambassador_accepted_at = now
        match.referee_accepted_at = now
        match.no_show_reported_by = Match.Side.AMBASSADOR
        match.no_show_reported_at = now

    return match, referee_reg, Match.Side.REFEREE


@require_debug
def match_preview(request: HttpRequest) -> HttpResponse:
    """Render ``public/match.html`` in a forced state for visual QA.

    DEBUG-only. The ``view`` query parameter selects one of the match page's
    design states (defaulting to ``proposed`` when absent or unknown); the page
    is rendered through the production ``_match_context`` with synthetic,
    unsaved objects, so the preview is the real page — not a mock. A state
    switcher (the ``preview_states`` context) is shown above the card so every
    combination can be flipped through. The accept/decline forms are inert here
    (no token is minted), since the purpose is visual review only.
    """
    view_key = request.GET.get("view", "proposed")
    if view_key not in {key for key, _ in _PREVIEW_VIEWS}:
        view_key = "proposed"

    match, registration, side = _build_preview_match(view_key)
    context = _match_context(match, registration, side, token=None)
    context["preview_states"] = [
        {"key": key, "label": label, "current": key == view_key}
        for key, label in _PREVIEW_VIEWS
    ]
    return render(request, "public/match.html", context)


# ---------------------------------------------------------------------------
# Component gallery (visual QA of the account Match status panel)
# ---------------------------------------------------------------------------


def _match_status_scenario(
    label: str,
    *,
    status: str | None,
    match_state: str = "none",
    partner_first_name: str = "",
    partner_accepted: bool = False,
    queue_position: int | None = None,
    total_accepted_matches: int = 0,
    can_rejoin: bool = False,
    can_cancel: bool = False,
) -> dict[str, object]:
    """Build one labelled render-context for the Match status partial.

    ``status`` is a ``Registration.Status`` value, or ``None`` for the
    no-registration case. ``match_state`` is one of ``none``, ``proposed``,
    ``pending``, ``accepted`` — derived from the active match in the real view
    (VERB-44). The Registration is unsaved — the partial only reads its
    ``status``/``get_status_display`` — and ``status_pill`` is derived the same
    way the real view derives it (``status_pill_for``).

    ``can_rejoin`` mirrors the context variable injected by ``account_detail``
    for the PAUSED state (VERB-74). ``can_cancel`` mirrors the equivalent flag
    for the "Cancel & refund" link (VERB-88).
    """
    registration = (
        None
        if status is None
        else Registration(role=Registration.Role.REFEREE, status=status)
    )
    return {
        "label": label,
        "registration": registration,
        "status_pill": status_pill_for(registration, match_state),
        "match_state": match_state,
        "partner_first_name": partner_first_name,
        "partner_accepted": partner_accepted,
        "queue_position": queue_position,
        "total_accepted_matches": total_accepted_matches,
        "can_rejoin": can_rejoin,
        "can_cancel": can_cancel,
    }


def _queue_scenario(
    label: str,
    *,
    is_open: bool,
    ambassadors: int,
    referees: int,
    matches: int,
    opens_at: datetime | None = None,
    days_until_open: int = 0,
    you_role: str = "",
    you_index: int | None = None,
) -> dict[str, object]:
    """Build one labelled synthetic queue-visualisation context for the gallery.

    Delegates to ``build_queue_context`` — the same shaper the live view uses —
    so the gallery renders the real component, not a mock. ``opens_at`` is only
    read in the pre-open (``is_open=False``) subheader; a fixed date keeps the
    gallery deterministic.

    Args:
        label: The human caption shown above the rendered component.
        is_open: Whether matching has begun in this scenario.
        ambassadors: Waiting ambassador count.
        referees: Waiting referee count.
        matches: Active-match (pair) count.
        opens_at: The matching open instant (pre-open scenarios only).
        days_until_open: Whole-day countdown shown pre-open.
        you_role: Role of the current user for the "you" highlight, or "".
        you_index: Zero-based position of the current user in that role's column.
    """
    queue = build_queue_context(
        ambassadors_waiting=ambassadors,
        referees_waiting=referees,
        matches=matches,
        is_open=is_open,
        opens_at=opens_at or timezone.make_aware(datetime(2026, 10, 1)),
        days_until_open=days_until_open,
        you_role=you_role,
        you_index=you_index,
    )
    return {"label": label, "queue": queue}


@require_debug
def components(request: HttpRequest) -> HttpResponse:
    """Render the account Match status panel in every combination (DEBUG-only).

    A component gallery: each scenario is the real partial
    (``accounts/partials/match_status.html``) rendered with synthetic context,
    so the page is the live component — not a mock. Covers every
    Registration.Status, all match_state variants, the two VERIFIED
    queue-position variants, and the no-registration case.

    This same partial — the whole card, not just the pill — is now also
    rendered in full on the registration-confirmation page
    (``public/register_done.html``, VERB-116), so the gallery below doubles as
    the visual review surface for both.
    """
    scenarios = [
        _match_status_scenario("No registration", status=None),
        _match_status_scenario(
            "Email unconfirmed (UNVERIFIED)", status=Registration.Status.UNVERIFIED
        ),
        _match_status_scenario(
            "In the queue — no position", status=Registration.Status.VERIFIED
        ),
        _match_status_scenario(
            "In the queue — with position",
            status=Registration.Status.VERIFIED,
            queue_position=3,
            total_accepted_matches=5,
        ),
        _match_status_scenario(
            "Proposed — partner not responded",
            status=Registration.Status.VERIFIED,
            match_state="proposed",
            partner_first_name="Bernard",
        ),
        _match_status_scenario(
            "Proposed — partner waiting on you",
            status=Registration.Status.VERIFIED,
            match_state="proposed",
            partner_first_name="Bernard",
            partner_accepted=True,
        ),
        _match_status_scenario(
            "Pending — partner not responded",
            status=Registration.Status.VERIFIED,
            match_state="pending",
            partner_first_name="Bernard",
        ),
        _match_status_scenario(
            "Pending — partner waiting on you",
            status=Registration.Status.VERIFIED,
            match_state="pending",
            partner_first_name="Bernard",
            partner_accepted=True,
        ),
        _match_status_scenario(
            "Accepted (both parties)",
            status=Registration.Status.VERIFIED,
            match_state="accepted",
            partner_first_name="Bernard",
        ),
        _match_status_scenario(
            "Paused (can rejoin / cancel)",
            status=Registration.Status.PAUSED,
            can_rejoin=True,
            can_cancel=True,
        ),
        _match_status_scenario("Withdrawn", status=Registration.Status.WITHDRAWN),
        _match_status_scenario("Suspended", status=Registration.Status.SUSPENDED),
    ]
    queue_scenarios = [
        _queue_scenario(
            "Pre-match — registration open, matching not started",
            is_open=False,
            ambassadors=6,
            referees=4,
            matches=0,
            days_until_open=83,
        ),
        _queue_scenario(
            "Live — referees matched instantly (ambassadors queue)",
            is_open=True,
            ambassadors=5,
            referees=0,
            matches=3,
        ),
        _queue_scenario(
            "Live — ambassadors matched instantly (referees queue)",
            is_open=True,
            ambassadors=0,
            referees=5,
            matches=3,
        ),
        _queue_scenario(
            "Live — large pool (grid caps, trailing ellipsis)",
            is_open=True,
            ambassadors=200,
            referees=0,
            matches=60,
        ),
        _queue_scenario(
            "Live — you are in the queue (ambassador, position 3)",
            is_open=True,
            ambassadors=6,
            referees=4,
            matches=3,
            you_role="ambassador",
            you_index=2,
        ),
    ]
    return render(
        request,
        "debug/components.html",
        {"scenarios": scenarios, "queue_scenarios": queue_scenarios},
    )
