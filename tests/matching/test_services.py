# Tests for the matching service functions.

import pytest
from django.contrib.auth.models import User

from accounts.models import Account
from matching.models import Registration, Resort
from matching.services import register_participant
from tests.matching.factories import PriceCategoryFactory, SeasonFactory

pytestmark = pytest.mark.django_db


def test_register_participant_creates_user_account_and_registration() -> None:
    """The service creates a passwordless user, an account and a registration."""
    season = SeasonFactory.create()
    category = PriceCategoryFactory.create(season=season)
    registration = register_participant(
        season=season,
        role=Registration.Role.AMBASSADOR,
        first_name="Ada",
        last_name="Lovelace",
        email="ADA@example.com",
        price_category=category,
        preferred_location=Resort.VERBIER,
        preferred_language="fr",
    )

    user = User.objects.get(username="ada@example.com")
    assert user.email == "ada@example.com"
    assert not user.has_usable_password()
    assert Account.objects.filter(user=user).exists()
    assert registration.role == Registration.Role.AMBASSADOR
    assert registration.held_prior_pass is True
    assert registration.account.preferred_language == "fr"
    assert registration.preferred_location == Resort.VERBIER


def test_referee_registration_sets_held_prior_pass_false() -> None:
    """A referee is recorded as not having held a prior pass."""
    season = SeasonFactory.create()
    category = PriceCategoryFactory.create(season=season)
    registration = register_participant(
        season=season,
        role=Registration.Role.REFEREE,
        first_name="Grace",
        last_name="Hopper",
        email="grace@example.com",
        price_category=category,
    )
    assert registration.held_prior_pass is False


def test_register_with_existing_user_reuses_it() -> None:
    """Passing a user reuses it (no new user) and keeps the name current."""
    from tests.accounts.factories import UserFactory

    season = SeasonFactory.create()
    category = PriceCategoryFactory.create(season=season)
    user = UserFactory.create(email="ada@example.com", first_name="A", last_name="L")
    registration = register_participant(
        season=season,
        role=Registration.Role.REFEREE,
        user=user,
        first_name="Ada",
        last_name="Lovelace",
        price_category=category,
    )
    assert User.objects.count() == 1
    assert registration.account.user == user
    user.refresh_from_db()
    assert user.first_name == "Ada"
    assert user.last_name == "Lovelace"


def test_register_existing_user_with_matching_names_no_update() -> None:
    """Passing a user whose name already matches skips the name update."""
    from tests.accounts.factories import UserFactory

    season = SeasonFactory.create()
    category = PriceCategoryFactory.create(season=season)
    user = UserFactory.create(first_name="Ada", last_name="Lovelace")
    registration = register_participant(
        season=season,
        role=Registration.Role.AMBASSADOR,
        user=user,
        first_name="Ada",
        last_name="Lovelace",
        price_category=category,
    )
    assert registration.account.user == user


def test_same_person_reuses_user_across_seasons() -> None:
    """Registering in a second season reuses the existing user and account."""
    season_one = SeasonFactory.create(name="2026/27")
    season_two = SeasonFactory.create(name="2027/28")
    category_one = PriceCategoryFactory.create(season=season_one)
    category_two = PriceCategoryFactory.create(season=season_two)

    first = register_participant(
        season=season_one,
        role=Registration.Role.AMBASSADOR,
        first_name="Ada",
        last_name="Lovelace",
        email="ada@example.com",
        price_category=category_one,
    )
    second = register_participant(
        season=season_two,
        role=Registration.Role.AMBASSADOR,
        first_name="Ada",
        last_name="Lovelace",
        email="ada@example.com",
        price_category=category_two,
    )

    assert first.account == second.account
    assert User.objects.filter(username="ada@example.com").count() == 1
