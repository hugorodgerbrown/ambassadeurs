# Tests for the account self-service views and the magic-link login flow.
#
# Login flow (VERB-46):
#   login_request GET/POST, login_sent GET, login_verify GET/POST, logout POST/GET.
# Account self-service:
#   account_detail, account_edit, account_delete, account_resend_confirmation,
#   account_match — see original test coverage (unchanged behaviour).

from datetime import UTC, datetime

import pytest
from django.contrib.auth import SESSION_KEY
from django.contrib.auth.models import User
from django.core import mail
from django.test import Client, TestCase, override_settings
from django.urls import reverse

from accounts.tokens import make_login_token
from matching.models import Match, Registration
from matching.services import accept_match
from tests.accounts.factories import UserFactory
from tests.matching.factories import MatchFactory, RegistrationFactory

pytestmark = pytest.mark.django_db


# ---------------------------------------------------------------------------
# login_request view (VERB-46)
# ---------------------------------------------------------------------------


def test_login_request_get_renders_form() -> None:
    """GET to accounts:login renders the login form."""
    response = Client().get(reverse("accounts:login"))
    assert response.status_code == 200
    assert "accounts/login.html" in [t.name for t in response.templates]


def test_login_request_post_known_email_redirects_to_sent() -> None:
    """POST with a known email redirects to login_sent and sends one email."""
    user = UserFactory.create(email="ada@example.com")
    mail.outbox.clear()
    response = Client().post(reverse("accounts:login"), {"email": "ada@example.com"})
    assert response.status_code == 302
    assert response.url == reverse("accounts:login_sent")
    assert len(mail.outbox) == 1
    assert mail.outbox[0].to == [user.email]


def test_login_request_post_unknown_email_redirects_to_sent_no_email() -> None:
    """POST with an unknown email still redirects to login_sent (no enumeration)."""
    mail.outbox.clear()
    response = Client().post(reverse("accounts:login"), {"email": "nobody@example.com"})
    assert response.status_code == 302
    assert response.url == reverse("accounts:login_sent")
    assert len(mail.outbox) == 0


def test_login_request_post_normalises_email_case() -> None:
    """POST with uppercase email is normalised before lookup (Invariant 5)."""
    UserFactory.create(email="ada@example.com")
    mail.outbox.clear()
    response = Client().post(reverse("accounts:login"), {"email": "ADA@EXAMPLE.COM"})
    assert response.status_code == 302
    assert len(mail.outbox) == 1
    assert mail.outbox[0].to == ["ada@example.com"]


@override_settings(DEBUG=True)
def test_login_request_post_stashes_url_in_session_under_debug() -> None:
    """Under DEBUG, a successful login request stashes the verify URL in session."""
    user = UserFactory.create(email="ada@example.com")
    client = Client()
    mail.outbox.clear()
    client.post(reverse("accounts:login"), {"email": user.email})
    assert "debug_login_url" in client.session


@override_settings(DEBUG=False)
def test_login_request_post_does_not_stash_url_outside_debug() -> None:
    """Outside DEBUG, the verify URL is not stashed in the session."""
    user = UserFactory.create(email="ada@example.com")
    client = Client()
    mail.outbox.clear()
    client.post(reverse("accounts:login"), {"email": user.email})
    assert "debug_login_url" not in client.session


# ---------------------------------------------------------------------------
# login_sent view (VERB-46)
# ---------------------------------------------------------------------------


def test_login_sent_get_renders_page() -> None:
    """GET to accounts:login_sent renders the sent confirmation page."""
    response = Client().get(reverse("accounts:login_sent"))
    assert response.status_code == 200
    assert "accounts/login_sent.html" in [t.name for t in response.templates]


@override_settings(DEBUG=True)
def test_login_sent_shows_debug_link_when_in_session() -> None:
    """Under DEBUG, login_sent shows the shortcut link when it is in the session."""
    client = Client()
    session = client.session
    session["debug_login_url"] = "http://testserver/account/login/TOKEN/"
    session.save()
    response = client.get(reverse("accounts:login_sent"))
    assert response.status_code == 200
    assert b"http://testserver/account/login/TOKEN/" in response.content


# ---------------------------------------------------------------------------
# login_verify view (VERB-46)
# ---------------------------------------------------------------------------


def test_login_verify_get_valid_token_renders_confirm_page() -> None:
    """GET with a valid token renders the confirm page without logging in."""
    user = UserFactory.create(email="ada@example.com")
    token = make_login_token(user.pk)
    client = Client()
    response = client.get(reverse("accounts:login_verify", args=[token]))
    assert response.status_code == 200
    assert "accounts/login_verify.html" in [t.name for t in response.templates]
    # Must NOT be logged in after a GET (prefetch safety).
    assert SESSION_KEY not in client.session


