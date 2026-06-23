# Account-domain service functions.
#
# Profile updates and account deletion run inline here (no Django signals,
# CLAUDE.md "Models"), each in a single transaction.

from __future__ import annotations

import logging

from allauth.account.models import EmailAddress
from django.contrib.auth.models import User
from django.db import transaction

from .models import Account

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
    logger.info("Verified participant email %s", email)
    return user


def update_account(
    *,
    user: User,
    first_name: str,
    last_name: str,
    phone: str = "",
    preferred_language: str = "",
) -> Account:
    """Update the user's name and their Account contact / preference fields."""
    with transaction.atomic():
        user.first_name = first_name
        user.last_name = last_name
        user.save(update_fields=["first_name", "last_name"])

        account, _ = Account.objects.get_or_create(user=user)
        account.phone = phone
        account.preferred_language = preferred_language
        account.save(update_fields=["phone", "preferred_language", "updated_at"])

    logger.info("Updated account for %s", user.email)
    return account


def delete_account(user: User) -> None:
    """Delete the user, cascading their Account and registrations."""
    email = user.email
    with transaction.atomic():
        user.delete()
    logger.info("Deleted account for %s", email)
