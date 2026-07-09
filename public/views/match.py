# Match accept/decline/report-no-show flow (VERB-19/VERB-21).
#
# A signed email link carries the participant to /match/<token>/ where they
# can accept or decline. No @login_required — the signed token IS the
# authentication. HTMX partial views for accept/decline are guarded with
# require_htmx (Invariant 7). Contact PII is revealed ONLY when
# match.status == ACCEPTED (Invariant 1).
#
# Withdraw acceptance (VERB-43 / ADR 0010): while a match is PENDING (one side
# accepted), the side that already accepted may retract via match_withdraw
# (@require_htmx) — a clean no-penalty un-accept (PENDING → PROPOSED) that
# returns them to the actionable proposed view.
#
# No-show reporting (VERB-21): once a match is ACCEPTED, either party may
# report the other as a post-accept no-show via
# match_report_no_show (@require_htmx) or the no-JS POST fallback in
# match_detail. The report transitions the match to CANCELLED, suspends the
# accused, and re-queues the reporter to the front of the pool.
#
# Wrong-user journey (VERB-32): match_detail GET branches on the auth state of
# the viewer. Anonymous → token auth (existing behaviour). Authenticated
# participant → own-side view (via match.side_of). Authenticated
# non-participant → 403 with match_forbidden.html. The shared
# _render_match_page helper is importable by accounts.views so that the
# tokenless accounts:match route can render the identical match.html.

from __future__ import annotations

from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from accounts.tokens import read_match_access_token
from core.decorators import require_htmx
from core.exceptions import StateTransitionError
from matching.models import Match, Registration
from matching.services import (
    accept_match,
    decline_match,
    report_no_show,
    withdraw_acceptance,
)

from ._shared import _authenticated_registration

# Display states for the match page — computed from match status + window.
_STATE_ACTIONABLE = "actionable"  # PROPOSED, within window, this side not yet responded
_STATE_WAITING = "waiting"  # PENDING and this side already accepted
_STATE_TERMINAL = (
    "terminal"  # ACCEPTED / DECLINED / EXPIRED / CANCELLED, or window lapsed
)


def _resolve_match_token(
    token: str,
) -> tuple[Match, Registration, Match.Side] | None:
    """Validate a match-access token and return the relevant domain objects.

    Returns ``(match, registration, side)`` on success, or ``None`` if the
    token is invalid, expired, or the registration is not a party on the match.
    The match is loaded with its two registrations selected-related.
    """
    parsed = read_match_access_token(token)
    if parsed is None:
        return None
    match_pk, registration_pk = parsed

    try:
        match = Match.objects.select_related(
            "ambassador_registration__user",
            "referee_registration__user",
        ).get(pk=match_pk)
    except Match.DoesNotExist:
        return None

    # Confirm the token's registration_pk is one of the two parties.
    if registration_pk not in (
        match.ambassador_registration_id,
        match.referee_registration_id,
    ):
        return None

    # Identify which registration the token is for.
    if registration_pk == match.ambassador_registration_id:
        registration = match.ambassador_registration
        side = Match.Side.AMBASSADOR
    else:
        registration = match.referee_registration
        side = Match.Side.REFEREE

    return match, registration, side


def _compute_match_display_state(match: Match, side: Match.Side) -> str:
    """Return the display state for a match page, from the viewer's perspective.

    Returns one of the module-level ``_STATE_*`` constants.

    PROPOSED and PENDING are both "active" states — neither is terminal. A
    PENDING match has one acceptance already recorded; if this side accepted, the
    viewer is in the _STATE_WAITING sub-state (waiting on the partner). If the
    *other* side accepted (and match is PENDING), this side hasn't responded yet
    so the viewer is still _STATE_ACTIONABLE.
    """
    if match.status not in (Match.Status.PROPOSED, Match.Status.PENDING):
        return _STATE_TERMINAL
    if timezone.now() > match.expires_at:
        return _STATE_TERMINAL
    # Active match within the window. Check if this side has accepted.
    if side == Match.Side.AMBASSADOR and match.ambassador_accepted_at is not None:
        return _STATE_WAITING
    if side == Match.Side.REFEREE and match.referee_accepted_at is not None:
        return _STATE_WAITING
    return _STATE_ACTIONABLE


def _side_accepted(match: Match, side: Match.Side) -> bool:
    """Return whether the given side has recorded an acceptance on the match."""
    if side == Match.Side.AMBASSADOR:
        return match.ambassador_accepted_at is not None
    return match.referee_accepted_at is not None


def _other_side(side: Match.Side) -> Match.Side:
    """Return the opposite side of a match."""
    if side == Match.Side.AMBASSADOR:
        return Match.Side.REFEREE
    return Match.Side.AMBASSADOR


