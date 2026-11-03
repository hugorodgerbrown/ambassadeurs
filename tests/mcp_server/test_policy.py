# Tests for who may connect an MCP client, and how the consent page names them.

import pytest

from mcp_server.policy import account_name, staff_only
from tests.accounts.factories import UserFactory

pytestmark = pytest.mark.django_db


@pytest.mark.parametrize(
    ("is_staff", "is_active", "allowed"),
    [(True, True, True), (False, True, False), (True, False, False)],
)
def test_staff_only(is_staff: bool, is_active: bool, allowed: bool) -> None:
    """Only an active staff member may connect."""
    user = UserFactory.create(is_staff=is_staff, is_active=is_active)
    assert staff_only(user) is allowed


def test_superuser_without_staff_flag_is_refused() -> None:
    """The rule reads the staff flag, nothing else."""
    assert staff_only(UserFactory.create(is_superuser=True)) is False


def test_account_name_masks_the_email() -> None:
    """The consent page names the account by its masked address."""
    user = UserFactory.create(username="hugo", email="hugo@example.com")
    assert account_name(user) == "h•••@example.com"


def test_account_name_falls_back_to_the_username() -> None:
    """An account with no email address is named by its username."""
    assert account_name(UserFactory.create(username="ops", email="")) == "ops"
