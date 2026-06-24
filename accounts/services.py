# Account-domain service functions.
#
# Profile updates and account deletion run inline here (no Django signals,
# CLAUDE.md "Models"), each in a single transaction.
#
# Note: ``Account`` has been removed. Participant attributes (phone,
# preferred_language) now live on ``matching.Registration``.
# ``update_account`` writes onto the user's registration; if the user has no
# registration the update is a no-op for those fields.

from __future__ import annotations

import logging

from allauth.account.models import EmailAddress
from django.contrib.auth.models import User
from django.db import transaction

from matching.models import Registration

logger = logging.getLogger(__name__)


def get_or_create_participant_user(email: str) -> User:
    """Return the passwordless user for a verified ``email``, creating if needed.

    Keyed on the lowercased email as username. The matching allauth
    ``EmailAddress`` is recorded as verified so the user's email state is
    consistent with the social-login flow.
    """
    email = email.lower()
    with transaction.atomic():
        user, created = User.objects.get_or_create(
            username=email, defaults={"email": email}
        )
        if created:
            user.set_unusable_password()
            user.save(update_fields=["password"])
        EmailAddress.objects.get_or_create(
            user=user,
            email=email,
            defaults={"verified": True, "primary": True},
        )
    logger.info("Verified participant user pk=%s", user.pk)
    return user


def mark_email_verified(user: User) -> None:
    """Record the allauth EmailAddress for ``user`` as verified and primary.

    Called at registration-confirmation time (combined-form flow) so that the
    allauth email state is consistent with the social-login path.  Mirrors what
    ``get_or_create_participant_user`` does at email-verification time.  If the
    EmailAddress row already exists with ``verified=True`` this is a no-op.
    """
    email = user.email.lower()
    EmailAddress.objects.update_or_create(
        user=user,
        email=email,
        defaults={"verified": True, "primary": True},
    )
    logger.info("Marked email verified for user pk=%s (%s)", user.pk, email)


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