def _match_view(match: Match, side: Match.Side) -> str:
    """Return the design view key for the match page, from the viewer's side.

    One of ``proposed``, ``you_accepted``, ``partner_accepted``, ``confirmed``,
    ``declined_you``, ``declined_partner``, ``expired``, ``cancelled_you``,
    ``cancelled_partner``. A PROPOSED or PENDING match whose contact window has
    lapsed is presented as ``expired`` — both parties re-queue, the same outcome
    as a swept expiry — so the page need not distinguish the two.

    This drives only presentation (header copy + outcome block); the action
    guards use ``_compute_match_display_state``.
    """
    status = match.status
    if status == Match.Status.ACCEPTED:
        return "confirmed"
    if status == Match.Status.DECLINED:
        return "declined_you" if match.declined_by == side else "declined_partner"
    if status == Match.Status.EXPIRED:
        return "expired"
    if status == Match.Status.CANCELLED:
        return (
            "cancelled_you"
            if match.no_show_reported_by == side
            else "cancelled_partner"
        )
    # PROPOSED or PENDING — distinguish by window and per-side acceptance.
    if timezone.now() > match.expires_at:
        return "expired"
    if _side_accepted(match, side):
        return "you_accepted"
    if _side_accepted(match, _other_side(side)):
        return "partner_accepted"
    return "proposed"


def _side_status_key(match: Match, side: Match.Side) -> str:
    """Return the roster pill key for one side.

    One of ``accepted`` / ``declined`` / ``no_response`` / ``pending``. Derived
    from the match alone (viewer-independent) so both roster rows read true.
    """
    if match.status == Match.Status.ACCEPTED or _side_accepted(match, side):
        return "accepted"
    if match.declined_by == side:
        return "declined"
    if match.status == Match.Status.EXPIRED or (
        match.status in (Match.Status.PROPOSED, Match.Status.PENDING)
        and timezone.now() > match.expires_at
    ):
        return "no_response"
    return "pending"


def _roster_row(
    registration: Registration | None,
    role_side: Match.Side,
    viewer_side: Match.Side,
    match: Match,
) -> dict[str, object]:
    """Build one roster row's display data.

    Reveals the party's first name, initials and nationality (the match redesign
    shows these from the proposed state; contact PII — email and phone — stays
    hidden until mutual accept, see Invariant 1). ``registration`` is ``None``
    only when the party's account has since been deleted — a decline no longer
    deletes it (the decliner is paused, VERB-74 / ADR 0013) — in which case the
    template falls back to a generic label.
    """
    name = ""
    initials = ""
    nationality: object = ""
    if registration is not None:
        first = registration.user.first_name or ""
        last = registration.user.last_name or ""
        name = first
        initials = (first[:1] + last[:1]).upper()
        nationality = registration.nationality
    return {
        "side": role_side,
        "name": name,
        "initials": initials,
        "nationality": nationality,
        "exists": registration is not None,
        "is_you": role_side == viewer_side,
        "status": _side_status_key(match, role_side),
    }


def _related_registration(match: Match, attr: str) -> Registration | None:
    """Return ``match.<attr>`` (a Registration FK), or None if the row is gone.

    The FK is non-nullable (CASCADE), so a freshly-loaded match normally
    resolves both sides; a decline no longer deletes the Registration — the
    decliner is paused (VERB-74 / ADR 0013). This guard is defensive: if the
    referenced Registration has since been deleted (e.g. via account deletion)
    the lazy load raises ``Registration.DoesNotExist``; treat that as None.
    """
    try:
        related: Registration | None = getattr(match, attr)
    except Registration.DoesNotExist:
        return None
    return related


