# Tests for the account service functions.

import pytest
from allauth.account.models import EmailAddress
from django.contrib.auth.models import User

from accounts.services import get_or_create_participant_user, update_account
from tests.accounts.factories import UserFactory
from tests.matching.factories import RegistrationFactory

pytestmark = pytest.mark.django_db


def test_creates_passwordless_user_with_verified_email() -> None:
    """A new participant user is passwordless with a verified email address."""
    user = get_or_create_participant_user("ADA@example.com")
    assert user.username == "ada@example.com"
    assert not user.has_usable_password()
    assert EmailAddress.objects.filter(
        user=user, email="ada@example.com", verified=True
    ).exists()


def test_is_idempotent_for_the_same_email() -> None:
    """Calling twice reuses the same user and does not duplicate it."""
    first = get_or_create_participant_user("ada@example.com")
    second = get_or_create_participant_user("ada@example.com")
    assert first == second
    assert User.objects.filter(username="ada@example.com").count() == 1


def test_update_account_saves_name_on_user() -> None:
    """update_account writes the new name onto the Django User."""
    user = UserFactory.create(first_name="Ada", last_name="Lovelace")
    update_account(
        user=user,
        first_name="Augusta",
        last_name="King",
    )
    user.refresh_from_db()
    assert user.first_name == "Augusta"
    assert user.last_name == "King"


def test_update_account_writes_phone_and_language_to_registration() -> None:
    """update_account writes phone and language onto the user's Registration."""
    registration = RegistrationFactory.create()
    update_account(
        user=registration.user,
        first_name="Ada",
        last_name="Lovelace",
        phone="+41790000001",
        preferred_language="fr",
    )
    registration.refresh_from_db()
    assert registration.phone == "+41790000001"
    assert registration.preferred_language == "fr"


def test_update_account_without_registration_does_not_raise() -> None:
    """update_account is a no-op for phone/language when user has no registration."""
    user = UserFactory.create(first_name="Ada", last_name="Lovelace")
    # Should not raise even though there is no registration.
    update_account(user=user, first_name="Augusta", last_name="King")
    user.refresh_from_db()
    assert user.first_name == "Augusta"
