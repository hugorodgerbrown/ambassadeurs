# Tests for the registration form.

import pytest

from matching.forms import RegistrationForm
from matching.models import PriceCategory, Registration
from tests.accounts.factories import AccountFactory, UserFactory
from tests.matching.factories import (
    PriceCategoryFactory,
    RegistrationFactory,
    SeasonFactory,
)

pytestmark = pytest.mark.django_db


def _valid_data(category: PriceCategory, **overrides: object) -> dict[str, object]:
    """Return a complete, valid POST payload for the form."""
    data: dict[str, object] = {
        "first_name": "Ada",
        "last_name": "Lovelace",
        "email": "ada@example.com",
        "price_category": category.pk,
        "attestation": True,
    }
    data.update(overrides)
    return data


def test_valid_form_lowercases_email() -> None:
    """A valid form cleans and lowercases the email."""
    season = SeasonFactory.create()
    category = PriceCategoryFactory.create(season=season)
    form = RegistrationForm(
        role=Registration.Role.AMBASSADOR,
        season=season,
        data=_valid_data(category, email="ADA@Example.COM"),
    )
    assert form.is_valid(), form.errors
    assert form.cleaned_data["email"] == "ada@example.com"


def test_attestation_is_required() -> None:
    """The mandatory attestation checkbox must be ticked."""
    season = SeasonFactory.create()
    category = PriceCategoryFactory.create(season=season)
    form = RegistrationForm(
        role=Registration.Role.AMBASSADOR,
        season=season,
        data=_valid_data(category, attestation=False),
    )
    assert not form.is_valid()
    assert "attestation" in form.errors


def test_price_category_limited_to_active_season() -> None:
    """A category from another season is not a valid choice."""
    season = SeasonFactory.create()
    other_category = PriceCategoryFactory.create(season=SeasonFactory.create())
    form = RegistrationForm(
        role=Registration.Role.AMBASSADOR,
        season=season,
        data=_valid_data(other_category),
    )
    assert not form.is_valid()
    assert "price_category" in form.errors


def test_authenticated_form_drops_email_and_prefills_name() -> None:
    """When a user is supplied the email field is removed and name prefilled."""
    season = SeasonFactory.create()
    PriceCategoryFactory.create(season=season)
    user = UserFactory.create(first_name="Ada", last_name="Lovelace")
    form = RegistrationForm(role=Registration.Role.REFEREE, season=season, user=user)
    assert "email" not in form.fields
    assert form.fields["first_name"].initial == "Ada"


def test_authenticated_duplicate_registration_rejected() -> None:
    """A signed-in user already registered this season is rejected."""
    season = SeasonFactory.create()
    category = PriceCategoryFactory.create(season=season)
    account = AccountFactory.create()
    RegistrationFactory.create(season=season, account=account, price_category=category)
    form = RegistrationForm(
        role=Registration.Role.AMBASSADOR,
        season=season,
        user=account.user,
        data={
            "first_name": "Ada",
            "last_name": "Lovelace",
            "price_category": category.pk,
            "attestation": True,
        },
    )
    assert not form.is_valid()
    assert form.non_field_errors()


def test_duplicate_email_in_season_rejected() -> None:
    """A second registration with the same email in the season is rejected."""
    season = SeasonFactory.create()
    category = PriceCategoryFactory.create(season=season)
    account = AccountFactory.create(user=UserFactory.create(email="ada@example.com"))
    RegistrationFactory.create(season=season, account=account, price_category=category)
    form = RegistrationForm(
        role=Registration.Role.REFEREE,
        season=season,
        data=_valid_data(category, email="ada@example.com"),
    )
    assert not form.is_valid()
    assert form.non_field_errors()
