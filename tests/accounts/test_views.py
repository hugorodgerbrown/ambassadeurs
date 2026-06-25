# Tests for the account self-service views.

import pytest
from allauth.account.models import EmailAddress
from django.contrib.auth import SESSION_KEY
from django.contrib.auth.models import User
from django.test import Client, override_settings
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
    assert SESSION_KEY not in client.session


def test_logout_get_renders_styled_page() -> None:
    """A GET to the logout URL renders our styled override, not the allauth default."""
    user = UserFactory.create()
    client = Client()
    client.force_login(user)
    response = client.get(reverse("account_logout"))
    assert response.status_code == 200
    assert "account/logout.html" in [t.name for t in response.templates]
    assert b"btn--primary" in response.content


# ---------------------------------------------------------------------------
# account_detail — email_verified context variable (VERB-25)
# ---------------------------------------------------------------------------


def test_detail_passes_email_verified_true_when_address_is_verified() -> None:
    """account_detail passes email_verified=True when an EmailAddress is verified."""
    user = UserFactory.create()
    EmailAddress.objects.create(
        user=user, email=user.email, verified=True, primary=True
    )
    client = Client()
    client.force_login(user)
    response = client.get(reverse("accounts:detail"))
    assert response.status_code == 200
    assert response.context["email_verified"] is True


def test_detail_passes_email_verified_false_when_no_verified_address() -> None:
    """account_detail passes email_verified=False when no verified address exists."""
    user = UserFactory.create()
    client = Client()
    client.force_login(user)
    response = client.get(reverse("accounts:detail"))
    assert response.status_code == 200
    assert response.context["email_verified"] is False


def test_detail_shows_tick_when_email_verified() -> None:
    """The detail page renders a tick SVG when the user's email is verified."""
    user = UserFactory.create()
    EmailAddress.objects.create(
        user=user, email=user.email, verified=True, primary=True
    )
    client = Client()
    client.force_login(user)
    response = client.get(reverse("accounts:detail"))
    assert b"Email verified" in response.content


def test_detail_shows_unverified_label_when_email_not_verified() -> None:
    """The detail page shows 'Unverified' when no verified EmailAddress exists."""
    user = UserFactory.create()
    client = Client()
    client.force_login(user)
    response = client.get(reverse("accounts:detail"))
    assert b"Unverified" in response.content


def test_detail_shows_resend_button_for_pending_unverified() -> None:
    """The detail page shows a resend button when the registration is PENDING."""
    registration = RegistrationFactory.create(status=Registration.Status.PENDING)
    client = Client()
    client.force_login(registration.user)
    response = client.get(reverse("accounts:detail"))
    assert b"Resend confirmation email" in response.content
    resend_url = reverse("accounts:resend_confirmation").encode()
    assert resend_url in response.content


def test_detail_hides_resend_button_when_email_verified() -> None:
    """The resend button is not shown once the email is verified."""
    registration = RegistrationFactory.create(status=Registration.Status.WAITING)
    user = registration.user
    EmailAddress.objects.create(
        user=user, email=user.email, verified=True, primary=True
    )
    client = Client()
    client.force_login(user)
    response = client.get(reverse("accounts:detail"))
    assert b"Resend confirmation email" not in response.content


# ---------------------------------------------------------------------------
# Status pill labels (VERB-25)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("status", "expected_label"),
    [
        (Registration.Status.PENDING, b"Pending email verification"),
        (Registration.Status.WAITING, b"Available for match"),
        (Registration.Status.MATCHED, b"Matched"),
        (Registration.Status.CONFIRMED, b"Confirmed"),
        (Registration.Status.WITHDRAWN, b"Withdrawn"),
        (Registration.Status.SUSPENDED, b"Suspended"),
    ],
)
def test_detail_status_pill_label(status: str, expected_label: bytes) -> None:
    """Each Registration.Status value renders with the correct richer label."""
    registration = RegistrationFactory.create(status=status)
    client = Client()
    client.force_login(registration.user)
    response = client.get(reverse("accounts:detail"))
    assert response.status_code == 200
    assert expected_label in response.content


# ---------------------------------------------------------------------------
# account_resend_confirmation (VERB-25)
# ---------------------------------------------------------------------------


def test_resend_anonymous_redirects_to_login() -> None:
    """An anonymous POST to resend-confirmation is redirected to login."""
    response = Client().post(reverse("accounts:resend_confirmation"))
    assert response.status_code == 302
    assert reverse("account_login") in response.url


def test_resend_get_redirects_to_detail_without_sending() -> None:
    """A GET to resend-confirmation redirects to account detail and sends no email."""
    from django.core import mail

    registration = RegistrationFactory.create(status=Registration.Status.PENDING)
    client = Client()
    client.force_login(registration.user)
    mail.outbox.clear()
    response = client.get(reverse("accounts:resend_confirmation"))
    assert response.status_code == 302
    assert response.url == reverse("accounts:detail")
    assert len(mail.outbox) == 0