def test_login_verify_get_shows_target_email() -> None:
    """The verify page shows the target user's email address."""
    user = UserFactory.create(email="ada@example.com")
    token = make_login_token(user.pk)
    response = Client().get(reverse("accounts:login_verify", args=[token]))
    assert b"ada@example.com" in response.content


def test_login_verify_get_corrupted_token_returns_400() -> None:
    """GET with a corrupted (tampered) token returns 400 and renders login_invalid."""
    user = UserFactory.create()
    token = make_login_token(user.pk)
    response = Client().get(
        reverse("accounts:login_verify", args=[token + "corrupted"])
    )
    assert response.status_code == 400
    assert "accounts/login_invalid.html" in [t.name for t in response.templates]


def test_login_verify_get_expired_token_returns_400(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """GET with a properly-signed but expired token returns 400 without logging in.

    Uses monkeypatch to make ``read_login_token`` evaluate the token with
    ``max_age=-1``, simulating expiry without waiting or mocking the clock.
    """
    import accounts.views as views_module
    from accounts.tokens import read_login_token as real_read

    user = UserFactory.create()
    token = make_login_token(user.pk)

    def _always_expired(t: str, max_age: int = -1) -> int | None:
        return real_read(t, max_age=-1)

    monkeypatch.setattr(views_module, "read_login_token", _always_expired)

    client = Client()
    response = client.get(reverse("accounts:login_verify", args=[token]))
    assert response.status_code == 400
    assert "accounts/login_invalid.html" in [t.name for t in response.templates]
    assert SESSION_KEY not in client.session


def test_login_verify_get_garbage_token_returns_400() -> None:
    """GET with a garbage token string returns 400."""
    response = Client().get(
        reverse("accounts:login_verify", args=["not-a-valid-token"])
    )
    assert response.status_code == 400
    assert "accounts/login_invalid.html" in [t.name for t in response.templates]


def test_login_verify_post_valid_token_logs_in_and_redirects() -> None:
    """POST with a valid token logs the user in and redirects to accounts:detail."""
    user = UserFactory.create()
    token = make_login_token(user.pk)
    client = Client()
    response = client.post(reverse("accounts:login_verify", args=[token]))
    assert response.status_code == 302
    assert response.url == reverse("accounts:detail")
    # User must be logged in.
    assert SESSION_KEY in client.session
    assert int(client.session[SESSION_KEY]) == user.pk


def test_login_verify_post_inactive_user_returns_400() -> None:
    """POST with a valid token for an inactive user returns 400 without logging in.

    Covers the is_active=True guard added to close the deactivated-user blocker.
    """
    user = UserFactory.create(is_active=False)
    token = make_login_token(user.pk)
    client = Client()
    response = client.post(reverse("accounts:login_verify", args=[token]))
    assert response.status_code == 400
    assert "accounts/login_invalid.html" in [t.name for t in response.templates]
    assert SESSION_KEY not in client.session


def test_login_verify_get_deleted_user_returns_400() -> None:
    """GET with a valid token whose user has since been deleted returns 400."""
    user = UserFactory.create()
    user_pk = user.pk
    token = make_login_token(user_pk)
    user.delete()
    response = Client().get(reverse("accounts:login_verify", args=[token]))
    assert response.status_code == 400
    assert "accounts/login_invalid.html" in [t.name for t in response.templates]


def test_login_verify_post_invalid_token_returns_400() -> None:
    """POST with an invalid token returns 400 and does not log in."""
    client = Client()
    response = client.post(reverse("accounts:login_verify", args=["bad-token-xyz"]))
    assert response.status_code == 400
    assert SESSION_KEY not in client.session


# ---------------------------------------------------------------------------
# logout view (VERB-46)
# ---------------------------------------------------------------------------


def test_logout_post_logs_out_and_redirects_home() -> None:
    """POST to accounts:logout logs out and redirects to public:home."""
    user = UserFactory.create()
    client = Client()
    client.force_login(user)
    response = client.post(reverse("accounts:logout"))
    assert response.status_code == 302
    assert response.url == reverse("public:home")
    assert SESSION_KEY not in client.session


def test_logout_get_renders_confirmation_page() -> None:
    """GET to accounts:logout renders the styled logout confirmation page."""
    user = UserFactory.create()
    client = Client()
    client.force_login(user)
    response = client.get(reverse("accounts:logout"))
    assert response.status_code == 200
    assert "accounts/logout.html" in [t.name for t in response.templates]
    assert b"btn--primary" in response.content


# ---------------------------------------------------------------------------
# @login_required redirects to accounts:login (VERB-46)
# ---------------------------------------------------------------------------


def test_detail_requires_login() -> None:
    """Anonymous users are redirected to accounts:login from the account page."""
    response = Client().get(reverse("accounts:detail"))
    assert response.status_code == 302
    assert reverse("accounts:login") in response.url


def test_account_match_anonymous_redirects_to_login() -> None:
    """An anonymous request to accounts:match is redirected to accounts:login."""
    response = Client().get(reverse("accounts:match"))
    assert response.status_code == 302
    assert reverse("accounts:login") in response.url


def test_resend_anonymous_redirects_to_login() -> None:
    """An anonymous POST to resend-confirmation is redirected to accounts:login."""
    response = Client().post(reverse("accounts:resend_confirmation"))
    assert response.status_code == 302
    assert reverse("accounts:login") in response.url


# ---------------------------------------------------------------------------
# account_detail — general rendering
# ---------------------------------------------------------------------------


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
    assert b"delete your account" in response.content


def test_detail_without_registration_shows_register_link() -> None:
    """A user without a registration sees a prompt to register."""
    user = UserFactory.create()
    client = Client()
    client.force_login(user)
    response = client.get(reverse("accounts:detail"))
    assert response.status_code == 200
    assert b"Register now" in response.content


# ---------------------------------------------------------------------------
# account_detail — email_verified derived from Registration.status (VERB-46)
# ---------------------------------------------------------------------------


def test_detail_passes_email_verified_true_when_registration_is_verified() -> None:
    """account_detail passes email_verified=True for a non-UNVERIFIED registration."""
    registration = RegistrationFactory.create(status=Registration.Status.VERIFIED)
    client = Client()
    client.force_login(registration.user)
    response = client.get(reverse("accounts:detail"))
    assert response.status_code == 200
    assert response.context["email_verified"] is True


def test_detail_passes_email_verified_false_when_registration_is_unverified() -> None:
    """account_detail passes email_verified=False for an UNVERIFIED registration."""
    registration = RegistrationFactory.create(status=Registration.Status.UNVERIFIED)
    client = Client()
    client.force_login(registration.user)
    response = client.get(reverse("accounts:detail"))
    assert response.status_code == 200
    assert response.context["email_verified"] is False


def test_detail_passes_email_verified_false_when_no_registration() -> None:
    """account_detail passes email_verified=False for users without a registration."""
    user = UserFactory.create()
    client = Client()
    client.force_login(user)
    response = client.get(reverse("accounts:detail"))
    assert response.status_code == 200
    assert response.context["email_verified"] is False


def test_detail_shows_tick_when_email_verified() -> None:
    """The detail page renders a tick SVG when the registration is verified."""
    registration = RegistrationFactory.create(status=Registration.Status.VERIFIED)
    client = Client()
    client.force_login(registration.user)
    response = client.get(reverse("accounts:detail"))
    assert b"Email verified" in response.content


def test_detail_shows_unverified_label_when_email_not_verified() -> None:
    """The detail page shows 'Unverified' for an UNVERIFIED registration."""
    registration = RegistrationFactory.create(status=Registration.Status.UNVERIFIED)
    client = Client()
    client.force_login(registration.user)
    response = client.get(reverse("accounts:detail"))
    assert b"Unverified" in response.content


def test_detail_shows_resend_button_for_unverified_registration() -> None:
    """The detail page shows a resend button when the registration is UNVERIFIED."""
    registration = RegistrationFactory.create(status=Registration.Status.UNVERIFIED)
    client = Client()
    client.force_login(registration.user)
    response = client.get(reverse("accounts:detail"))
    assert b"Resend confirmation email" in response.content
    resend_url = reverse("accounts:resend_confirmation").encode()
    assert resend_url in response.content


def test_detail_hides_resend_button_when_email_verified() -> None:
    """The resend button is not shown once the registration is verified."""
    registration = RegistrationFactory.create(status=Registration.Status.VERIFIED)
    client = Client()
    client.force_login(registration.user)
    response = client.get(reverse("accounts:detail"))
    assert b"Resend confirmation email" not in response.content


# ---------------------------------------------------------------------------
# account_detail — no messages banner (VERB-46)
# ---------------------------------------------------------------------------


def test_detail_has_no_messages_banner() -> None:
    """The detail page no longer renders a Django messages banner block."""
    user = UserFactory.create()
    client = Client()
    client.force_login(user)
    response = client.get(reverse("accounts:detail"))
    # The messages block was removed; any stale message in the session must not
    # appear on the page.
    assert (
        b"mb-4 rounded-control border border-line bg-surface p-3 text-sm text-body"
        not in response.content
    )


# ---------------------------------------------------------------------------
# Status pill labels (VERB-25, updated for VERB-44)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("status", "expected_label"),
    [
        (
            Registration.Status.UNVERIFIED,
            b"Please confirm your email address to enter the pool",
        ),
        (Registration.Status.VERIFIED, b"in the queue"),
        (Registration.Status.WITHDRAWN, b"Your registration has been withdrawn"),
        (Registration.Status.SUSPENDED, b"Your registration has been suspended"),
    ],
)
def test_detail_status_sentence(status: str, expected_label: bytes) -> None:
    """Each Registration.Status value (no active match) renders the correct sentence."""
    registration = RegistrationFactory.create(status=status)
    client = Client()
    client.force_login(registration.user)
    response = client.get(reverse("accounts:detail"))
    assert response.status_code == 200
    assert expected_label in response.content