def _match_context(
    match: Match,
    registration: Registration,
    side: Match.Side,
    *,
    token: str | None = None,
) -> dict[str, object]:
    """Build the shared context for the match page and its action partial.

    Used by ``_render_match_page`` (full page) and the HTMX action endpoints
    (which render only ``public/partials/match_actions.html``). When ``token`` is
    provided the HTMX action URLs are included; otherwise they are omitted (the
    tokenless account route does not expose the action forms).

    The counterpart's contact details (email, phone) are included ONLY when
    ``match.status == ACCEPTED`` (Invariant 1); the first name is revealed
    earlier via ``partner_name`` and the roster.
    """
    # Resolve both sides defensively. The FKs are non-nullable (CASCADE), so a
    # freshly-loaded match normally has both registrations; a decline no longer
    # deletes either (the decliner is paused, VERB-74 / ADR 0013). The guard
    # covers the residual case where a registration has been deleted (e.g.
    # account deletion): the lazy load raises DoesNotExist, which
    # _related_registration treats as None.
    ambassador_reg = _related_registration(match, "ambassador_registration")
    referee_reg = _related_registration(match, "referee_registration")
    counterpart = referee_reg if side == Match.Side.AMBASSADOR else ambassador_reg
    view = _match_view(match, side)
    context: dict[str, object] = {
        "match": match,
        "registration": registration,
        "side": side,
        "view": view,
        "roster": [
            _roster_row(ambassador_reg, Match.Side.AMBASSADOR, side, match),
            _roster_row(referee_reg, Match.Side.REFEREE, side, match),
        ],
        "partner_name": (
            counterpart.user.first_name if counterpart is not None else ""
        ),
        "show_deadline": view in ("proposed", "you_accepted", "partner_accepted"),
    }
    if token is not None:
        context["accept_url"] = reverse("public:match_accept", args=[token])
        context["decline_url"] = reverse("public:match_decline", args=[token])
        context["withdraw_url"] = reverse("public:match_withdraw", args=[token])
        context["report_no_show_url"] = reverse(
            "public:match_report_no_show", args=[token]
        )
    # Reveal counterpart PII ONLY when both parties have accepted (Invariant 1).
    if match.status == Match.Status.ACCEPTED:
        context["counterpart"] = counterpart
    return context


def _render_match_page(
    request: HttpRequest,
    match: Match,
    registration: Registration,
    side: Match.Side,
    *,
    token: str | None = None,
) -> HttpResponse:
    """Build the match-page context and return the rendered ``public/match.html``.

    Shared by ``match_detail`` (token route) and ``accounts.views.account_match``
    (tokenless, login-required route). When ``token`` is provided the HTMX action
    URLs are included; when it is ``None`` they are omitted (the tokenless route
    does not expose action partials).
    """
    context = _match_context(match, registration, side, token=token)
    return render(request, "public/match.html", context)


def match_detail(request: HttpRequest, token: str) -> HttpResponse:
    """Render the match page (GET) or handle the no-JS POST fallback.

    No ``@login_required`` — the signed token authenticates the viewer. Token
    read failure or an unrelated registration → 400 with the invalid template.

    GET: render the full ``public/match.html`` page with display state and,
    only when ``match.status == ACCEPTED``, the counterpart's contact details.
    The auth branch (VERB-32) applies:
    - Anonymous → token side (existing behaviour).
    - Authenticated participant (one of the two parties) → own side via
      ``match.side_of(registration)`` so they always see their own perspective.
    - Authenticated non-participant → 403 with ``match_forbidden.html``.

    POST (no-JS fallback): ``action=accept|decline`` → call the relevant service
    → PRG redirect back to this view. The window and status are re-checked before
    calling the service to avoid a double-submit 500.
    """
    resolved = _resolve_match_token(token)
    if resolved is None:
        return render(request, "public/match_invalid.html", status=400)

    match, token_registration, token_side = resolved

    if request.method == "POST":
        # POST always uses token authentication (the no-JS form carries the token
        # in the URL). The display_state check is against the token side.
        display_state = _compute_match_display_state(match, token_side)
        action = request.POST.get("action")
        if action in ("accept", "decline") and display_state == _STATE_ACTIONABLE:
            try:
                if action == "accept":
                    accept_match(match, token_registration)
                else:
                    decline_match(match, token_registration)
                    # After a successful decline the decliner's registration is
                    # paused (VERB-74 / ADR 0013), not deleted. Render the
                    # paused-confirmation page (match_removed.html) directly
                    # rather than PRG-redirecting back to the match view.
                    return render(request, "public/match_removed.html")
            except StateTransitionError, ValueError:
                # Match status changed between read and action (accept raises
                # StateTransitionError, decline raises ValueError); fall
                # through to PRG redirect so the updated state is displayed.
                pass
        elif (
            action == "report_no_show"
            and match.status == Match.Status.ACCEPTED
            and not match.no_show_reported_by
        ):
            try:
                report_no_show(match, token_registration)
            except ValueError:
                # Match status changed or already reported; fall through.
                pass
        # PRG: redirect back to the match page after POST.
        return redirect(reverse("public:match", args=[token]))

    # GET — auth branch (VERB-32).
    auth_registration = _authenticated_registration(request)
    if not request.user.is_authenticated:
        # Anonymous: render from the token's side (existing behaviour).
        return _render_match_page(
            request, match, token_registration, token_side, token=token
        )

    # Authenticated. Check whether this user is a party on the match.
    if auth_registration is not None and auth_registration.pk in (
        match.ambassador_registration_id,
        match.referee_registration_id,
    ):
        # Authenticated participant: render from their own side.
        own_side = match.side_of(auth_registration)
        return _render_match_page(
            request, match, auth_registration, own_side, token=token
        )

    # Authenticated non-participant (wrong user or no registration).
    return render(request, "public/match_forbidden.html", status=403)


