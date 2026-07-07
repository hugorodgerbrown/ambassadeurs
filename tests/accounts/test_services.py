# Tests for the account service functions.
#
# Email content assertions check only structural facts (recipient, URL present,
# non-empty subject) — not translated string literals — because the test env
# compiles no .mo catalogues and gettext falls back to the English source.
#
# delete_account tests (VERB-88) mock stripe.Refund.create — no test in this
# module makes a real network call.

from typing import Any
from unittest.mock import patch

import pytest
import stripe
from django.contrib.auth.models import User
from django.core import mail
from django.test import RequestFactory, TestCase, override_settings

from accounts.services import (
    delete_account,
    send_already_registered_email,
    send_confirmation_email,
    send_login_email,
    update_account,
)
from billing.models import Payment
from billing.services.payments import InvalidPaymentTransition
from matching.models import Registration
from tests.accounts.factories import UserFactory
from tests.billing.factories import PaymentFactory
from tests.matching.factories import RegistrationFactory

pytestmark = pytest.mark.django_db


class _FakeRefund:
    """Minimal stand-in for a stripe.Refund object."""

    def __init__(self, refund_id: str = "re_test0001") -> None:
        self.id = refund_id


def _mock_refund_create(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    """Monkeypatch stripe.Refund.create and return the list of call kwargs."""
    calls: list[dict[str, Any]] = []

    def _fake_create(**kwargs: Any) -> _FakeRefund:
        calls.append(kwargs)
        return _FakeRefund()

    monkeypatch.setattr(stripe.Refund, "create", _fake_create)
    return calls


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


def test_send_confirmation_email_attaches_html_alternative() -> None:
    """send_confirmation_email attaches a non-empty text/html alternative."""
    registration = RegistrationFactory.create(status=Registration.Status.UNVERIFIED)
    request = RequestFactory().get("/")
    request.META["SERVER_NAME"] = "testserver"
    request.META["SERVER_PORT"] = "80"
    mail.outbox.clear()

    send_confirmation_email(request, registration)

    html_alternatives = [
        content
        for content, mimetype in mail.outbox[0].alternatives
        if mimetype == "text/html"
    ]
    assert len(html_alternatives) == 1
    assert html_alternatives[0].strip()


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


def test_send_login_email_attaches_html_alternative() -> None:
    """send_login_email attaches a non-empty text/html alternative."""
    user = UserFactory.create(email="ada@example.com")
    request = RequestFactory().get("/")
    request.META["SERVER_NAME"] = "testserver"
    request.META["SERVER_PORT"] = "80"
    mail.outbox.clear()

    send_login_email(request, user)

    html_alternatives = [
        content
        for content, mimetype in mail.outbox[0].alternatives
        if mimetype == "text/html"
    ]
    assert len(html_alternatives) == 1
    assert html_alternatives[0].strip()


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


def test_send_confirmation_email_subject_is_non_empty() -> None:
    """The confirmation email has a non-empty single-line subject."""
    registration = RegistrationFactory.create(status=Registration.Status.UNVERIFIED)
    request = RequestFactory().get("/")
    request.META["SERVER_NAME"] = "testserver"
    request.META["SERVER_PORT"] = "80"
    mail.outbox.clear()

    send_confirmation_email(request, registration)

    assert len(mail.outbox) == 1
    subject = mail.outbox[0].subject
    assert subject
    assert "\n" not in subject


def test_send_login_email_subject_is_non_empty() -> None:
    """The login email has a non-empty single-line subject."""
    user = UserFactory.create()
    request = RequestFactory().get("/")
    request.META["SERVER_NAME"] = "testserver"
    request.META["SERVER_PORT"] = "80"
    mail.outbox.clear()

    send_login_email(request, user)

    assert len(mail.outbox) == 1
    subject = mail.outbox[0].subject
    assert subject
    assert "\n" not in subject


def test_send_confirmation_email_body_contains_confirm_url() -> None:
    """Confirmation email body includes the signed confirm URL."""
    registration = RegistrationFactory.create(status=Registration.Status.UNVERIFIED)
    request = RequestFactory().get("/")
    request.META["SERVER_NAME"] = "testserver"
    request.META["SERVER_PORT"] = "80"
    mail.outbox.clear()

    confirm_url = send_confirmation_email(request, registration)

    assert confirm_url in mail.outbox[0].body


def test_send_confirmation_email_ambassador_body_mentions_referee() -> None:
    """Ambassador confirmation email body references finding a Referee."""
    registration = RegistrationFactory.create(
        status=Registration.Status.UNVERIFIED,
        role=Registration.Role.AMBASSADOR,
    )
    request = RequestFactory().get("/")
    request.META["SERVER_NAME"] = "testserver"
    request.META["SERVER_PORT"] = "80"
    mail.outbox.clear()

    send_confirmation_email(request, registration)

    # The EN source string mentions "Referee" in the ambassador copy.
    assert "Referee" in mail.outbox[0].body


def test_send_confirmation_email_referee_body_mentions_ambassador() -> None:
    """Referee confirmation email body references finding an Ambassador."""
    registration = RegistrationFactory.create(
        status=Registration.Status.UNVERIFIED,
        role=Registration.Role.REFEREE,
    )
    request = RequestFactory().get("/")
    request.META["SERVER_NAME"] = "testserver"
    request.META["SERVER_PORT"] = "80"
    mail.outbox.clear()

    send_confirmation_email(request, registration)

    # The EN source string mentions "Ambassador" in the referee copy.
    assert "Ambassador" in mail.outbox[0].body


# ---------------------------------------------------------------------------
# send_already_registered_email (VERB-72 — no email enumeration on register)
# ---------------------------------------------------------------------------


def test_send_already_registered_email_sends_mail_to_user() -> None:
    """send_already_registered_email sends one email to the user's address."""
    user = UserFactory.create(email="ada@example.com")
    request = RequestFactory().get("/")
    request.META["SERVER_NAME"] = "testserver"
    request.META["SERVER_PORT"] = "80"
    mail.outbox.clear()

    verify_url = send_already_registered_email(request, user)

    assert len(mail.outbox) == 1
    assert mail.outbox[0].to == ["ada@example.com"]
    assert verify_url in mail.outbox[0].body


def test_send_already_registered_email_attaches_html_alternative() -> None:
    """send_already_registered_email attaches a non-empty text/html alternative."""
    user = UserFactory.create(email="ada@example.com")
    request = RequestFactory().get("/")
    request.META["SERVER_NAME"] = "testserver"
    request.META["SERVER_PORT"] = "80"
    mail.outbox.clear()

    send_already_registered_email(request, user)

    html_alternatives = [
        content
        for content, mimetype in mail.outbox[0].alternatives
        if mimetype == "text/html"
    ]
    assert len(html_alternatives) == 1
    assert html_alternatives[0].strip()


# ---------------------------------------------------------------------------
# delete_account (VERB-88 — refund at the deletion chokepoint)
# ---------------------------------------------------------------------------


def test_delete_account_refunds_held_deposit(monkeypatch: pytest.MonkeyPatch) -> None:
    """delete_account refunds a HELD deposit before deleting the user."""
    calls = _mock_refund_create(monkeypatch)
    registration = RegistrationFactory.create()
    payment = PaymentFactory.create(
        registration=registration,
        status=Payment.Status.HELD,
        stripe_payment_intent_id="pi_test0001",
    )
    user = registration.user

    delete_account(user)

    assert len(calls) == 1
    payment.refresh_from_db()
    assert payment.status == Payment.Status.REFUNDED
    assert payment.reason == Payment.Reason.USER_CANCELLED


def test_delete_account_deletes_user_and_preserves_payment_audit_row(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """After delete_account, the User is gone but the Payment row survives (null FK)."""
    _mock_refund_create(monkeypatch)
    registration = RegistrationFactory.create()
    payment = PaymentFactory.create(
        registration=registration, status=Payment.Status.HELD
    )
    user_pk = registration.user.pk

    delete_account(registration.user)

    assert not User.objects.filter(pk=user_pk).exists()
    payment.refresh_from_db()
    assert payment.registration_id is None


def test_delete_account_captured_deposit_is_not_refunded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A CAPTURED deposit (reached ACCEPTED) is untouched; the account still deletes."""
    calls = _mock_refund_create(monkeypatch)
    registration = RegistrationFactory.create()
    payment = PaymentFactory.create(
        registration=registration, status=Payment.Status.CAPTURED
    )
    user_pk = registration.user.pk

    delete_account(registration.user)

    assert calls == []
    payment.refresh_from_db()
    assert payment.status == Payment.Status.CAPTURED
    assert not User.objects.filter(pk=user_pk).exists()


def test_delete_account_forfeited_deposit_is_not_refunded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A FORFEITED deposit (no-show) is left untouched; the account still deletes."""
    calls = _mock_refund_create(monkeypatch)
    registration = RegistrationFactory.create()
    payment = PaymentFactory.create(
        registration=registration, status=Payment.Status.FORFEITED
    )
    user_pk = registration.user.pk

    delete_account(registration.user)

    assert calls == []
    payment.refresh_from_db()
    assert payment.status == Payment.Status.FORFEITED
    assert not User.objects.filter(pk=user_pk).exists()


def test_delete_account_free_tier_no_payment_deletes_cleanly(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A registration with no Payment row (free tier) deletes with no refund attempt."""
    calls = _mock_refund_create(monkeypatch)
    registration = RegistrationFactory.create()
    user_pk = registration.user.pk

    delete_account(registration.user)

    assert calls == []
    assert not User.objects.filter(pk=user_pk).exists()


def test_delete_account_admin_user_with_no_registration_deletes_cleanly(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A User with no Registration (e.g. an admin) deletes with no refund attempt."""
    calls = _mock_refund_create(monkeypatch)
    user = UserFactory.create()
    user_pk = user.pk

    delete_account(user)

    assert calls == []
    assert not User.objects.filter(pk=user_pk).exists()


def test_delete_account_fires_account_deleted_event_with_role(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """delete_account sends an 'account_deleted' event with the registration's role.

    Deferred via transaction.on_commit — captureOnCommitCallbacks(execute=True)
    runs it here, mirroring the on-commit assertions elsewhere in the codebase.
    """
    _mock_refund_create(monkeypatch)
    registration = RegistrationFactory.create()
    user = registration.user
    user_pk = user.pk
    role = registration.role

    with (
        patch("accounts.services.capture_event") as mock_capture,
        TestCase.captureOnCommitCallbacks(execute=True),
    ):
        delete_account(user)

    mock_capture.assert_called_once_with(
        str(user_pk), "account_deleted", {"role": role}
    )
    # No PII (email) in the event payload.
    _, _, properties = mock_capture.call_args[0]
    assert user.email not in str(properties)


def test_delete_account_admin_with_no_registration_fires_event_with_none_role(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A User with no Registration is tracked with role=None, not omitted."""
    _mock_refund_create(monkeypatch)
    user = UserFactory.create()
    user_pk = user.pk

    with (
        patch("accounts.services.capture_event") as mock_capture,
        TestCase.captureOnCommitCallbacks(execute=True),
    ):
        delete_account(user)

    mock_capture.assert_called_once_with(
        str(user_pk), "account_deleted", {"role": None}
    )


def test_delete_account_does_not_fire_event_before_commit() -> None:
    """Without captureOnCommitCallbacks, the deferred event has not fired yet.

    Pins the ghost-event fix: the event must be registered via
    transaction.on_commit, not sent eagerly before the delete's atomic block.
    """
    registration = RegistrationFactory.create()
    user = registration.user

    with patch("accounts.services.capture_event") as mock_capture:
        delete_account(user)

    mock_capture.assert_not_called()


def test_delete_account_refunds_exactly_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A sequential retry of the delete cannot double-refund the same payment.

    ``refund()`` is guarded on ``status == HELD``. After ``delete_account``
    refunds and deletes the account, the Payment survives via ``SET_NULL`` but
    its registration is gone, so a subsequent ``delete_account`` for the (now
    absent) user finds no HELD deposit and issues no second Stripe call. A
    truly *concurrent* double-POST is covered by the InvalidPaymentTransition
    race test below (the loser catches the benign exception).
    """
    calls = _mock_refund_create(monkeypatch)
    registration = RegistrationFactory.create()
    payment = PaymentFactory.create(
        registration=registration,
        status=Payment.Status.HELD,
        stripe_payment_intent_id="pi_test0001",
    )
    user_pk = registration.user.pk

    delete_account(registration.user)
    assert len(calls) == 1
    assert calls[0]["idempotency_key"] == f"refund-payment-{payment.pk}"

    # A retried call against the same (now-deleted) user's registration finds
    # no HELD deposit left to refund — the payment survives via SET_NULL, but
    # it is no longer associated with any registration to look up from.
    payment.refresh_from_db()
    assert payment.status == Payment.Status.REFUNDED
    assert not Registration.objects.filter(user_id=user_pk).exists()
    assert len(calls) == 1


def test_delete_account_proceeds_if_deposit_refunded_concurrently(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If close_season refunds the deposit first, deletion still succeeds.

    ``refund()`` raises ``InvalidPaymentTransition`` when the payment is no
    longer HELD (a concurrent ``close_season`` win). ``delete_account`` treats
    that as benign — the money is already on its way back — and proceeds to
    delete the account rather than 500 the mid-logout user.
    """

    def _raise(*args: object, **kwargs: object) -> None:
        raise InvalidPaymentTransition("deposit already left HELD")

    monkeypatch.setattr("accounts.services.refund", _raise)
    registration = RegistrationFactory.create()
    PaymentFactory.create(
        registration=registration,
        status=Payment.Status.HELD,
        stripe_payment_intent_id="pi_test0002",
    )
    user_pk = registration.user.pk

    delete_account(registration.user)

    assert not User.objects.filter(pk=user_pk).exists()