@pytest.mark.parametrize(
    ("status", "tone", "label"),
    [
        (Registration.Status.UNVERIFIED, b"tag-status--muted", b"Unverified"),
        (Registration.Status.VERIFIED, b"tag-status--muted", b"Queued"),
        (Registration.Status.WITHDRAWN, b"tag-status--muted", b"Withdrawn"),
        (Registration.Status.SUSPENDED, b"tag-status--muted", b"Suspended"),
    ],
)
def test_detail_status_pill(status: str, tone: bytes, label: bytes) -> None:
    """The Match status heading shows a pill for each pool-standing status."""
    registration = RegistrationFactory.create(status=status)
    client = Client()
    client.force_login(registration.user)
    response = client.get(reverse("accounts:detail"))
    assert response.status_code == 200
    assert tone in response.content
    assert label in response.content


def test_detail_status_pill_no_registration() -> None:
    """A user without a registration sees a neutral 'Queued' pill."""
    user = UserFactory.create()
    client = Client()
    client.force_login(user)
    response = client.get(reverse("accounts:detail"))
    assert response.status_code == 200
    assert b"tag-status--muted" in response.content
    assert b"Queued" in response.content


def test_detail_status_pill_proposed_match() -> None:
    """A registration with a PROPOSED active match shows the 'wait' tone pill."""
    reg = RegistrationFactory.create()
    MatchFactory.create(ambassador_registration=reg)
    client = Client()
    client.force_login(reg.user)
    response = client.get(reverse("accounts:detail"))
    assert response.status_code == 200
    assert b"tag-status--wait" in response.content
    assert b"Pending" in response.content