@require_htmx
@require_POST
def match_accept(request: HttpRequest, token: str) -> HttpResponse:
    """HTMX POST: accept the match and return the updated actions partial.

    Guarded by ``@require_htmx`` (Invariant 7) and ``@require_POST`` — a GET,
    even with the HX header, must not trigger the accept transition. Re-validates
    the token, confirms the match is still PROPOSED and within the window, then
    calls ``accept_match``. Renders ``public/partials/match_actions.html``
    reflecting the resulting state.
    """
    resolved = _resolve_match_token(token)
    if resolved is None:
        return HttpResponse(status=400)

    match, registration, side = resolved
    display_state = _compute_match_display_state(match, side)

    if display_state == _STATE_ACTIONABLE:
        try:
            match = accept_match(match, registration)
        except StateTransitionError:
            # Status changed between resolution and action; re-render current state.
            pass

    context = _match_context(match, registration, side, token=token)
    return render(request, "public/partials/match_actions.html", context)


@require_htmx
@require_POST
def match_withdraw(request: HttpRequest, token: str) -> HttpResponse:
    """HTMX POST: withdraw this side's acceptance and return the actions partial.

    Guarded by ``@require_htmx`` (Invariant 7) and ``@require_POST`` — a GET,
    even with the HX header, must not retract an acceptance. Re-validates the
    token, confirms this side is in the WAITING (already-accepted) display state,
    then calls ``withdraw_acceptance``. The side returns to the actionable
    ``proposed`` view. Renders ``public/partials/match_actions.html`` reflecting
    the resulting state.

    A POST once the state is no longer WAITING (e.g. the partner accepted and the
    match is now ACCEPTED) is a safe no-op: the guard skips the service call and
    the partial re-renders with the current state.
    """
    resolved = _resolve_match_token(token)
    if resolved is None:
        return HttpResponse(status=400)

    match, registration, side = resolved
    display_state = _compute_match_display_state(match, side)

    if display_state == _STATE_WAITING:
        try:
            match = withdraw_acceptance(match, registration)
        except ValueError:
            # Status changed between resolution and action; re-render current state.
            pass

    context = _match_context(match, registration, side, token=token)
    return render(request, "public/partials/match_actions.html", context)


@require_htmx
@require_POST
def match_decline(request: HttpRequest, token: str) -> HttpResponse:
    """HTMX POST: decline the match and return the updated actions partial.

    Guarded by ``@require_htmx`` (Invariant 7) and ``@require_POST`` — decline
    is a state mutation (it pauses the decliner's registration, VERB-74 /
    ADR 0013) so a GET, even with the HX header, must not trigger it.
    Re-validates the token, confirms the match is
    still PROPOSED and within the window, then calls ``decline_match``. Renders
    ``public/partials/match_actions.html`` reflecting the resulting state.
    """
    resolved = _resolve_match_token(token)
    if resolved is None:
        return HttpResponse(status=400)

    match, registration, side = resolved
    display_state = _compute_match_display_state(match, side)

    if display_state == _STATE_ACTIONABLE:
        try:
            match = decline_match(match, registration)
        except ValueError:
            # Status changed between resolution and action; re-render current state.
            pass

    context = _match_context(match, registration, side, token=token)
    return render(request, "public/partials/match_actions.html", context)


@require_htmx
@require_POST
def match_report_no_show(request: HttpRequest, token: str) -> HttpResponse:
    """HTMX POST: report a post-accept no-show and return the updated actions partial.

    Guarded by ``@require_htmx`` (Invariant 7) and ``@require_POST`` — the
    action is irreversible (suspends the accused's registration) so a GET, even
    with the HX header, must not trigger it.

    Re-validates the token, confirms the match is still ACCEPTED with no
    existing report, then calls ``report_no_show``. Renders
    ``public/partials/match_actions.html`` reflecting the resulting CANCELLED
    state.

    A second POST on a match that is already CANCELLED (or otherwise
    non-ACCEPTED) is a safe no-op: the service raises ``ValueError``, which is
    caught and silently swallowed, and the partial is re-rendered with the
    current state.
    """
    resolved = _resolve_match_token(token)
    if resolved is None:
        return HttpResponse(status=400)

    match, registration, side = resolved

    if match.status == Match.Status.ACCEPTED and not match.no_show_reported_by:
        try:
            match = report_no_show(match, registration)
        except ValueError:
            # Status changed or already reported between resolution and action;
            # re-render current state.
            match.refresh_from_db()

    context = _match_context(match, registration, side, token=token)
    return render(request, "public/partials/match_actions.html", context)
