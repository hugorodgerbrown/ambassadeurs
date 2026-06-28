# Tests for the account service functions.

import pytest
from django.core import mail
from django.test import RequestFactory, override_settings

from accounts.services import send_confirmation_email, send_login_email, update_account
from matching.models import Registration
from tests.accounts.factories import UserFactory
from tests.matching.factories import RegistrationFactory

pytestmark = pytest.mark.django_db


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


# ---------------------------------------------------------------------------
# send_confirmation_email (VERB-25)
# ---------------------------------------------------------------------------


def test_send_confirmation_email_sends_mail_and_returns_confirm_url() -> None:
    """send_confirmation_email sends one email and returns the confirm URL."""
    registration = RegistrationFactory.create(status=Registration.Status.UNVERIFIED)
    request = RequestFactory().get("/")
    request.META["SERVER_NAME"] = "testserver"
    request.META["SERVER_PORT"] = "80"
    mail.outbox.clear()

    confirm_url = send_confirmation_email(request, registration)

    assert len(mail.outbox) == 1
    assert mail.outbox[0].to == [registration.user.email]
    assert "register/confirm/" in mail.outbox[0].body
    assert confirm_url.startswith("http://testserver/")
    assert "register/confirm/" in confirm_url


# ---------------------------------------------------------------------------
# send_login_email (VERB-46 magic-link login)
# ---------------------------------------------------------------------------


def test_send_login_email_sends_mail_to_user() -> None:
    """send_login_email sends one email to the user's address."""
    user = UserFactory.create(email="ada@example.com")
    request = RequestFactory().get("/")
    request.META["SERVER_NAME"] = "testserver"
    request.META["SERVER_PORT"] = "80"
    mail.outbox.clear()

    send_login_email(request, user)

    assert len(mail.outbox) == 1
    assert mail.outbox[0].to == ["ada@example.com"]


def test_send_login_email_body_contains_verify_url() -> None:
    """send_login_email includes the magic-link verify URL in the body."""
    user = UserFactory.create()
    request = RequestFactory().get("/")
    request.META["SERVER_NAME"] = "testserver"
    request.META["SERVER_PORT"] = "80"
    mail.outbox.clear()

    verify_url = send_login_email(request, user)

    assert len(mail.outbox) == 1
    assert "account/login/" in mail.outbox[0].body
    assert verify_url in mail.outbox[0].body


def test_send_login_email_returns_verify_url() -> None:
    """send_login_email returns the absolute verify URL."""
    user = UserFactory.create()
    request = RequestFactory().get("/")
    request.META["SERVER_NAME"] = "testserver"
    request.META["SERVER_PORT"] = "80"
    mail.outbox.clear()

    verify_url = send_login_email(request, user)

    assert verify_url.startswith("http://testserver/")
    assert "account/login/" in verify_url


@override_settings(DEBUG=True)
def test_send_login_email_logs_url_under_debug(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Under DEBUG, send_login_email logs the verify URL."""
    import logging

    user = UserFactory.create()
    request = RequestFactory().get("/")
    request.META["SERVER_NAME"] = "testserver"
    request.META["SERVER_PORT"] = "80"
    mail.outbox.clear()

    with caplog.at_level(logging.INFO, logger="accounts.services"):
        verify_url = send_login_email(request, user)

    assert verify_url in caplog.text


def test_send_confirmation_email_subject_contains_confirm_phrase() -> None:
    """The confirmation email subject references email confirmation."""
    registration = RegistrationFactory.create(status=Registration.Status.UNVERIFIED)
    request = RequestFactory().get("/")
    request.META["SERVER_NAME"] = "testserver"
    request.META["SERVER_PORT"] = "80"
    mail.outbox.clear()

    send_confirmation_email(request, registration)

    assert len(mail.outbox) == 1
    assert "Confirm" in mail.outbox[0].subject