def test_detail_status_pill_accepted_match() -> None:
    """A registration with an ACCEPTED match shows the 'done' tone pill."""
    reg = RegistrationFactory.create()
    MatchFactory.create(accepted=True, ambassador_registration=reg)
    client = Client()
    client.force_login(reg.user)
    response = client.get(reverse("accounts:detail"))
    assert response.status_code == 200
    assert b"tag-status--done" in response.content
    assert b"Accepted" in response.content


# ---------------------------------------------------------------------------
# Active-match sub-states: partner name + partner response (VERB-37, VERB-44)
# ---------------------------------------------------------------------------

_FAR_FUTURE = datetime(2099, 12, 31, 23, 59, 59, tzinfo=UTC)
_ACCEPTED_AT = datetime(2026, 9, 2, 10, 0, 0, tzinfo=UTC)


def test_detail_proposed_match_partner_pending_names_partner() -> None:
    """A PROPOSED match (partner not responded) shows the partner's name."""
    reg = RegistrationFactory.create()
    ref_reg = RegistrationFactory.create(
        referee=True,
        user__first_name="Bernard",
        user__last_name="Borel",
    )
    MatchFactory.create(
        ambassador_registration=reg,
        referee_registration=ref_reg,
        status=Match.Status.PROPOSED,
        expires_at=_FAR_FUTURE,
    )
    client = Client()
    client.force_login(reg.user)
    response = client.get(reverse("accounts:detail"))
    assert response.status_code == 200
    assert b"You have been matched with <strong>Bernard</strong>" in response.content
    assert b"They have not yet responded" in response.content
    # PII invariant: surname, email and phone must not appear before mutual accept.
    assert b"Borel" not in response.content
    assert ref_reg.user.email.encode() not in response.content
    assert ref_reg.phone.encode() not in response.content


def test_detail_pending_match_partner_waiting_on_you() -> None:
    """PENDING match with partner accepted: the viewer is told the partner waits."""
    reg = RegistrationFactory.create()
    ref_reg = RegistrationFactory.create(
        referee=True,
        user__first_name="Bernard",
    )
    MatchFactory.create(
        ambassador_registration=reg,
        referee_registration=ref_reg,
        status=Match.Status.PENDING,
        expires_at=_FAR_FUTURE,
        referee_accepted_at=_ACCEPTED_AT,
    )
    client = Client()
    client.force_login(reg.user)
    response = client.get(reverse("accounts:detail"))
    assert response.status_code == 200
    assert b"You have been matched with <strong>Bernard</strong>" in response.content
    assert b"They are waiting for you to respond" in response.content


