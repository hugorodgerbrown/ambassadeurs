# Tests for the Season and PriceCategory models.

import pytest
from django.db import IntegrityError

from matching.models import PriceCategory, Season
from tests.matching.factories import PriceCategoryFactory, SeasonFactory

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
