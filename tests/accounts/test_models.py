# Tests for the accounts app models.
#
# Account has been removed; auth uses the default Django User. We test that the
# reverse registration accessor works correctly (set up by
# matching.Registration's OneToOneField to User), and the Passkey model kit.

import pytest

from accounts.models import Passkey
from tests.accounts.factories import PasskeyFactory, UserFactory
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


# ---------------------------------------------------------------------------
# Passkey (ADR 0030)
# ---------------------------------------------------------------------------


def test_passkey_to_string_is_its_name() -> None:
    """A passkey reads as its device name."""
    passkey = PasskeyFactory.create(name="Passkey on Mac")
    assert str(passkey) == passkey.to_string() == "Passkey on Mac"


def test_passkey_queryset_for_user() -> None:
    """``for_user`` returns only that user's passkeys."""
    mine = PasskeyFactory.create()
    PasskeyFactory.create()
    assert list(Passkey.objects.for_user(mine.user)) == [mine]


def test_passkeys_go_with_their_user() -> None:
    """Deleting a user deletes their passkeys."""
    passkey = PasskeyFactory.create()
    passkey.user.delete()
    assert not Passkey.objects.exists()
