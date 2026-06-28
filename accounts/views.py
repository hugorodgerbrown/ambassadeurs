# Account self-service views.
#
# The authenticated participant views and edits their own profile. Role is
# shown read-only — it is fixed once registered (CLAUDE.md). Participant
# attributes (phone, preferred_language) now live on matching.Registration
# rather than a separate Account model. If the user has no registration they
# are redirected to the registration flow.
#
# Match state on the account page (VERB-44 / ADR 0011):
#   Registration.Status tracks pool standing (UNVERIFIED, VERIFIED, WITHDRAWN,
#   SUSPENDED). Match progress is derived from the active Match row.
#   account_detail computes `match_state ∈ {none, proposed, pending, accepted}`
#   from the active match (if any) and passes it to the template, so the
#   template never needs to compare Registration.Status values to infer match
#   progress.

from __future__ import annotations

from typing import cast

from allauth.account.models import EmailAddress
from django.conf import settings
from django.contrib import messages
from django.contrib.auth import logout
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import User
from django.db.models import Q
from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect, render
from django.utils.translation import gettext as _

from matching.models import Match, Registration
from matching.services import queue_position as get_queue_position
from matching.services import total_accepted_matches
from public.views import _render_match_page

from .forms import AccountForm
from .services import delete_account, send_confirmation_email, update_account
from .tokens import make_match_access_token


def _match_status_pill(
    registration: Registration | None,
    match_state: str,
) -> dict[str, str]:
    """Return the ``{label, tone}`` for the Match status heading pill.

    ``tone`` is the ``.tag-status--<tone>`` suffix; ``label`` is translated.
    ``match_state`` is one of ``none``, ``proposed``, ``pending``, ``accepted``.

    Covers every Registration.Status plus the no-registration and active-match
    cases. VERIFIED with no active match → "In the queue" (muted); UNVERIFIED,
    WITHDRAWN, SUSPENDED → muted. An active match overrides the pill regardless
    of Registration.Status.
    """
    if registration is None:
        return {"label": _("No match"), "tone": "muted"}

    # Active-match states override the registration-status pill.
    if match_state == "proposed":
        return {"label": _("Match pending"), "tone": "wait"}
    if match_state == "pending":
        return {"label": _("Match pending"), "tone": "wait"}
    if match_state == "accepted":
        return {"label": _("Match confirmed"), "tone": "done"}

    # No active match — derive from pool standing.
    pills: dict[str, tuple[str, str]] = {
        Registration.Status.UNVERIFIED: (_("Email unconfirmed"), "muted"),
        Registration.Status.VERIFIED: (_("In the queue"), "muted"),
        Registration.Status.WITHDRAWN: (_("Withdrawn"), "muted"),
        Registration.Status.SUSPENDED: (_("Suspended"), "muted"),
    }
    label, tone = pills.get(
        Registration.Status(registration.status),
        (registration.get_status_display(), "muted"),
    )
    return {"label": str(label), "tone": tone}


@login_required
def account_detail(request: HttpRequest) -> HttpResponse:
    """Show the participant's profile, match status and security controls.

    Fetches the user's active match (if any) and derives:
    - ``match_state``: one of ``none``, ``proposed``, ``pending``, ``accepted``.
    - ``partner_first_name``: the partner's first name (shown before mutual
      accept; only surname/email/phone stay hidden per Invariant 1).
    - ``partner_accepted``: whether the partner has responded (drives the
      "partner pending" vs "partner waiting on us" copy).

    Queue position and accepted-match count are computed for VERIFIED
    registrations that are currently in the pool (no active match).
    """
    user = cast(User, request.user)
    try:
        registration: Registration | None = Registration.objects.get(user=user)
    except Registration.DoesNotExist:
        registration = None

    email_verified = EmailAddress.objects.filter(user=user, verified=True).exists()

    debug_verify_url = None
    if settings.DEBUG:
        debug_verify_url = request.session.pop("debug_verify_url", None)

    # Look up the active match for this user (PROPOSED, PENDING, or ACCEPTED).
    # Registration.status no longer reflects match progress (VERB-44).
    active_match: Match | None = (
        Match.objects.active()
        .filter(
            Q(ambassador_registration__user=user) | Q(referee_registration__user=user)
        )
        .select_related(
            "ambassador_registration__user",
            "referee_registration__user",
        )
        .first()
    )

    match_state = "none"
    partner_first_name = ""
    partner_accepted = False

    if active_match is not None:
        if active_match.status == Match.Status.ACCEPTED:
            match_state = "accepted"
        elif active_match.status == Match.Status.PENDING:
            match_state = "pending"
        else:
            match_state = "proposed"

        # Identify which side this user is on to find the partner.
        if active_match.ambassador_registration is not None and (
            active_match.ambassador_registration.user_id == user.pk
        ):
            partner = active_match.referee_registration
            partner_accepted = active_match.referee_accepted_at is not None
        else:
            partner = active_match.ambassador_registration
            partner_accepted = active_match.ambassador_accepted_at is not None

        if partner is not None:
            partner_first_name = partner.user.first_name

    # Fall back to a generic noun when the partner has no first name on file.
    if not partner_first_name:
        partner_first_name = _("your partner")

    # Queue position and accepted-match count — only computed for VERIFIED
    # registrations without an active match (pool members awaiting a pairing).
    position: int | None = None
    accepted_count: int = 0
    if (
        registration is not None
        and registration.status == Registration.Status.VERIFIED
        and active_match is None
    ):
        position = get_queue_position(registration)
        accepted_count = total_accepted_matches()

    return render(
        request,
        "accounts/detail.html",
        {
            "registration": registration,
            "email_verified": email_verified,
            "debug_verify_url": debug_verify_url,
            "status_pill": _match_status_pill(registration, match_state),
            "match_state": match_state,
            "partner_first_name": partner_first_name,
            "partner_accepted": partner_accepted,
            "queue_position": position,
            "total_accepted_matches": accepted_count,
        },
    )


