# Tests for the registration form.

import pytest

from matching.forms import RegistrationForm
from matching.models import Registration
from tests.accounts.factories import UserFactory
from tests.matching.factories import RegistrationFactory

pytestmark = pytest.mark.django_db


def _valid_ambassador_data(**overrides: object) -> dict[str, object]:
    """Return a complete, valid POST payload for the ambassador form."""
    data: dict[str, object] = {
        "first_name": "Ada",
        "last_name": "Lovelace",
        "email": "ada@example.com",
        "prior_pass": Registration.PriorPass.SEASONAL,
        "attestation": True,
    }
    data.update(overrides)
    return data


def _valid_referee_data(**overrides: object) -> dict[str, object]:
    """Return a complete, valid POST payload for the referee form."""
    data: dict[str, object] = {
        "first_name": "Grace",
        "last_name": "Hopper",
        "email": "grace@example.com",
        "attestation": True,
    }
    data.update(overrides)
    return data


def test_valid_ambassador_form_lowercases_email() -> None:
    """A valid ambassador form cleans and lowercases the email."""
    form = RegistrationForm(
        role=Registration.Role.AMBASSADOR,
        data=_valid_ambassador_data(email="ADA@Example.COM"),
    )
    assert form.is_valid(), form.errors
    assert form.cleaned_data["email"] == "ada@example.com"


def test_attestation_is_required() -> None:
    """The mandatory attestation checkbox must be ticked."""
    form = RegistrationForm(
        role=Registration.Role.AMBASSADOR,
        data=_valid_ambassador_data(attestation=False),
    )
    assert not form.is_valid()
    assert "attestation" in form.errors


def test_ambassador_form_has_prior_pass_field() -> None:
    """The ambassador form includes the prior_pass select."""
    form = RegistrationForm(role=Registration.Role.AMBASSADOR)
    assert "prior_pass" in form.fields


def test_referee_form_has_no_prior_pass_field() -> None:
    """The referee form does not expose the prior_pass select."""
    form = RegistrationForm(role=Registration.Role.REFEREE)
    assert "prior_pass" not in form.fields


def test_referee_prior_pass_resolves_to_none_in_clean() -> None:
    """A valid referee form always resolves prior_pass to NONE."""
    form = RegistrationForm(
        role=Registration.Role.REFEREE,
        data=_valid_referee_data(),
    )
    assert form.is_valid(), form.errors
    assert form.cleaned_data["prior_pass"] == Registration.PriorPass.NONE


def test_authenticated_form_drops_email_and_prefills_name() -> None:
    """When a user is supplied the email field is removed and name prefilled."""
    user = UserFactory.create(first_name="Ada", last_name="Lovelace")
    form = RegistrationForm(role=Registration.Role.REFEREE, user=user)
    assert "email" not in form.fields
    assert form.fields["first_name"].initial == "Ada"


def test_authenticated_duplicate_registration_rejected() -> None:
    """A signed-in user who already has a registration is rejected."""
    registration = RegistrationFactory.create(role=Registration.Role.AMBASSADOR)
    form = RegistrationForm(
        role=Registration.Role.AMBASSADOR,
        user=registration.user,
        data={
            "first_name": "Ada",
            "last_name": "Lovelace",
            "prior_pass": Registration.PriorPass.SEASONAL,
            "attestation": True,
        },
    )
    assert not form.is_valid()
    assert form.non_field_errors()


def test_duplicate_email_rejected() -> None:
    """A second registration with the same email is rejected."""
    user = UserFactory.create(email="ada@example.com")
    RegistrationFactory.create(user=user)
    form = RegistrationForm(
        role=Registration.Role.REFEREE,
        data=_valid_referee_data(email="ada@example.com"),
    )
    assert not form.is_valid()
    assert form.non_field_errors()


def test_ambassador_prior_pass_invalid_choice_rejected() -> None:
    """An invalid prior_pass value on the ambassador form is rejected."""
    form = RegistrationForm(
        role=Registration.Role.AMBASSADOR,
        data=_valid_ambassador_data(prior_pass="INVALID"),
    )
    assert not form.is_valid()
    assert "prior_pass" in form.errors
