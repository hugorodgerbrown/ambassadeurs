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
        "prior_pass_attestation": True,
        "terms_accepted": True,
    }
    data.update(overrides)
    return data


def _valid_referee_data(**overrides: object) -> dict[str, object]:
    """Return a complete, valid POST payload for the referee form."""
    data: dict[str, object] = {
        "first_name": "Grace",
        "last_name": "Hopper",
        "email": "grace@example.com",
        "prior_pass_attestation": True,
        "terms_accepted": True,
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


def test_prior_pass_attestation_is_required() -> None:
    """The eligibility declaration checkbox must be ticked."""
    form = RegistrationForm(
        role=Registration.Role.AMBASSADOR,
        data=_valid_ambassador_data(prior_pass_attestation=False),
    )
    assert not form.is_valid()
    assert "prior_pass_attestation" in form.errors


def test_terms_accepted_is_required() -> None:
    """The Terms of Use acceptance checkbox must be ticked."""
    form = RegistrationForm(
        role=Registration.Role.AMBASSADOR,
        data=_valid_ambassador_data(terms_accepted=False),
    )
    assert not form.is_valid()
    assert "terms_accepted" in form.errors


def test_both_checkboxes_present_makes_form_valid() -> None:
    """The form is valid when both confirmation checkboxes are ticked."""
    form = RegistrationForm(
        role=Registration.Role.AMBASSADOR,
        data=_valid_ambassador_data(),
    )
    assert form.is_valid(), form.errors


def test_referee_both_checkboxes_present_makes_form_valid() -> None:
    """The referee form is valid when both confirmation checkboxes are ticked."""
    form = RegistrationForm(
        role=Registration.Role.REFEREE,
        data=_valid_referee_data(),
    )
    assert form.is_valid(), form.errors


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
            "prior_pass_attestation": True,
            "terms_accepted": True,
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


def test_accepted_statements_returns_ambassador_specific_eligibility_label() -> None:
    """accepted_statements() returns a 2-item list; ambassador wording is first."""
    form = RegistrationForm(
        role=Registration.Role.AMBASSADOR,
        data=_valid_ambassador_data(),
    )
    assert form.is_valid(), form.errors
    statements = form.accepted_statements()
    assert len(statements) == 2
    assert "2024/25 or 2025/26" in statements[0]
    assert "season or annual" in statements[0]
    # The eligibility statement must not contain the referee negation.
    assert "not held" not in statements[0]
    assert statements[1] == "I have read and agree to the Terms of Use"


def test_accepted_statements_returns_referee_specific_eligibility_label() -> None:
    """accepted_statements() returns a 2-item list; referee wording is first."""
    form = RegistrationForm(
        role=Registration.Role.REFEREE,
        data=_valid_referee_data(),
    )
    assert form.is_valid(), form.errors
    statements = form.accepted_statements()
    assert len(statements) == 2
    assert "not purchase" in statements[0]
    assert "2024/25 or 2025/26" in statements[0]
    assert statements[1] == "I have read and agree to the Terms of Use"


# ---------------------------------------------------------------------------
# nationality field
# ---------------------------------------------------------------------------


def test_nationality_field_present_in_form() -> None:
    """RegistrationForm always exposes the nationality select field."""
    form = RegistrationForm(role=Registration.Role.AMBASSADOR)
    assert "nationality" in form.fields


def test_nationality_field_is_not_required() -> None:
    """The nationality field is optional (required=False)."""
    form = RegistrationForm(role=Registration.Role.AMBASSADOR)
    assert form.fields["nationality"].required is False


def test_form_valid_with_nationality_supplied() -> None:
    """Form is valid when a country code is supplied for nationality."""
    form = RegistrationForm(
        role=Registration.Role.AMBASSADOR,
        data=_valid_ambassador_data(nationality="CH"),
    )
    assert form.is_valid(), form.errors
    assert form.cleaned_data["nationality"] == "CH"


def test_form_valid_with_nationality_omitted() -> None:
    """Form is valid when nationality is omitted; cleaned value is empty string."""
    form = RegistrationForm(
        role=Registration.Role.AMBASSADOR,
        data=_valid_ambassador_data(),
    )
    assert form.is_valid(), form.errors
    assert form.cleaned_data.get("nationality", "") == ""


def test_referee_form_valid_with_nationality_supplied() -> None:
    """Referee form is valid when a country code is supplied for nationality."""
    form = RegistrationForm(
        role=Registration.Role.REFEREE,
        data=_valid_referee_data(nationality="FR"),
    )
    assert form.is_valid(), form.errors
    assert form.cleaned_data["nationality"] == "FR"