def test_detail_proposed_match_referee_view_names_ambassador_partner() -> None:
    """A PROPOSED match referee sees the ambassador partner's first name."""
    amb_reg = RegistrationFactory.create(
        user__first_name="Astrid",
        user__last_name="Aebi",
    )
    ref_reg = RegistrationFactory.create(referee=True)
    MatchFactory.create(
        ambassador_registration=amb_reg,
        referee_registration=ref_reg,
        status=Match.Status.PROPOSED,
        expires_at=_FAR_FUTURE,
    )
    client = Client()
    client.force_login(ref_reg.user)
    response = client.get(reverse("accounts:detail"))
    assert response.status_code == 200
    assert b"You have been matched with <strong>Astrid</strong>" in response.content
    assert b"They have not yet responded" in response.content
    # PII invariant: surname, email and phone must not appear before mutual accept.
    assert b"Aebi" not in response.content
    assert amb_reg.user.email.encode() not in response.content
    assert amb_reg.phone.encode() not in response.content


def test_detail_accepted_match_names_partner_and_points_to_match() -> None:
    """An ACCEPTED match names the partner and links to the match page."""
    ref_reg = RegistrationFactory.create(referee=True)
    amb_reg = RegistrationFactory.create(user__first_name="Astrid")
    MatchFactory.create(
        ambassador_registration=amb_reg,
        referee_registration=ref_reg,
        status=Match.Status.ACCEPTED,
        ambassador_accepted_at=_ACCEPTED_AT,
        referee_accepted_at=_ACCEPTED_AT,
        expires_at=_FAR_FUTURE,
    )
    client = Client()
    client.force_login(ref_reg.user)
    response = client.get(reverse("accounts:detail"))
    assert response.status_code == 200
    assert b"You have been matched with <strong>Astrid</strong>" in response.content
    assert b"view the match to see their contact details" in response.content


# ---------------------------------------------------------------------------
# account_resend_confirmation (VERB-25, updated for VERB-46: redirect-only)
# ---------------------------------------------------------------------------


def test_resend_get_redirects_to_detail_without_sending() -> None:
    """A GET to resend-confirmation redirects to account detail and sends no email."""
    registration = RegistrationFactory.create(status=Registration.Status.UNVERIFIED)
    client = Client()
    client.force_login(registration.user)
    mail.outbox.clear()
    response = client.get(reverse("accounts:resend_confirmation"))
    assert response.status_code == 302
    assert response.url == reverse("accounts:detail")
    assert len(mail.outbox) == 0


def test_resend_post_sends_email_for_unverified_registration() -> None:
    """A POST for an UNVERIFIED registration sends a confirmation email."""
    registration = RegistrationFactory.create(status=Registration.Status.UNVERIFIED)
    client = Client()
    client.force_login(registration.user)
    mail.outbox.clear()
    response = client.post(reverse("accounts:resend_confirmation"))
    assert response.status_code == 302
    assert response.url == reverse("accounts:detail")
    assert len(mail.outbox) == 1
    assert "register/confirm/" in mail.outbox[0].body


def test_resend_post_redirects_when_no_unverified_registration() -> None:
    """A POST with no UNVERIFIED registration sends no email and redirects."""
    registration = RegistrationFactory.create(status=Registration.Status.VERIFIED)
    client = Client()
    client.force_login(registration.user)
    mail.outbox.clear()
    response = client.post(reverse("accounts:resend_confirmation"))
    assert response.status_code == 302
    assert response.url == reverse("accounts:detail")
    assert len(mail.outbox) == 0


@override_settings(DEBUG=True)
def test_resend_post_stashes_url_in_session_under_debug() -> None:
    """Under DEBUG, resend stashes the confirm URL in the session."""
    registration = RegistrationFactory.create(status=Registration.Status.UNVERIFIED)
    client = Client()
    client.force_login(registration.user)
    client.post(reverse("accounts:resend_confirmation"))
    assert "debug_verify_url" in client.session


@override_settings(DEBUG=False)
def test_resend_post_does_not_stash_url_outside_debug() -> None:
    """Outside DEBUG, the confirm URL is not stashed in the session."""
    registration = RegistrationFactory.create(status=Registration.Status.UNVERIFIED)
    client = Client()
    client.force_login(registration.user)
    client.post(reverse("accounts:resend_confirmation"))
    assert "debug_verify_url" not in client.session


# ---------------------------------------------------------------------------
# account_edit
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# account_delete
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# DEBUG panel in base.html (VERB-25)
# ---------------------------------------------------------------------------


