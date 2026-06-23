# Tests for the Account model.

import pytest

from accounts.models import Account
from tests.accounts.factories import AccountFactory, UserFactory

pytestmark = pytest.mark.django_db


def test_to_string_references_the_user() -> None:
    """Account.to_string names the underlying user."""
    user = UserFactory.create(username="alice")
    account = AccountFactory.create(user=user)
    assert str(account) == f"Account for {user}"


def test_for_user_filters_to_the_owner() -> None:
    """AccountQuerySet.for_user returns only that user's account."""
    account = AccountFactory.create()
    AccountFactory.create()
    assert list(Account.objects.for_user(account.user)) == [account]


def test_account_is_one_to_one_with_user() -> None:
    """The reverse accessor resolves the account from the user."""
    account = AccountFactory.create()
    assert account.user.account == account
