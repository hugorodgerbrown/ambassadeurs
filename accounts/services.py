# Account-domain service functions.
#
# Profile updates, account deletion, confirmation-email dispatch, and the
# magic-link login email run inline here (no Django signals, CLAUDE.md "Models"),
# each in a single transaction where applicable.
#
# Note: ``Account`` has been removed. Participant attributes (phone,
# preferred_language) now live on ``matching.Registration`` (OneToOneField to User).
# ``update_account`` writes onto the user's registration; if the user has no
# registration the update is a no-op for those fields.
#
# allauth has been removed (VERB-46). Email-verified state is now derived from
# Registration.status (not the allauth EmailAddress model).
#
# delete_account is the single deletion chokepoint (VERB-88): every delete
# path (the self-service "Delete account" button and the new "Cancel &
# refund" entry from the PAUSED account page) goes through here, so a HELD
# deposit is always refunded before the user — and their registration — is
# deleted. A CAPTURED (accepted match) or FORFEITED (suspended) deposit is not
# HELD, so ``.held().first()`` returns None and no refund happens; that one
# guard naturally gives the "no refund for accepted/suspended" behaviour.
#
# delete_account also fires a best-effort ``account_deleted`` analytics event
# (VERB-124), deferred to transaction.on_commit inside the delete's atomic
# block (core.observability.capture_event) so a failed delete never sends a
# ghost event.

from __future__ import annotations

import logging

from django.conf import settings
from django.contrib.auth.models import User
from django.db import transaction
from django.http import HttpRequest
from django.urls import reverse

from accounts.tokens import (
    LOGIN_TOKEN_MAX_AGE,
    MAX_AGE_SECONDS,
    make_login_token,
    make_registration_confirmation_token,
)
from billing.models import Payment
from billing.services.payments import InvalidPaymentTransition, refund
from core.emails import send_templated_email
from core.observability import capture_event
from matching.models import Registration

logger = logging.getLogger(__name__)


def send_login_email(request: HttpRequest, user: User) -> str:
    """Email a signed magic-link to ``user`` for passwordless login.

    The token carries ``user.pk`` scoped to the single-purpose salt
    ``accounts.login`` (Invariant 6). Subject and body are rendered from
    ``templates/email/login/subject.txt`` and ``templates/email/login/body.txt``
    (plus ``body.html``) so copy can be translated without touching Python.

    Args:
        request: The current HTTP request, used to build the absolute verify URL.
        user: The User who requested a login link.

    Returns:
        The absolute verify URL embedded in the email, so the caller can stash
        it in the session for the DEBUG shortcut link.
    """
    token = make_login_token(user.pk)
    verify_url = request.build_absolute_uri(
        reverse("accounts:login_verify", args=[token])
    )
    expiry_hours = LOGIN_TOKEN_MAX_AGE // 3600
    context = {
        "first_name": user.first_name or "",
        "verify_url": verify_url,
        "expiry_hours": expiry_hours,
    }
    # No language given — renders in the active request language, as before.
    send_templated_email("login", context, [user.email])

    # In development the email is written to the console. Log the unwrapped link
    # on a single line for convenience. Gated on DEBUG so the signed token never
    # reaches production logs.
    if settings.DEBUG:
        logger.info(
            "Login link for user pk=%s: %s",
            user.pk,
            verify_url,
        )

    return verify_url


def send_already_registered_email(request: HttpRequest, user: User) -> str:
    """Email an already-enrolled ``user`` a sign-in link instead of registering.

    Sent when someone submits the public registration form with an email that
    already has a (non-UNVERIFIED) registration. The HTTP response is the same
    generic "check your email" page shown to a brand-new registrant, so the
    registration form does not reveal who is enrolled (VERB-72). Only the real
    mailbox owner sees this mail, which tells them they are already registered
    and links them straight in.

    Reuses the login token (``accounts.login`` salt, Invariant 6) — no new
    token type. Returns the absolute verify URL for the DEBUG shortcut link.
    """
    token = make_login_token(user.pk)
    verify_url = request.build_absolute_uri(
        reverse("accounts:login_verify", args=[token])
    )
    expiry_hours = LOGIN_TOKEN_MAX_AGE // 3600
    context = {
        "first_name": user.first_name or "",
        "verify_url": verify_url,
        "expiry_hours": expiry_hours,
    }
    # No language given — renders in the active request language, as before.
    send_templated_email("already_registered", context, [user.email])

    if settings.DEBUG:
        logger.info(
            "Already-registered sign-in link for user pk=%s: %s",
            user.pk,
            verify_url,
        )

    return verify_url


