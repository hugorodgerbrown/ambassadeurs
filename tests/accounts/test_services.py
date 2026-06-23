# Tests for the account service functions.

import pytest
from allauth.account.models import EmailAddress
from django.contrib.auth.models import User

from accounts.services import get_or_create_participant_user

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
