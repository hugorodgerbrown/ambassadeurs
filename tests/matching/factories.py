# Test factories for the matching domain.

from decimal import Decimal

import factory

from matching.models import PriceCategory, Registration, Season
from tests.accounts.factories import AccountFactory


class SeasonFactory(factory.django.DjangoModelFactory[Season]):
    """Factory for Season."""

    class Meta:
        model = Season
        django_get_or_create = ["name"]

    name = factory.Sequence(lambda n: f"20{26 + n}/{27 + n}")
    slug = factory.LazyAttribute(lambda o: o.name.replace("/", "-"))
    is_active = True
    contact_window_hours = 72


class PriceCategoryFactory(factory.django.DjangoModelFactory[PriceCategory]):
    """Factory for PriceCategory."""

    class Meta:
        model = PriceCategory

    season = factory.SubFactory(SeasonFactory)
    code = PriceCategory.Code.ADULT
    order = 2
    label = "Adult"
    full_price = Decimal("1400.00")
    discounted_price = Decimal("999.00")


class RegistrationFactory(factory.django.DjangoModelFactory[Registration]):
    """Factory for Registration (ambassador by default)."""

    class Meta:
        model = Registration

    season = factory.SubFactory(SeasonFactory)
    account = factory.SubFactory(AccountFactory)
    price_category = factory.SubFactory(
        PriceCategoryFactory, season=factory.SelfAttribute("..season")
    )
    role = Registration.Role.AMBASSADOR
    held_prior_pass = True
    discount_eligible = True
    status = Registration.Status.WAITING
