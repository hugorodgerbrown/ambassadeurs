# Tests for the account self-service views.

import pytest
from django.contrib.auth.models import User
from django.test import Client
from django.urls import reverse

from matching.models import Registration
from tests.accounts.factories import UserFactory
from tests.matching.factories import RegistrationFactory

pytestmark = pytest.mark.django_db


def test_detail_requires_login() -> None:
    """Anonymous users are redirected away from the account page."""
    response = Client().get(reverse("accounts:detail"))
    assert response.status_code == 302
    assert reverse("account_login") in response.url


def test_detail_renders_with_registration_role_readonly() -> None:
    """The detail page shows the user's email and their registration role."""
    registration = RegistrationFactory.create(
        user=UserFactory.create(email="ada@example.com"),
        role=Registration.Role.AMBASSADOR,
    )
    client = Client()
    client.force_login(registration.user)
    response = client.get(reverse("accounts:detail"))
    assert response.status_code == 200
    assert b"ada@example.com" in response.content
    assert b"Ambassador" in response.content
    assert b"role is fixed" in response.content


def test_detail_without_registration_shows_register_link() -> None:
    """A user without a registration sees a prompt to register."""
    user = UserFactory.create()
    client = Client()
    client.force_login(user)
    response = client.get(reverse("accounts:detail"))
    assert response.status_code == 200
    assert b"Register now" in response.content


def test_edit_get_renders_form() -> None:
    """The edit page renders the prefilled form."""
    user = UserFactory.create(first_name="Ada")
    client = Client()
    client.force_login(user)
    response = client.get(reverse("accounts:edit"))
    assert response.status_code == 200
    assert "accounts/edit.html" in [t.name for t in response.templates]


def test_edit_post_updates_name_and_registration_fields() -> None:
    """A valid edit updates the name, phone and language and redirects."""
    registration = RegistrationFactory.create(
        user=UserFactory.create(first_name="Ada", last_name="Lovelace"),
    )
    user = registration.user
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
    registration.refresh_from_db()
    assert registration.phone == "+41790000000"
    assert registration.preferred_language == "fr"


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


def test_delete_post_removes_user_and_registration() -> None:
    """Deleting the account removes the user and cascades the registration."""
    registration = RegistrationFactory.create()
    user_pk = registration.user.pk
    client = Client()
    client.force_login(registration.user)
    response = client.post(reverse("accounts:delete"))
    assert response.status_code == 302
    assert response.url == reverse("public:home")
    assert not User.objects.filter(pk=user_pk).exists()
    assert not Registration.objects.exists()


def test_logout_via_post_logs_out_and_redirects() -> None:
    """A POST to the logout URL logs the user out and redirects to the home page."""
    user = UserFactory.create()
    client = Client()
    client.force_login(user)
    response = client.post(reverse("account_logout"))
    assert response.status_code == 302
    assert response.url == "/"
    assert "_auth_user_id" not in client.session


def test_logout_get_renders_styled_page() -> None:
    """A GET to the logout URL renders our styled override, not the allauth default."""
    user = UserFactory.create()
    client = Client()
    client.force_login(user)
    response = client.get(reverse("account_logout"))
    assert response.status_code == 200
    assert "account/logout.html" in [t.name for t in response.templates]
    assert b"btn--primary" in response.content