def test_resend_post_sends_email_for_pending_registration() -> None:
    """A POST for a PENDING registration sends a confirmation email."""
    from django.core import mail

    registration = RegistrationFactory.create(status=Registration.Status.PENDING)
    client = Client()
    client.force_login(registration.user)
    mail.outbox.clear()
    response = client.post(reverse("accounts:resend_confirmation"))
    assert response.status_code == 302
    assert response.url == reverse("accounts:detail")
    assert len(mail.outbox) == 1
    assert "register/confirm/" in mail.outbox[0].body


def test_resend_post_shows_success_message() -> None:
    """A successful resend sets a success message."""
    registration = RegistrationFactory.create(status=Registration.Status.PENDING)
    client = Client()
    client.force_login(registration.user)
    response = client.post(reverse("accounts:resend_confirmation"), follow=True)
    messages_list = list(response.context["messages"])
    assert any("resent" in str(m).lower() for m in messages_list)


def test_resend_post_error_when_no_pending_registration() -> None:
    """A POST with no PENDING registration sets an error message and sends no email."""
    from django.core import mail

    registration = RegistrationFactory.create(status=Registration.Status.WAITING)
    client = Client()
    client.force_login(registration.user)
    mail.outbox.clear()
    response = client.post(reverse("accounts:resend_confirmation"), follow=True)
    assert response.status_code == 200
    messages_list = list(response.context["messages"])
    assert any("already" in str(m).lower() for m in messages_list)
    assert len(mail.outbox) == 0


@override_settings(DEBUG=True)
def test_resend_post_stashes_url_in_session_under_debug() -> None:
    """Under DEBUG, resend stashes the confirm URL in the session."""
    registration = RegistrationFactory.create(status=Registration.Status.PENDING)
    client = Client()
    client.force_login(registration.user)
    client.post(reverse("accounts:resend_confirmation"))
    assert "debug_verify_url" in client.session


@override_settings(DEBUG=False)
def test_resend_post_does_not_stash_url_outside_debug() -> None:
    """Outside DEBUG, the confirm URL is not stashed in the session."""
    registration = RegistrationFactory.create(status=Registration.Status.PENDING)
    client = Client()
    client.force_login(registration.user)
    client.post(reverse("accounts:resend_confirmation"))
    assert "debug_verify_url" not in client.session


# ---------------------------------------------------------------------------
# DEBUG panel in base.html (VERB-25)
# ---------------------------------------------------------------------------


@override_settings(DEBUG=True)
def test_debug_panel_shown_on_detail_when_url_in_session() -> None:
    """Under DEBUG, account_detail renders the dev panel when session has the URL."""
    registration = RegistrationFactory.create(status=Registration.Status.PENDING)
    client = Client()
    client.force_login(registration.user)
    # Plant the URL in the session (as the resend view would do).
    session = client.session
    session["debug_verify_url"] = "http://testserver/register/confirm/FAKE/"
    session.save()
    response = client.get(reverse("accounts:detail"))
    assert b"Development shortcut" in response.content
    assert b"Development panel" in response.content


@override_settings(DEBUG=False)
def test_debug_panel_absent_outside_debug() -> None:
    """Outside DEBUG, account_detail never renders the dev panel."""
    user = UserFactory.create()
    client = Client()
    client.force_login(user)
    response = client.get(reverse("accounts:detail"))
    assert b"Development panel" not in response.content


@override_settings(DEBUG=True, INTERNAL_IPS=["127.0.0.1"])
def test_debug_panel_shown_without_verify_url() -> None:
    """Under DEBUG the panel frame appears even when no debug_verify_url is set.

    This covers the requirement that the panel is site-wide, not limited to pages
    that inject debug_verify_url into context.
    """
    user = UserFactory.create()
    client = Client()
    client.force_login(user)
    # No session key planted — no debug_verify_url in context.
    response = client.get(reverse("accounts:detail"))
    assert b"Development panel" in response.content
    assert b"Development shortcut" not in response.content


@override_settings(DEBUG=True, INTERNAL_IPS=["127.0.0.1"])
def test_debug_panel_shown_with_verify_url_shows_shortcut() -> None:
    """Under DEBUG the shortcut link appears inside the panel when the session has the URL."""
    registration = RegistrationFactory.create(status=Registration.Status.PENDING)
    client = Client()
    client.force_login(registration.user)
    session = client.session
    session["debug_verify_url"] = "http://testserver/register/confirm/FAKE/"
    session.save()
    response = client.get(reverse("accounts:detail"))
    assert b"Development panel" in response.content
    assert b"Development shortcut" in response.content