@login_required
def account_resend_confirmation(request: HttpRequest) -> HttpResponse:
    """Resend the confirmation email for an UNVERIFIED registration.

    POST-only. Looks up the authenticated user's UNVERIFIED registration; if
    found, resends the confirmation email and stashes the URL in the session
    under DEBUG. On any other method, redirects to the account detail page
    without sending.
    """
    if request.method != "POST":
        return redirect("accounts:detail")

    user = cast(User, request.user)
    try:
        registration = Registration.objects.get(
            user=user, status=Registration.Status.UNVERIFIED
        )
    except Registration.DoesNotExist:
        messages.error(
            request,
            _("No pending registration found. Your email may already be confirmed."),
        )
        return redirect("accounts:detail")

    confirm_url = send_confirmation_email(request, registration)
    messages.success(
        request,
        _("Confirmation email resent. Please check your inbox."),
    )
    if settings.DEBUG:
        request.session["debug_verify_url"] = confirm_url

    return redirect("accounts:detail")


@login_required
def account_edit(request: HttpRequest) -> HttpResponse:
    """Edit the participant's name, phone and preferred language."""
    user = cast(User, request.user)
    try:
        registration: Registration | None = Registration.objects.get(user=user)
    except Registration.DoesNotExist:
        registration = None

    if request.method == "POST":
        form = AccountForm(request.POST)
        if form.is_valid():
            data = form.cleaned_data
            update_account(
                user=user,
                first_name=data["first_name"],
                last_name=data["last_name"],
                phone=data["phone"],
                preferred_language=data["preferred_language"],
            )
            messages.success(request, _("Your details have been updated."))
            return redirect("accounts:detail")
    else:
        form = AccountForm(
            initial={
                "first_name": user.first_name,
                "last_name": user.last_name,
                "phone": registration.phone if registration else "",
                "preferred_language": (
                    registration.preferred_language if registration else ""
                ),
            }
        )
    return render(
        request,
        "accounts/edit.html",
        {"form": form, "registration": registration},
    )


@login_required
def account_delete(request: HttpRequest) -> HttpResponse:
    """Confirm (GET) and perform (POST) deletion of the participant's account."""
    if request.method == "POST":
        user = cast(User, request.user)
        logout(request)
        delete_account(user)
        messages.success(request, _("Your account has been deleted."))
        return redirect("public:home")
    return render(request, "accounts/delete.html")


@login_required
def account_match(request: HttpRequest) -> HttpResponse:
    """Render the match page for the authenticated user's active match.

    Looks up the user's non-terminal match (PROPOSED, PENDING, or ACCEPTED)
    without requiring a token — the login session provides the auth. This route
    allows participants to reach their match page from the account page even
    after the emailed token link has expired (relevant for ACCEPTED matches).

    A fresh single-purpose match-access token is minted on each load and passed
    to ``_render_match_page`` so the on-page accept/decline/report-no-show forms
    have valid action URLs. Page access itself is gated by ``@login_required``
    (non-expiring), not the token; the token only powers the HTMX action URLs
    and is scoped by ``_MATCH_SALT`` with the usual contact-window expiry
    (Invariant 6).

    Redirects to ``accounts:detail`` if the user has no active match, so there
    is no error page for the "no match yet" case.
    """
    user = cast(User, request.user)
    match = (
        Match.objects.active()
        .filter(
            Q(ambassador_registration__user=user) | Q(referee_registration__user=user)
        )
        .select_related(
            "ambassador_registration__user",
            "referee_registration__user",
        )
        .first()
    )
    if match is None:
        return redirect("accounts:detail")

    # Determine which registration belongs to this user.
    if match.ambassador_registration.user_id == user.pk:
        registration = match.ambassador_registration
    else:
        registration = match.referee_registration

    side = match.side_of(registration)
    # Mint a fresh per-participant token so the action forms (accept/decline/
    # report-no-show) have valid hx-post URLs. Token is single-purpose and
    # in-window (valid for CONTACT_WINDOW_HOURS), satisfying Invariant 6.
    token = make_match_access_token(match.pk, registration.pk)
    return _render_match_page(request, match, registration, side, token=token)