def send_confirmation_email(request: HttpRequest, registration: Registration) -> str:
    """Email a signed confirmation link for ``registration``.

    The token carries ``registration.pk`` scoped to the single-purpose salt
    ``accounts.registration-confirm`` (Invariant 6). Returns the confirm URL
    so the caller can stash it for the DEBUG shortcut. Subject and body are
    rendered from ``templates/email/confirmation/subject.txt`` and
    ``templates/email/confirmation/body.txt`` (plus ``body.html``).

    Args:
        request: The current HTTP request, used to build the absolute confirm URL.
        registration: The UNVERIFIED Registration whose owner must confirm their email.
    """
    token = make_registration_confirmation_token(registration.pk)
    confirm_url = request.build_absolute_uri(
        reverse("public:register_confirm", args=[token])
    )
    expiry_hours = MAX_AGE_SECONDS // 3600
    context = {
        "first_name": registration.user.first_name or "",
        "confirm_url": confirm_url,
        "expiry_hours": expiry_hours,
        "is_ambassador": registration.is_ambassador,
    }
    # No language given — renders in the active request language, as before.
    send_templated_email("confirmation", context, [registration.user.email])

    # In development the email is written to the console, where the long confirm
    # URL is quoted-printable soft-wrapped and awkward to copy. Log the
    # unwrapped link on a single line for convenience. Gated on DEBUG so the
    # signed token never reaches production logs.
    if settings.DEBUG:
        logger.info(
            "Confirmation link for registration pk=%s: %s",
            registration.pk,
            confirm_url,
        )

    return confirm_url


def update_account(
    *,
    user: User,
    first_name: str,
    last_name: str,
    phone: str = "",
    preferred_language: str = "",
) -> None:
    """Update the user's name and, if they have a registration, their contact fields.

    If the user has no registration (e.g. an admin user) the phone and
    preferred_language update is silently skipped; only the name is saved.
    """
    with transaction.atomic():
        user.first_name = first_name
        user.last_name = last_name
        user.save(update_fields=["first_name", "last_name"])

        try:
            registration = Registration.objects.get(user=user)
        except Registration.DoesNotExist:
            registration = None

        if registration is not None:
            registration.phone = phone
            registration.preferred_language = preferred_language
            registration.save(
                update_fields=["phone", "preferred_language", "updated_at"]
            )

    logger.info("Updated account for user pk=%s", user.pk)


def delete_account(user: User) -> None:
    """Refund any HELD deposit, then delete the user, cascading their registration.

    The refund happens *before* deletion — it needs the Registration to still
    exist to find the deposit — while the Payment audit row itself survives
    deletion (``Payment.registration`` is ``SET_NULL``, VERB-85). A CAPTURED
    (accepted match) or FORFEITED (suspended) deposit is not HELD, so no
    refund is issued for those; ``refund()`` is idempotent per payment, so a
    double-submit of this view can never double-refund. If the season-end
    ``close_season`` sweep refunds the same deposit concurrently (it wins the
    row lock first), ``refund()`` raises ``InvalidPaymentTransition`` — a
    benign race, since the money is already on its way back; we log it and
    proceed with deletion rather than 500 the user (who is mid-logout).

    The ``account_deleted`` analytics event (VERB-124) is captured into a
    local before the ``role`` (read from ``registration``, still available
    here) is gone, then deferred to ``transaction.on_commit`` inside the
    atomic block — the same deferral pattern used for ``registration`` /
    ``email_verified`` — so a failed delete does not send a ghost event. A
    user with no registration (e.g. an admin) is tracked with ``role=None``.
    """
    user_pk = user.pk
    registration = Registration.objects.filter(user=user).first()
    if registration is not None:
        deposit = Payment.objects.for_registration(registration).held().first()
        if deposit is not None:
            try:
                refund(deposit, reason=Payment.Reason.USER_CANCELLED)
            except InvalidPaymentTransition:
                logger.warning(
                    "delete_account: deposit pk=%s left HELD before refund "
                    "(concurrent close_season?); proceeding to delete user pk=%s",
                    deposit.pk,
                    user_pk,
                )
    role = registration.role if registration is not None else None
    with transaction.atomic():
        user.delete()
        transaction.on_commit(
            lambda: capture_event(str(user_pk), "account_deleted", {"role": role})
        )
    logger.info("Deleted account for user pk=%s", user_pk)
