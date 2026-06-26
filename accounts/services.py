# Account-domain service functions.
#
# Profile updates, account deletion, and confirmation-email dispatch run inline
# here (no Django signals, CLAUDE.md "Models"), each in a single transaction.
#
# Note: ``Account`` has been removed. Participant attributes (phone,
# preferred_language) now live on ``matching.Registration``.
# ``update_account`` writes onto the user's registration; if the user has no
# registration the update is a no-op for those fields.

from __future__ import annotations

import logging

from allauth.account.models import EmailAddress
from django.conf import settings
from django.contrib.auth.models import User
from django.core.mail import send_mail
from django.db import transaction
from django.http import HttpRequest
from django.urls import reverse
from django.utils.translation import gettext as _

from accounts.tokens import make_registration_confirmation_token
from core.emails import normalise_email
from matching.models import Registration

logger = logging.getLogger(__name__)


def mark_email_verified(user: User) -> None:
    """Record the allauth EmailAddress for ``user`` as verified and primary.

    Called at registration-confirmation time (combined-form flow) so that the
    allauth email state is consistent with the social-login path.  Mirrors what
    ``get_or_create_participant_user`` does at email-verification time.  If the
    EmailAddress row already exists with ``verified=True`` this is a no-op.
    """
    email = normalise_email(user.email)
    EmailAddress.objects.update_or_create(
        user=user,
        email=email,
        defaults={"verified": True, "primary": True},
    )
    logger.info("Marked email verified for user pk=%s (%s)", user.pk, email)


def send_confirmation_email(request: HttpRequest, registration: Registration) -> str:
    """Email a signed confirmation link for ``registration``.

    The token carries ``registration.pk`` scoped to the single-purpose salt
    ``accounts.registration-confirm`` (Invariant 6). Returns the confirm URL
    so the caller can stash it for the DEBUG shortcut.

    Args:
        request: The current HTTP request, used to build the absolute confirm URL.
        registration: The PENDING Registration whose owner must confirm their email.
    """
    token = make_registration_confirmation_token(registration.pk)
    confirm_url = request.build_absolute_uri(
        reverse("public:register_confirm", args=[token])
    )
    subject = _("Confirm your email to join the queue")
    body = _(
        "Click the link below to confirm your email and join the matching queue "
        "for the 4 Vallées Ambassadors Program:\n\n"
        "%(url)s\n\n"
        "This link expires in 24 hours. If you didn't request it, ignore this email."
    ) % {"url": confirm_url}
    send_mail(subject, body, settings.DEFAULT_FROM_EMAIL, [registration.user.email])

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
    """Delete the user, cascading their registration and matches."""
    user_pk = user.pk
    with transaction.atomic():
        user.delete()
    logger.info("Deleted account for user pk=%s", user_pk)