@override_settings(DEBUG=True)
def test_debug_panel_shown_on_detail_when_url_in_session() -> None:
    """Under DEBUG, account_detail renders the dev panel when session has the URL."""
    registration = RegistrationFactory.create(status=Registration.Status.UNVERIFIED)
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
    """Under DEBUG the shortcut link appears in the panel when the session URL set."""
    registration = RegistrationFactory.create(status=Registration.Status.UNVERIFIED)
    client = Client()
    client.force_login(registration.user)
    session = client.session
    session["debug_verify_url"] = "http://testserver/register/confirm/FAKE/"
    session.save()
    response = client.get(reverse("accounts:detail"))
    assert b"Development panel" in response.content
    assert b"Development shortcut" in response.content


# ---------------------------------------------------------------------------
# account_match view (VERB-32)
# ---------------------------------------------------------------------------


def test_account_match_no_active_match_redirects_to_detail() -> None:
    """A logged-in user with no active match is redirected to accounts:detail."""
    user = UserFactory.create()
    RegistrationFactory.create(user=user, status=Registration.Status.VERIFIED)
    client = Client()
    client.force_login(user)
    response = client.get(reverse("accounts:match"))
    assert response.status_code == 302
    assert response.url == reverse("accounts:detail")


def test_account_match_no_registration_redirects_to_detail() -> None:
    """A logged-in user with no registration at all is redirected to accounts:detail."""
    user = UserFactory.create()
    client = Client()
    client.force_login(user)
    response = client.get(reverse("accounts:match"))
    assert response.status_code == 302
    assert response.url == reverse("accounts:detail")


def test_account_match_proposed_match_renders_match_page() -> None:
    """A user with a PROPOSED active match sees the match page (200)."""
    ambassador_reg = RegistrationFactory.create(
        role=Registration.Role.AMBASSADOR,
        prior_pass=Registration.PriorPass.SEASONAL,
    )
    referee_reg = RegistrationFactory.create(referee=True)
    MatchFactory.create(
        ambassador_registration=ambassador_reg,
        referee_registration=referee_reg,
    )
    client = Client()
    client.force_login(ambassador_reg.user)
    response = client.get(reverse("accounts:match"))
    assert response.status_code == 200
    assert "public/match.html" in [t.name for t in response.templates]


def test_account_match_renders_own_side_for_ambassador() -> None:
    """The account match view renders from the ambassador's side."""
    ambassador_reg = RegistrationFactory.create(
        role=Registration.Role.AMBASSADOR,
        prior_pass=Registration.PriorPass.SEASONAL,
    )
    referee_reg = RegistrationFactory.create(referee=True)
    MatchFactory.create(
        ambassador_registration=ambassador_reg,
        referee_registration=referee_reg,
    )
    client = Client()
    client.force_login(ambassador_reg.user)
    response = client.get(reverse("accounts:match"))
    assert response.status_code == 200
    # The context side must be the ambassador's side.
    assert response.context["side"] == Match.Side.AMBASSADOR


def test_account_match_renders_own_side_for_referee() -> None:
    """The account match view renders from the referee's side."""
    ambassador_reg = RegistrationFactory.create(
        role=Registration.Role.AMBASSADOR,
        prior_pass=Registration.PriorPass.SEASONAL,
    )
    referee_reg = RegistrationFactory.create(referee=True)
    MatchFactory.create(
        ambassador_registration=ambassador_reg,
        referee_registration=referee_reg,
    )
    client = Client()
    client.force_login(referee_reg.user)
    response = client.get(reverse("accounts:match"))
    assert response.status_code == 200
    assert response.context["side"] == Match.Side.REFEREE


def test_account_match_accepted_match_includes_counterpart_pii() -> None:
    """An ACCEPTED match via accounts:match reveals counterpart contact details."""
    ambassador_reg = RegistrationFactory.create(
        role=Registration.Role.AMBASSADOR,
        prior_pass=Registration.PriorPass.SEASONAL,
        phone="+41790009999",
    )
    referee_reg = RegistrationFactory.create(
        referee=True,
        phone="+41790008888",
    )
    MatchFactory.create(
        accepted=True,
        ambassador_registration=ambassador_reg,
        referee_registration=referee_reg,
    )
    # Log in as the ambassador; should see the referee's PII.
    client = Client()
    client.force_login(ambassador_reg.user)
    response = client.get(reverse("accounts:match"))
    assert response.status_code == 200
    content = response.content.decode()
    assert "+41790008888" in content
    assert referee_reg.user.email in content


