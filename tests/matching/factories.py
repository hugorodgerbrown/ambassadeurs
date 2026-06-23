# Test factories for the matching domain.

from datetime import UTC, datetime

import factory

from matching.models import Match, Registration
from tests.accounts.factories import UserFactory


class RegistrationFactory(factory.django.DjangoModelFactory[Registration]):
    """Factory for Registration (ambassador with a seasonal pass by default).

    Use the ``referee`` trait for a referee (prior_pass=NONE).
    """

    class Meta:
        model = Registration

    user = factory.SubFactory(UserFactory)
    role = Registration.Role.AMBASSADOR
    prior_pass = Registration.PriorPass.SEASONAL
    phone = factory.Sequence(lambda n: f"+4179000{n:04d}")
    preferred_language = "en"
    preferred_location = ""
    status = Registration.Status.WAITING
    priority = 0

    class Params:
        """Extra traits for common configurations."""

        referee = factory.Trait(
            role=Registration.Role.REFEREE,
            prior_pass=Registration.PriorPass.NONE,
        )


class MatchFactory(factory.django.DjangoModelFactory[Match]):
    """Factory for Match (PROPOSED by default)."""

    class Meta:
        model = Match

    ambassador_registration = factory.SubFactory(RegistrationFactory)
    referee_registration = factory.SubFactory(RegistrationFactory, referee=True)
    status = Match.Status.PROPOSED
    expires_at = factory.LazyFunction(
        lambda: datetime(2099, 12, 31, 23, 59, 59, tzinfo=UTC)
    )
