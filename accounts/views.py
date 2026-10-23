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
#   `match_state ∈ {none, proposed, pending, accepted}` and the rest of the
#   Match status card's context are built by matching.selectors.match_status_context
#   (VERB-116), shared with public.views.register_done so both surfaces render
#   the identical component.
#
# Login flow (VERB-46 — allauth removed):
#   login_request  GET/POST — email form → sends magic link
#   login_sent     GET — static "check your inbox" page
#   login_verify   GET — show "Sign in as <email>" + Confirm button (no login)
#                  POST — validate token, call login(), redirect to accounts:detail
#   logout         POST — calls logout(), redirects to public:home
#                  GET  — renders logout confirmation page
#
# Passkeys (ADR 0030, ported from the Titan template). JSON endpoints driven by
# static/js/passkeys.js; the challenge lives in the session (accounts.passkeys):
#   passkey_sign_in_options   POST — WebAuthn challenge for credentials.get()
#   passkey_sign_in           POST — verify, login(), answer with where to go
#   passkey_register_options  POST — challenge for credentials.create() (signed in)
#   passkey_register          POST — verify and save the Passkey (signed in)
#   passkey_delete            POST — remove one of the user's passkeys (signed in)

from __future__ import annotations

import json
import logging
from typing import Any, cast

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import login
from django.contrib.auth import logout as auth_logout
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import User
from django.db.models import Q
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.translation import gettext as _
from django.views.decorators.http import require_POST
from django_ratelimit.decorators import ratelimit

from accounts import passkeys
from accounts.models import Passkey
from billing.models import Payment
from core.decorators import login_required_json
from core.emails import mask_email, normalise_email
from core.ratelimit import rate_limited_response
from matching.models import Match, Registration
from matching.selectors import match_status_context, queue_snapshot_context
from matching.services import rejoin_queue
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

    # Masked (``a•••@example.com``): the link can be forwarded or opened on a
    # shared screen, and the page only needs to say whose account it opens.
    return render(
        request, "accounts/login_verify.html", {"masked_email": mask_email(user.email)}
    )


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
# Passkeys (ADR 0030)
# ---------------------------------------------------------------------------

_BACKEND = "django.contrib.auth.backends.ModelBackend"


def _json_body(request: HttpRequest) -> dict[str, Any] | None:
    """Return the request's JSON object, or None if it isn't one."""
    try:
        data = json.loads(request.body)
    except ValueError:
        return None
    return data if isinstance(data, dict) else None


def _rate_limited_json() -> JsonResponse:
    """Return the 429 a passkey endpoint answers once its rate limit is hit."""
    return JsonResponse({"error": "rate_limited"}, status=429)


@require_POST
@ratelimit(key="ip", rate="30/10m", method="POST", block=False)  # type: ignore[untyped-decorator]  # django-ratelimit has no type stubs
def passkey_sign_in_options(request: HttpRequest) -> JsonResponse:
    """Return a challenge for ``navigator.credentials.get()``.

    Rate-limited to 30 challenges per IP per 10 minutes, as in Titan.
    """
    if getattr(request, "limited", False):
        return _rate_limited_json()
    return JsonResponse(passkeys.sign_in_options(request.session))


@require_POST
def passkey_sign_in(request: HttpRequest) -> JsonResponse:
    """Verify a passkey and sign its owner in.

    The body is ``{"credential": <the credential's JSON>}``. Success answers
    ``{"next": <account page URL>}``, the same landing as the magic link. An
    unknown passkey answers 404 with its id, so the page can tell the browser
    to forget it (``PublicKeyCredential.signalUnknownCredential``). A
    deactivated account answers 403 and is not signed in.
    """
    data = _json_body(request)
    if data is None or not isinstance(data.get("credential"), dict):
        return JsonResponse({"error": "bad_request"}, status=400)
    try:
        user = passkeys.authenticate(json.dumps(data["credential"]), request.session)
    except passkeys.UnknownPasskeyError as exc:
        return JsonResponse(
            {"error": "unknown_passkey", "credentialId": exc.credential_id}, status=404
        )
    except passkeys.PasskeyError as exc:
        logger.info("passkeys.sign_in_failed %s", exc)
        return JsonResponse({"error": "not_verified"}, status=400)
    if not user.is_active:
        return JsonResponse({"error": "account_disabled"}, status=403)
    login(request, user, backend=_BACKEND)
    return JsonResponse({"next": reverse("accounts:detail")})


@require_POST
@login_required_json
def passkey_register_options(request: HttpRequest) -> JsonResponse:
    """Return a challenge for ``navigator.credentials.create()``."""
    user = cast(User, request.user)
    return JsonResponse(passkeys.registration_options(user, request.session))


@require_POST
@login_required_json
@ratelimit(key="ip", rate="30/10m", method="POST", block=False)  # type: ignore[untyped-decorator]  # django-ratelimit has no type stubs
def passkey_register(request: HttpRequest) -> JsonResponse:
    """Verify a new passkey and save it. The body is the credential's JSON."""
    if getattr(request, "limited", False):
        return _rate_limited_json()
    if _json_body(request) is None:
        return JsonResponse({"error": "bad_request"}, status=400)
    user = cast(User, request.user)
    name = passkeys.default_name(request.headers.get("User-Agent", ""))
    try:
        passkey = passkeys.register(request.body.decode(), request.session, user, name)
    except passkeys.PasskeyError as exc:
        logger.info("passkeys.register_failed %s", exc)
        return JsonResponse({"error": "not_verified"}, status=400)
    return JsonResponse({"id": passkey.pk, "name": passkey.name}, status=201)


@require_POST
@login_required
def passkey_delete(request: HttpRequest, pk: int) -> HttpResponse:
    """Remove one of the signed-in user's passkeys, then show the account page.

    Scoped to the user: another user's passkey id answers 404. Redirect-only,
    like ``account_resend_confirmation``: the list itself shows it has gone.
    """
    passkey = get_object_or_404(Passkey, pk=pk, user=request.user)
    passkey.delete()
    return redirect("accounts:detail")


# ---------------------------------------------------------------------------
# Account self-service views (authenticated)
# ---------------------------------------------------------------------------


@login_required
def account_detail(request: HttpRequest) -> HttpResponse:
    """Show the participant's profile, match status and security controls.

    The Match status card's context (``registration``, ``status_pill``,
    ``match_state``, ``partner_first_name``, ``partner_accepted``,
    ``queue_position``, ``can_rejoin``, ``can_cancel``) is built by
    ``matching.selectors.match_status_context`` (VERB-116) — shared with
    ``public.views.register_done`` so both surfaces render the identical
    component.

    ``email_verified`` is derived from the Registration status (not from the
    former allauth EmailAddress model, which has been removed in VERB-46).
    An admin user with no Registration is treated as unverified (False).

    ``queue`` (SKI-174) is the live queue visualisation, rendered inside the
    Match status card. Passing the viewer's registration — and the queue position
    ``match_status_context`` has already computed, rather than making
    ``queue_position`` run again — draws their own glyph in the highlight colour,
    so the ordinal in the status copy has a picture to sit against. A user with
    no registration still gets the pool picture, just without the highlight.
    """
    user = cast(User, request.user)
    status_context = match_status_context(user)
    registration = status_context["registration"]

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

    return render(
        request,
        "accounts/detail.html",
        {
            **status_context,
            "passkeys": Passkey.objects.for_user(user),
            "email_verified": email_verified,
            "debug_verify_url": debug_verify_url,
            "queue": queue_snapshot_context(
                timezone.now(), registration, status_context["queue_position"]
            ),
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