def test_account_match_terminal_match_is_not_returned() -> None:
    """A DECLINED (terminal) match is not surfaced on accounts:match."""
    ambassador_reg = RegistrationFactory.create(
        role=Registration.Role.AMBASSADOR,
        prior_pass=Registration.PriorPass.SEASONAL,
    )
    referee_reg = RegistrationFactory.create(referee=True)
    MatchFactory.create(
        declined=True,
        ambassador_registration=ambassador_reg,
        referee_registration=referee_reg,
    )
    client = Client()
    client.force_login(ambassador_reg.user)
    response = client.get(reverse("accounts:match"))
    # No active match; redirect to detail.
    assert response.status_code == 302
    assert response.url == reverse("accounts:detail")


# ---------------------------------------------------------------------------
# account detail template — "View your match" link (VERB-32)
# ---------------------------------------------------------------------------


def test_detail_shows_view_match_link_for_proposed_match() -> None:
    """The account detail page shows the 'View your match' link for a PROPOSED match."""
    ambassador_reg = RegistrationFactory.create(
        role=Registration.Role.AMBASSADOR,
        prior_pass=Registration.PriorPass.SEASONAL,
    )
    MatchFactory.create(ambassador_registration=ambassador_reg)
    client = Client()
    client.force_login(ambassador_reg.user)
    response = client.get(reverse("accounts:detail"))
    assert response.status_code == 200
    # Assert on the URL, not translated copy (test env has no compiled catalogues).
    assert reverse("accounts:match").encode() in response.content


def test_detail_shows_view_match_link_for_accepted_match() -> None:
    """The detail page shows the 'View your match' link for an ACCEPTED match."""
    ambassador_reg = RegistrationFactory.create(
        role=Registration.Role.AMBASSADOR,
        prior_pass=Registration.PriorPass.SEASONAL,
    )
    MatchFactory.create(accepted=True, ambassador_registration=ambassador_reg)
    client = Client()
    client.force_login(ambassador_reg.user)
    response = client.get(reverse("accounts:detail"))
    assert response.status_code == 200
    assert reverse("accounts:match").encode() in response.content


@pytest.mark.parametrize(
    "status",
    [
        Registration.Status.UNVERIFIED,
        Registration.Status.VERIFIED,
        Registration.Status.WITHDRAWN,
        Registration.Status.SUSPENDED,
    ],
)
def test_detail_hides_view_match_link_for_no_active_match(status: str) -> None:
    """The 'View your match' link is absent when there is no active match."""
    registration = RegistrationFactory.create(status=status)
    client = Client()
    client.force_login(registration.user)
    response = client.get(reverse("accounts:detail"))
    assert response.status_code == 200
    assert reverse("accounts:match").encode() not in response.content


# ---------------------------------------------------------------------------
# account_match — action URLs minted per load (VERB-32 blocker fix)
# ---------------------------------------------------------------------------


def test_account_match_proposed_includes_accept_and_decline_urls() -> None:
    """accounts:match for a PROPOSED match includes accept_url and decline_url."""
    ambassador_reg = RegistrationFactory.create(
        role=Registration.Role.AMBASSADOR,
        prior_pass=Registration.PriorPass.SEASONAL,
    )
    referee_reg = RegistrationFactory.create(referee=True)
    MatchFactory.create(
        ambassador_registration=ambassador_reg,
        referee_registration=referee_reg,
    )
    client = Client()
    client.force_login(ambassador_reg.user)
    response = client.get(reverse("accounts:match"))
    assert response.status_code == 200
    # Both action URLs must be present and non-empty in the context.
    assert response.context["accept_url"]
    assert response.context["decline_url"]


def test_account_match_accepted_includes_report_no_show_url() -> None:
    """accounts:match for an ACCEPTED match includes report_no_show_url."""
    ambassador_reg = RegistrationFactory.create(
        role=Registration.Role.AMBASSADOR,
        prior_pass=Registration.PriorPass.SEASONAL,
    )
    referee_reg = RegistrationFactory.create(referee=True)
    MatchFactory.create(
        accepted=True,
        ambassador_registration=ambassador_reg,
        referee_registration=referee_reg,
    )
    client = Client()
    client.force_login(ambassador_reg.user)
    response = client.get(reverse("accounts:match"))
    assert response.status_code == 200
    assert response.context["report_no_show_url"]


def test_account_match_accept_via_minted_token_transitions_match() -> None:
    """Posting to the accept_url from accounts:match context transitions the match.

    Derives the accept URL from the context (as the on-page form would use it)
    and POSTs via HTMX to confirm the token minted in account_match is valid
    and the accept endpoint honours it.
    """
    ambassador_reg = RegistrationFactory.create(
        role=Registration.Role.AMBASSADOR,
        prior_pass=Registration.PriorPass.SEASONAL,
    )
    referee_reg = RegistrationFactory.create(referee=True)
    MatchFactory.create(
        ambassador_registration=ambassador_reg,
        referee_registration=referee_reg,
    )
    client = Client()
    client.force_login(ambassador_reg.user)

    # Load accounts:match to retrieve the minted accept_url.
    page_response = client.get(reverse("accounts:match"))
    assert page_response.status_code == 200
    accept_url = page_response.context["accept_url"]
    assert accept_url  # non-empty

    # POST to the accept URL with the HTMX header — should transition to PENDING.
    with TestCase.captureOnCommitCallbacks(execute=True):
        htmx_response = client.post(accept_url, headers={"hx-request": "true"})

    assert htmx_response.status_code == 200
    match_row = ambassador_reg.matches_as_ambassador.first()
    assert match_row is not None
    assert match_row.ambassador_accepted_at is not None
    assert match_row.status == Match.Status.PENDING  # one side accepted → PENDING


