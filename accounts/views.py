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
#
# Login flow (VERB-46 — allauth removed):
#   login_request  GET/POST — email form → sends magic link
#   login_sent     GET — static "check your inbox" page
#   login_verify   GET — show "Sign in as <email>" + Confirm button (no login)
#                  POST — validate token, call login(), redirect to accounts:detail
#   logout         POST — calls logout(), redirects to public:home
#                  GET  — renders logout confirmation page

from __future__ import annotations

import logging
from typing import cast

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import login
from django.contrib.auth import logout as auth_logout
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import User
from django.db.models import Q
from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect, render
from django.utils import timezone
from django.utils.translation import gettext as _
from django_ratelimit.decorators import ratelimit

from billing.models import Payment
from core.emails import normalise_email
from core.ratelimit import rate_limited_response
from matching.models import Match, Registration
from matching.services import queue_position as get_queue_position
from matching.services import rejoin_queue, status_pill_for
from public.views import _render_match_page

from .forms import AccountForm
from .services import (
    delete_account,
    send_confirmation_email,
    send_login_email,
    update_account,
)
from .tokens import make_match_access_token, read_login_token

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Magic-link login flow (VERB-46)
# ---------------------------------------------------------------------------


@ratelimit(key="ip", rate="20/h", method="POST", block=False)  # type: ignore[untyped-decorator]  # django-ratelimit has no type stubs
@ratelimit(key="post:email", rate="5/h", method="POST", block=False)  # type: ignore[untyped-decorator]  # django-ratelimit has no type stubs
def login_request(request: HttpRequest) -> HttpResponse:
    """Show the magic-link login form (GET) or process the email submission (POST).

    GET — renders the email form at ``accounts/login.html``.
    POST — normalises the submitted email, looks up an active User with that
    address, and if found sends a magic link via ``send_login_email``. Always
    redirects to ``accounts:login_sent`` regardless of whether the address was
    found — no enumeration (Invariant 5, acceptance criterion).

    Rate-limited: 20 POSTs/hour per IP and 5 POSTs/hour per email address.
    Exceeding either limit returns a 429 response without revealing whether
    the email is registered (Invariant 5 preserved).
    """
    if request.method == "POST":
        if getattr(request, "limited", False):
            return rate_limited_response(request)
        raw_email = request.POST.get("email", "")
        email = normalise_email(raw_email)
        try:
            user = User.objects.get(email=email, is_active=True)
        except User.DoesNotExist:
            user = None

        if user is not None:
            login_url = send_login_email(request, user)
            if settings.DEBUG:
                request.session["debug_login_url"] = login_url

        return redirect("accounts:login_sent")

    return render(request, "accounts/login.html")


def login_sent(request: HttpRequest) -> HttpResponse:
    """Static "check your inbox" page shown after a login link is requested.

    Under DEBUG, the session may carry ``debug_login_url`` — pop it and pass
    it to the template so developers can click through without email.
    """
    debug_login_url = None
    if settings.DEBUG:
        debug_login_url = request.session.pop("debug_login_url", None)
    return render(
        request,
        "accounts/login_sent.html",
        {"debug_login_url": debug_login_url},
    )


def login_verify(request: HttpRequest, token: str) -> HttpResponse:
    """Render the magic-link confirmation page (GET) or complete the login (POST).

    GET — validates the token via ``read_login_token``. Invalid/expired tokens
    render ``accounts/login_invalid.html`` with status 400 and do NOT log the
    user in (prefetch-safe, Invariant 6). Valid tokens render
    ``accounts/login_verify.html`` showing the target user's email and a
    Confirm button that POSTs to this same URL.

    POST — re-validates the token (idempotent guard against replay), then calls
    ``django.contrib.auth.login`` with ``ModelBackend`` and redirects to
    ``accounts:detail``.
    """
    user_pk = read_login_token(token)
    if user_pk is None:
        return render(request, "accounts/login_invalid.html", status=400)

    try:
        user = User.objects.get(pk=user_pk, is_active=True)
    except User.DoesNotExist:
        # Covers both deleted users and deactivated (is_active=False) users;
        # treat both as invalid tokens so inactive accounts cannot log in.
        return render(request, "accounts/login_invalid.html", status=400)

    if request.method == "POST":
        login(
            request,
            user,
            backend="django.contrib.auth.backends.ModelBackend",
        )
        return redirect("accounts:detail")

    return render(request, "accounts/login_verify.html", {"email": user.email})


def logout_view(request: HttpRequest) -> HttpResponse:
    """Log out the current user (POST) or show the logout confirmation page (GET).

    POST — calls ``django.contrib.auth.logout`` and redirects to ``public:home``.
    GET  — renders ``accounts/logout.html`` with a confirmation form.
    """
    if request.method == "POST":
        auth_logout(request)
        return redirect("public:home")
    return render(request, "accounts/logout.html")


