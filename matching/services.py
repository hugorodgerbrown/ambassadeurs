# Matching-domain service functions.
#
# Side effects (User/Account/Registration creation) are orchestrated here and
# called inline from views — never via Django signals (CLAUDE.md "Models").

from __future__ import annotations

import logging

from django.contrib.auth.models import User
from django.db import transaction

from accounts.models import Account

from .models import PriceCategory, Registration, Season

logger = logging.getLogger(__name__)


def register_participant(
    *,
    season: Season,
    role: str,
    first_name: str,
    last_name: str,
    email: str,
    price_category: PriceCategory,
    preferred_location: str = "",
    preferred_language: str = "",
) -> Registration:
    """Enrol a participant into ``season``'s pool and return the Registration.

    Creates a passwordless ``User`` (keyed on the lowercased email as username)
    and its ``Account`` on first registration, reusing them across seasons. The
    prior-season attestation (``held_prior_pass``) is derived from the role:
    ambassadors are returning holders, referees are genuinely new. Runs in a
    single transaction.
    """
    email = email.lower()
    with transaction.atomic():
        user, created = User.objects.get_or_create(
            username=email,
            defaults={
                "email": email,
                "first_name": first_name,
                "last_name": last_name,
            },
        )
        if created:
            user.set_unusable_password()
            user.save(update_fields=["password"])

        account, _ = Account.objects.get_or_create(user=user)
        if preferred_language and account.preferred_language != preferred_language:
            account.preferred_language = preferred_language
            account.save(update_fields=["preferred_language", "updated_at"])

        registration = Registration.objects.create(
            season=season,
            account=account,
            role=role,
            price_category=price_category,
            preferred_location=preferred_location,
            held_prior_pass=(role == Registration.Role.AMBASSADOR),
        )

    logger.info("Registered %s as %s for season %s", email, role, season.name)
    return registration
