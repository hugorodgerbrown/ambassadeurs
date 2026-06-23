# Tests for the accounts app models.
#
# Account has been removed. The only model in the accounts app is the default
# Django User. We test that the reverse registration accessor works correctly
# (set up by matching.Registration's OneToOneField to User).

import pytest

from tests.accounts.factories import UserFactory
from tests.matching.factories import RegistrationFactory

pytestmark = pytest.mark.django_db


def test_user_registration_reverse_accessor() -> None:
    """The reverse OneToOneField exposes the registration on the user."""
    registration = RegistrationFactory.create()
    assert registration.user.registration == registration


def test_user_without_registration_raises_does_not_exist() -> None:
    """A user with no registration raises Registration.DoesNotExist on access."""
    from matching.models import Registration

    user = UserFactory.create()
    with pytest.raises(Registration.DoesNotExist):
        _ = user.registration  # type: ignore[attr-defined]