# ---------------------------------------------------------------------------
# Account self-service views (authenticated)
# ---------------------------------------------------------------------------


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

    ``email_verified`` is derived from the Registration status (not from the
    former allauth EmailAddress model, which has been removed in VERB-46).
    An admin user with no Registration is treated as unverified (False).

    ``can_cancel`` (VERB-88) is True under the same condition as
    ``can_rejoin`` (PAUSED, no active match); it drives the "Cancel & refund"
    link on the account page.
    """
    user = cast(User, request.user)
    try:
        registration: Registration | None = Registration.objects.get(user=user)
    except Registration.DoesNotExist:
        registration = None

    # Email is considered verified once the registration leaves UNVERIFIED status
    # (i.e. the confirmation link was clicked). Admin users without a registration
    # are treated as unverified for display purposes.
    email_verified = (
        registration is not None
        and registration.status != Registration.Status.UNVERIFIED
    )

    debug_verify_url = None
    if settings.DEBUG:
        debug_verify_url = request.session.pop("debug_verify_url", None)

    # Look up the active match for this user (PROPOSED, PENDING, or ACCEPTED).
    # Registration.status no longer reflects match progress (VERB-44).
    # active_at excludes a PROPOSED/PENDING match whose contact window has
    # lapsed but which the hourly expire_matches sweep has not yet processed,
    # so the account page reads it as inactive — matching the match page's own
    # expires_at check (VERB-113).
    active_match: Match | None = (
        Match.objects.active_at(timezone.now())
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

    # Queue position — only computed for VERIFIED registrations without an active
    # match (pool members awaiting a pairing).
    position: int | None = None
    if (
        registration is not None
        and registration.status == Registration.Status.VERIFIED
        and active_match is None
    ):
        position = get_queue_position(registration)

    # can_rejoin — True when the registration is PAUSED and there is no active
    # match (the normal case after a decline or expiry).
    can_rejoin = (
        registration is not None
        and registration.status == Registration.Status.PAUSED
        and active_match is None
    )

    # can_cancel — True under the same condition as can_rejoin (PAUSED, no
    # active match). Drives the "Cancel & refund" link (VERB-88), which sits
    # alongside "Rejoin the queue" on the account page.
    can_cancel = (
        registration is not None
        and registration.status == Registration.Status.PAUSED
        and active_match is None
    )

    return render(
        request,
        "accounts/detail.html",
        {
            "registration": registration,
            "email_verified": email_verified,
            "debug_verify_url": debug_verify_url,
            "status_pill": status_pill_for(registration, match_state),
            "match_state": match_state,
            "partner_first_name": partner_first_name,
            "partner_accepted": partner_accepted,
            "queue_position": position,
            "can_rejoin": can_rejoin,
            "can_cancel": can_cancel,
        },
    )


@login_required
def account_resend_confirmation(request: HttpRequest) -> HttpResponse:
    """Resend the confirmation email for an UNVERIFIED registration.

    POST-only. Looks up the authenticated user's UNVERIFIED registration; if
    found, resends the confirmation email and stashes the URL in the session
    under DEBUG. On any other method, redirects to the account detail page
    without sending.

    Messages have been removed (owner decision, VERB-46). The view is now
    redirect-only — the redirect itself is the only feedback.
    """
    if request.method != "POST":
        return redirect("accounts:detail")

    user = cast(User, request.user)
    try:
        registration = Registration.objects.get(
            user=user, status=Registration.Status.UNVERIFIED
        )
    except Registration.DoesNotExist:
        return redirect("accounts:detail")

    confirm_url = send_confirmation_email(request, registration)
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
    """Confirm (GET) and perform (POST) deletion of the participant's account.

    ``delete_account`` (VERB-88) is the single deletion chokepoint — it
    refunds any HELD deposit before deleting the user, so this view need not
    know about billing beyond the confirm-page copy. The GET branch looks up
    the same HELD deposit (read-only) so ``delete.html`` can tell the user
    whether confirming will trigger a refund.
    """
    if request.method == "POST":
        user = cast(User, request.user)
        auth_logout(request)
        delete_account(user)
        messages.success(request, _("Your account has been deleted."))
        return redirect("public:home")

    user = cast(User, request.user)
    registration = Registration.objects.filter(user=user).first()
    deposit_amount_chf: int | None = None
    if registration is not None:
        deposit = Payment.objects.for_registration(registration).held().first()
        if deposit is not None:
            deposit_amount_chf = deposit.amount_chf

    return render(
        request,
        "accounts/delete.html",
        {"deposit_amount_chf": deposit_amount_chf},
    )


@login_required
def account_match(request: HttpRequest) -> HttpResponse:
    """Render the match page for the authenticated user's active match.

    Looks up the user's non-terminal match (PROPOSED, PENDING, or ACCEPTED)
    without requiring a token — the login session provides the auth. This route
    allows participants to reach their match page from the account page even
    after the emailed token link has expired (relevant for ACCEPTED matches).
    A PROPOSED/PENDING match whose contact window has lapsed but which the
    hourly expire_matches sweep has not yet processed is treated as inactive
    (``active_at``), so the user is redirected to ``accounts:detail`` rather
    than shown a stale match page (VERB-113).

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
        Match.objects.active_at(timezone.now())
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


@login_required
def account_rejoin_queue(request: HttpRequest) -> HttpResponse:
    """Re-activate a PAUSED registration and attempt an immediate match.

    POST-only. Loads the authenticated user's PAUSED registration and calls
    ``rejoin_queue``, which transitions it to VERIFIED (priority -= 1) and
    attempts a match proposal. Non-POST requests and non-PAUSED registrations
    both redirect to ``accounts:detail`` without action.

    The view mirrors ``account_resend_confirmation`` in shape (guard →
    redirect pattern, no messages).
    """
    if request.method != "POST":
        return redirect("accounts:detail")

    user = cast(User, request.user)
    try:
        registration = Registration.objects.get(
            user=user, status=Registration.Status.PAUSED
        )
    except Registration.DoesNotExist:
        return redirect("accounts:detail")

    rejoin_queue(registration)
    return redirect("accounts:detail")
