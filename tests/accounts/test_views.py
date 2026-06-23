# Tests for the account self-service views.

import pytest
from django.contrib.auth.models import User
from django.test import Client
from django.urls import reverse

from accounts.models import Account
from matching.models import Registration
from tests.accounts.factories import AccountFactory, UserFactory
from tests.matching.factories import RegistrationFactory

pytestmark = pytest.mark.django_db


def test_detail_requires_login() -> None:
    """Anonymous users are redirected away from the account page."""
    response = Client().get(reverse("accounts:detail"))
    assert response.status_code == 302
    assert reverse("account_login") in response.url


def test_detail_renders_with_registration_role_readonly() -> None:
    """The detail page shows the user's email and their registration role."""
    account = AccountFactory.create(user=UserFactory.create(email="ada@example.com"))
    RegistrationFactory.create(account=account, role=Registration.Role.AMBASSADOR)
    client = Client()
    client.force_login(account.user)
    response = client.get(reverse("accounts:detail"))
    assert response.status_code == 200
    assert b"ada@example.com" in response.content
    assert b"Ambassador" in response.content
    assert b"role is fixed" in response.content


def test_edit_get_renders_form() -> None:
    """The edit page renders the prefilled form."""
    user = UserFactory.create(first_name="Ada")
    client = Client()
    client.force_login(user)
    response = client.get(reverse("accounts:edit"))
    assert response.status_code == 200
    assert "accounts/edit.html" in [t.name for t in response.templates]


def test_edit_post_updates_details() -> None:
    """A valid edit updates the name, phone and language and redirects."""
    user = UserFactory.create(first_name="Ada", last_name="Lovelace")
    client = Client()
    client.force_login(user)
    response = client.post(
        reverse("accounts:edit"),
        {
            "first_name": "Augusta",
            "last_name": "King",
            "phone": "+41790000000",
            "preferred_language": "fr",
        },
    )
    assert response.status_code == 302
    assert response.url == reverse("accounts:detail")
    user.refresh_from_db()
    assert user.first_name == "Augusta"
    account = Account.objects.get(user=user)
    assert account.phone == "+41790000000"
    assert account.preferred_language == "fr"


def test_edit_post_invalid_redisplays_form() -> None:
    """An invalid edit (missing required name) re-renders the form."""
    user = UserFactory.create(first_name="Ada", last_name="Lovelace")
    client = Client()
    client.force_login(user)
    response = client.post(
        reverse("accounts:edit"),
        {"first_name": "", "last_name": "King", "phone": "", "preferred_language": ""},
    )
    assert response.status_code == 200
    user.refresh_from_db()
    assert user.first_name == "Ada"


def test_delete_get_renders_confirmation() -> None:
    """The delete page renders a confirmation."""
    user = UserFactory.create()
    client = Client()
    client.force_login(user)
    response = client.get(reverse("accounts:delete"))
    assert response.status_code == 200
    assert "accounts/delete.html" in [t.name for t in response.templates]


def test_delete_post_removes_user_and_registrations() -> None:
    """Deleting the account removes the user and cascades registrations."""
    account = AccountFactory.create()
    RegistrationFactory.create(account=account)
    user_pk = account.user.pk
    client = Client()
    client.force_login(account.user)
    response = client.post(reverse("accounts:delete"))
    assert response.status_code == 302
    assert response.url == reverse("public:home")
    assert not User.objects.filter(pk=user_pk).exists()
    assert not Registration.objects.exists()
    assert not Account.objects.filter(pk=account.pk).exists()
