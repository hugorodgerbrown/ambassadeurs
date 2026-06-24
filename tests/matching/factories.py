# Test factories for the matching domain.

from datetime import UTC, datetime

import factory

from matching.models import Match, Registration
from tests.accounts.factories import UserFactory

# Placeholder consent statements used as factory defaults so that existing tests
# that do not exercise the acceptance flow remain green.
_DEFAULT_ACCEPTED_TERMS = [
    "I confirm my eligibility.",
    "I have read and agree to the Terms of Use",
]

# Sentinel tz-aware datetime used as the default response timestamp in traits.
_RESPONSE_AT = datetime(2026, 9, 2, 10, 0, 0, tzinfo=UTC)


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
    flake_count = 0
    accepted_terms = factory.LazyFunction(lambda: list(_DEFAULT_ACCEPTED_TERMS))
    terms_accepted_at = factory.LazyFunction(
        lambda: datetime(2026, 9, 1, 10, 0, 0, tzinfo=UTC)
    )

    class Params:
        """Extra traits for common configurations."""

        referee = factory.Trait(
            role=Registration.Role.REFEREE,
            prior_pass=Registration.PriorPass.NONE,
        )
        suspended = factory.Trait(
            status=Registration.Status.SUSPENDED,
        )


class MatchFactory(factory.django.DjangoModelFactory[Match]):
    """Factory for Match (PROPOSED by default).

    Traits:
        accepted: status=ACCEPTED, both *_accepted_at populated, both
            registrations CONFIRMED.
        declined: status=DECLINED, declined_by=AMBASSADOR, declined_at set.
        abandoned: status=ABANDONED, no_show_reported_by=REFEREE,
            no_show_reported_at set.
    """

    class Meta:
        model = Match

    ambassador_registration = factory.SubFactory(RegistrationFactory)
    referee_registration = factory.SubFactory(RegistrationFactory, referee=True)
    status = Match.Status.PROPOSED
    expires_at = factory.LazyFunction(
        lambda: datetime(2099, 12, 31, 23, 59, 59, tzinfo=UTC)
    )

    class Params:
        """Extra traits for common match states."""

        accepted = factory.Trait(
            status=Match.Status.ACCEPTED,
            ambassador_accepted_at=factory.LazyFunction(lambda: _RESPONSE_AT),
            referee_accepted_at=factory.LazyFunction(lambda: _RESPONSE_AT),
            ambassador_registration=factory.SubFactory(
                RegistrationFactory,
                status=Registration.Status.CONFIRMED,
            ),
            referee_registration=factory.SubFactory(
                RegistrationFactory,
                referee=True,
                status=Registration.Status.CONFIRMED,
            ),
        )

        declined = factory.Trait(
            status=Match.Status.DECLINED,
            declined_by=Match.Side.AMBASSADOR,
            declined_at=factory.LazyFunction(lambda: _RESPONSE_AT),
        )

        abandoned = factory.Trait(
            status=Match.Status.ABANDONED,
            ambassador_accepted_at=factory.LazyFunction(lambda: _RESPONSE_AT),
            referee_accepted_at=factory.LazyFunction(lambda: _RESPONSE_AT),
            no_show_reported_by=Match.Side.REFEREE,
            no_show_reported_at=factory.LazyFunction(lambda: _RESPONSE_AT),
        )
