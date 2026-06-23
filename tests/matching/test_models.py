# Tests for the Season, PriceCategory and Registration models.

import pytest
from django.db import IntegrityError

from matching.models import PriceCategory, Registration, Season
from tests.accounts.factories import AccountFactory
from tests.matching.factories import (
    PriceCategoryFactory,
    RegistrationFactory,
    SeasonFactory,
)

pytestmark = pytest.mark.django_db


def test_season_to_string_is_the_name() -> None:
    """Season.to_string returns the season name."""
    season = SeasonFactory.create(name="2026/27")
    assert str(season) == "2026/27"


def test_active_returns_only_open_seasons() -> None:
    """SeasonQuerySet.active filters to seasons open for registration."""
    active = SeasonFactory.create(is_active=True)
    SeasonFactory.create(is_active=False)
    assert list(Season.objects.active()) == [active]


def test_price_category_to_string() -> None:
    """PriceCategory.to_string combines the season and category label."""
    category = PriceCategoryFactory.create(
        season=SeasonFactory.create(name="2026/27"),
        code=PriceCategory.Code.ADULT,
    )
    assert str(category) == "2026/27 · Adult"


def test_code_must_be_unique_per_season() -> None:
    """A season cannot have two categories with the same code."""
    season = SeasonFactory.create()
    PriceCategoryFactory.create(season=season, code=PriceCategory.Code.ADULT, order=2)
    with pytest.raises(IntegrityError):
        PriceCategoryFactory.create(
            season=season, code=PriceCategory.Code.ADULT, order=3
        )


def test_for_season_filters_categories() -> None:
    """PriceCategoryQuerySet.for_season scopes to one season."""
    season = SeasonFactory.create()
    category = PriceCategoryFactory.create(season=season)
    PriceCategoryFactory.create(season=SeasonFactory.create())
    assert list(PriceCategory.objects.for_season(season)) == [category]


def test_registration_to_string() -> None:
    """Registration.to_string combines user, role and season."""
    account = AccountFactory.create(user__first_name="Ada", user__last_name="Lovelace")
    registration = RegistrationFactory.create(
        account=account,
        season=SeasonFactory.create(name="2026/27"),
        role=Registration.Role.AMBASSADOR,
    )
    assert "Ambassador" in str(registration)
    assert "2026/27" in str(registration)


def test_one_registration_per_account_per_season() -> None:
    """An account cannot register twice in the same season."""
    account = AccountFactory.create()
    season = SeasonFactory.create()
    RegistrationFactory.create(account=account, season=season)
    with pytest.raises(IntegrityError):
        RegistrationFactory.create(account=account, season=season)


def test_registration_queryset_role_and_status_filters() -> None:
    """The queryset helpers filter by role and pool status."""
    season = SeasonFactory.create()
    category = PriceCategoryFactory.create(season=season)
    ambassador = RegistrationFactory.create(
        season=season, price_category=category, role=Registration.Role.AMBASSADOR
    )
    referee = RegistrationFactory.create(
        season=season,
        price_category=category,
        role=Registration.Role.REFEREE,
        held_prior_pass=False,
    )
    for_season = Registration.objects.for_season(season)
    assert list(for_season.ambassadors()) == [ambassador]
    assert list(for_season.referees()) == [referee]
    assert set(for_season.waiting()) == {ambassador, referee}