def test_account_match_second_accept_via_minted_token_confirms_match() -> None:
    """Both sides accepting via minted tokens transitions the match to ACCEPTED."""
    ambassador_reg = RegistrationFactory.create(
        role=Registration.Role.AMBASSADOR,
        prior_pass=Registration.PriorPass.SEASONAL,
    )
    referee_reg = RegistrationFactory.create(referee=True)
    match = MatchFactory.create(
        ambassador_registration=ambassador_reg,
        referee_registration=referee_reg,
    )

    # Ambassador accepts first via the service directly (off-HTMX).
    with TestCase.captureOnCommitCallbacks(execute=False):
        accept_match(match, ambassador_reg)

    match.refresh_from_db()
    assert match.status == Match.Status.PENDING  # one side accepted → PENDING

    # Referee accepts via the minted token from accounts:match.
    ref_client = Client()
    ref_client.force_login(referee_reg.user)
    page_response = ref_client.get(reverse("accounts:match"))
    assert page_response.status_code == 200
    accept_url = page_response.context["accept_url"]

    with TestCase.captureOnCommitCallbacks(execute=True):
        htmx_response = ref_client.post(accept_url, headers={"hx-request": "true"})

    assert htmx_response.status_code == 200
    match.refresh_from_db()
    assert match.status == Match.Status.ACCEPTED


# ---------------------------------------------------------------------------
# Pool-availability invariant (VERB-44): VERIFIED reg with active match excluded
# ---------------------------------------------------------------------------


def test_detail_match_state_none_for_verified_reg_without_match() -> None:
    """account_detail passes match_state='none' for a VERIFIED reg with no match."""
    reg = RegistrationFactory.create(status=Registration.Status.VERIFIED)
    client = Client()
    client.force_login(reg.user)
    response = client.get(reverse("accounts:detail"))
    assert response.status_code == 200
    assert response.context["match_state"] == "none"


def test_detail_match_state_proposed_for_proposed_match() -> None:
    """account_detail passes match_state='proposed' for a PROPOSED active match."""
    reg = RegistrationFactory.create()
    MatchFactory.create(ambassador_registration=reg, status=Match.Status.PROPOSED)
    client = Client()
    client.force_login(reg.user)
    response = client.get(reverse("accounts:detail"))
    assert response.status_code == 200
    assert response.context["match_state"] == "proposed"


def test_detail_match_state_pending_for_pending_match() -> None:
    """account_detail passes match_state='pending' when the active match is PENDING."""
    reg = RegistrationFactory.create()
    MatchFactory.create(ambassador_registration=reg, status=Match.Status.PENDING)
    client = Client()
    client.force_login(reg.user)
    response = client.get(reverse("accounts:detail"))
    assert response.status_code == 200
    assert response.context["match_state"] == "pending"


def test_detail_match_state_accepted_for_accepted_match() -> None:
    """account_detail passes match_state='accepted' for an ACCEPTED active match."""
    reg = RegistrationFactory.create()
    MatchFactory.create(accepted=True, ambassador_registration=reg)
    client = Client()
    client.force_login(reg.user)
    response = client.get(reverse("accounts:detail"))
    assert response.status_code == 200
    assert response.context["match_state"] == "accepted"


def test_detail_pii_hidden_for_proposed_match() -> None:
    """PII (surname, email, phone) must not appear on the detail page pre-accept."""
    reg = RegistrationFactory.create()
    ref_reg = RegistrationFactory.create(
        referee=True,
        user__first_name="Bernard",
        user__last_name="SecretSurname",
        phone="+41799999999",
    )
    MatchFactory.create(
        ambassador_registration=reg,
        referee_registration=ref_reg,
        status=Match.Status.PROPOSED,
        expires_at=_FAR_FUTURE,
    )
    client = Client()
    client.force_login(reg.user)
    response = client.get(reverse("accounts:detail"))
    assert response.status_code == 200
    assert b"SecretSurname" not in response.content
    assert ref_reg.user.email.encode() not in response.content
    assert ref_reg.phone.encode() not in response.content
