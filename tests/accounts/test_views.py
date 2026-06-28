# Tests for the account self-service views.

from datetime import UTC, datetime

import pytest
from allauth.account.models import EmailAddress
from django.contrib.auth import SESSION_KEY
from django.contrib.auth.models import User
from django.test import Client, TestCase, override_settings
from django.urls import reverse

from matching.models import Match, Registration
from matching.services import accept_match
from tests.accounts.factories import UserFactory
from tests.matching.factories import MatchFactory, RegistrationFactory

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
    assert b"delete your account" in response.content


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
        (
            Registration.Status.PENDING,
            b"Please confirm your email address to enter the pool",
        ),
        (Registration.Status.WAITING, b"You are in the queue"),
        # MATCHED / CONFIRMED with no active match: partner name falls back to the
        # generic "your partner", so assert on the stable lead-in copy.
        (Registration.Status.MATCHED, b"You have been matched with"),
        (
            Registration.Status.CONFIRMED,
            b"view the match to see their contact details",
        ),
        (Registration.Status.WITHDRAWN, b"Your registration has been withdrawn"),
        (Registration.Status.SUSPENDED, b"Your registration has been suspended"),
    ],
)
def test_detail_status_sentence(status: str, expected_label: bytes) -> None:
    """Each Registration.Status value renders with the correct explanatory sentence."""
    registration = RegistrationFactory.create(status=status)
    client = Client()
    client.force_login(registration.user)
    response = client.get(reverse("accounts:detail"))
    assert response.status_code == 200
    assert expected_label in response.content


@pytest.mark.parametrize(
    ("status", "tone", "label"),
    [
        (Registration.Status.PENDING, b"tag-status--muted", b"Email unconfirmed"),
        (Registration.Status.WAITING, b"tag-status--muted", b"In the queue"),
        (Registration.Status.MATCHED, b"tag-status--wait", b"Match pending"),
        (Registration.Status.CONFIRMED, b"tag-status--done", b"Match confirmed"),
        (Registration.Status.WITHDRAWN, b"tag-status--muted", b"Withdrawn"),
        (Registration.Status.SUSPENDED, b"tag-status--muted", b"Suspended"),
    ],
)
def test_detail_status_pill(status: str, tone: bytes, label: bytes) -> None:
    """The Match status heading shows a tone-coded pill for each status."""
    registration = RegistrationFactory.create(status=status)
    client = Client()
    client.force_login(registration.user)
    response = client.get(reverse("accounts:detail"))
    assert response.status_code == 200
    assert tone in response.content
    assert label in response.content


def test_detail_status_pill_no_registration() -> None:
    """A user without a registration sees a neutral 'No match' pill."""
    user = UserFactory.create()
    client = Client()
    client.force_login(user)
    response = client.get(reverse("accounts:detail"))
    assert response.status_code == 200
    assert b"tag-status--muted" in response.content
    assert b"No match" in response.content


# ---------------------------------------------------------------------------
# MATCHED sub-states: partner name + partner response (VERB-37, amended)
# ---------------------------------------------------------------------------

_FAR_FUTURE = datetime(2099, 12, 31, 23, 59, 59, tzinfo=UTC)
_ACCEPTED_AT = datetime(2026, 9, 2, 10, 0, 0, tzinfo=UTC)


def test_detail_matched_partner_pending_names_partner() -> None:
    """A MATCHED ambassador whose partner has not responded sees the partner's name."""
    reg = RegistrationFactory.create(status=Registration.Status.MATCHED)
    ref_reg = RegistrationFactory.create(
        referee=True,
        status=Registration.Status.MATCHED,
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
    assert b"You have been matched with Bernard" in response.content
    assert b"They have not yet responded" in response.content
    # PII invariant: surname, email and phone must not appear before mutual accept.
    assert b"Borel" not in response.content
    assert ref_reg.user.email.encode() not in response.content
    assert ref_reg.phone.encode() not in response.content


def test_detail_matched_partner_accepted_says_waiting_on_you() -> None:
    """When the partner has accepted, the viewer is told the partner waits on them."""
    reg = RegistrationFactory.create(status=Registration.Status.MATCHED)
    ref_reg = RegistrationFactory.create(
        referee=True,
        status=Registration.Status.MATCHED,
        user__first_name="Bernard",
    )
    MatchFactory.create(
        ambassador_registration=reg,
        referee_registration=ref_reg,
        status=Match.Status.PROPOSED,
        expires_at=_FAR_FUTURE,
        referee_accepted_at=_ACCEPTED_AT,
    )
    client = Client()
    client.force_login(reg.user)
    response = client.get(reverse("accounts:detail"))
    assert response.status_code == 200
    assert b"You have been matched with Bernard" in response.content
    assert b"They are waiting for you to respond" in response.content


def test_detail_matched_referee_view_names_ambassador_partner() -> None:
    """A MATCHED referee sees the ambassador partner's first name, partner pending."""
    amb_reg = RegistrationFactory.create(
        status=Registration.Status.MATCHED,
        user__first_name="Astrid",
        user__last_name="Aebi",
    )
    ref_reg = RegistrationFactory.create(
        referee=True,
        status=Registration.Status.MATCHED,
    )
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
    assert b"You have been matched with Astrid" in response.content
    assert b"They have not yet responded" in response.content
    # PII invariant: surname, email and phone must not appear before mutual accept.
    assert b"Aebi" not in response.content
    assert amb_reg.user.email.encode() not in response.content
    assert amb_reg.phone.encode() not in response.content


def test_detail_confirmed_names_partner_and_points_to_match() -> None:
    """A CONFIRMED registration names the partner and links to the match page."""
    ref_reg = RegistrationFactory.create(
        referee=True,
        status=Registration.Status.CONFIRMED,
    )
    amb_reg = RegistrationFactory.create(
        status=Registration.Status.CONFIRMED,
        user__first_name="Astrid",
    )
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
    assert b"You have been matched with Astrid" in response.content
    assert b"view the match to see their contact details" in response.content


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
    """Under DEBUG the shortcut link appears in the panel when the session URL set."""
    registration = RegistrationFactory.create(status=Registration.Status.PENDING)
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


def test_account_match_anonymous_redirects_to_login() -> None:
    """An anonymous request to accounts:match is redirected to login."""
    response = Client().get(reverse("accounts:match"))
    assert response.status_code == 302
    assert reverse("account_login") in response.url


def test_account_match_no_active_match_redirects_to_detail() -> None:
    """A logged-in user with no active match is redirected to accounts:detail."""
    user = UserFactory.create()
    RegistrationFactory.create(user=user, status=Registration.Status.WAITING)
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


def test_account_match_matched_registration_renders_match_page() -> None:
    """A user with a PROPOSED active match sees the match page (200)."""
    ambassador_reg = RegistrationFactory.create(
        role=Registration.Role.AMBASSADOR,
        prior_pass=Registration.PriorPass.SEASONAL,
        status=Registration.Status.MATCHED,
    )
    referee_reg = RegistrationFactory.create(
        referee=True,
        status=Registration.Status.MATCHED,
    )
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
        status=Registration.Status.MATCHED,
    )
    referee_reg = RegistrationFactory.create(
        referee=True,
        status=Registration.Status.MATCHED,
    )
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
        status=Registration.Status.MATCHED,
    )
    referee_reg = RegistrationFactory.create(
        referee=True,
        status=Registration.Status.MATCHED,
    )
    MatchFactory.create(
        ambassador_registration=ambassador_reg,
        referee_registration=referee_reg,
    )
    client = Client()
    client.force_login(referee_reg.user)
    response = client.get(reverse("accounts:match"))
    assert response.status_code == 200
    assert response.context["side"] == Match.Side.REFEREE


def test_account_match_confirmed_match_includes_counterpart_pii() -> None:
    """An ACCEPTED match via accounts:match reveals counterpart contact details."""
    ambassador_reg = RegistrationFactory.create(
        role=Registration.Role.AMBASSADOR,
        prior_pass=Registration.PriorPass.SEASONAL,
        phone="+41790009999",
        status=Registration.Status.CONFIRMED,
    )
    referee_reg = RegistrationFactory.create(
        referee=True,
        phone="+41790008888",
        status=Registration.Status.CONFIRMED,
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
        status=Registration.Status.WAITING,
    )
    referee_reg = RegistrationFactory.create(
        referee=True,
        status=Registration.Status.WAITING,
    )
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


def test_detail_shows_view_match_link_for_matched_registration() -> None:
    """The account detail page shows the 'View your match' link for MATCHED status."""
    ambassador_reg = RegistrationFactory.create(
        role=Registration.Role.AMBASSADOR,
        prior_pass=Registration.PriorPass.SEASONAL,
        status=Registration.Status.MATCHED,
    )
    MatchFactory.create(ambassador_registration=ambassador_reg)
    client = Client()
    client.force_login(ambassador_reg.user)
    response = client.get(reverse("accounts:detail"))
    assert response.status_code == 200
    # Assert on the URL, not translated copy (test env has no compiled catalogues).
    assert reverse("accounts:match").encode() in response.content


def test_detail_shows_view_match_link_for_confirmed_registration() -> None:
    """The account detail page shows the 'View your match' link for CONFIRMED status."""
    ambassador_reg = RegistrationFactory.create(
        role=Registration.Role.AMBASSADOR,
        prior_pass=Registration.PriorPass.SEASONAL,
        status=Registration.Status.CONFIRMED,
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
        Registration.Status.PENDING,
        Registration.Status.WAITING,
        Registration.Status.WITHDRAWN,
        Registration.Status.SUSPENDED,
    ],
)
def test_detail_hides_view_match_link_for_non_match_statuses(status: str) -> None:
    """The 'View your match' link is absent for non-MATCHED/CONFIRMED statuses."""
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
    """accounts:match for a MATCHED registration includes accept_url and decline_url."""
    ambassador_reg = RegistrationFactory.create(
        role=Registration.Role.AMBASSADOR,
        prior_pass=Registration.PriorPass.SEASONAL,
        status=Registration.Status.MATCHED,
    )
    referee_reg = RegistrationFactory.create(
        referee=True,
        status=Registration.Status.MATCHED,
    )
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


def test_account_match_confirmed_includes_report_no_show_url() -> None:
    """accounts:match for a CONFIRMED registration includes report_no_show_url."""
    ambassador_reg = RegistrationFactory.create(
        role=Registration.Role.AMBASSADOR,
        prior_pass=Registration.PriorPass.SEASONAL,
        status=Registration.Status.CONFIRMED,
    )
    referee_reg = RegistrationFactory.create(
        referee=True,
        status=Registration.Status.CONFIRMED,
    )
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
        status=Registration.Status.MATCHED,
    )
    referee_reg = RegistrationFactory.create(
        referee=True,
        status=Registration.Status.MATCHED,
    )
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

    # POST to the accept URL with the HTMX header — should transition to waiting.
    with TestCase.captureOnCommitCallbacks(execute=True):
        htmx_response = client.post(accept_url, headers={"hx-request": "true"})

    assert htmx_response.status_code == 200
    ambassador_reg.refresh_from_db()
    match_row = ambassador_reg.matches_as_ambassador.first()
    assert match_row is not None
    assert match_row.ambassador_accepted_at is not None


def test_account_match_second_accept_via_minted_token_confirms_match() -> None:
    """Both sides accepting via minted tokens transitions the match to ACCEPTED."""
    ambassador_reg = RegistrationFactory.create(
        role=Registration.Role.AMBASSADOR,
        prior_pass=Registration.PriorPass.SEASONAL,
        status=Registration.Status.MATCHED,
    )
    referee_reg = RegistrationFactory.create(
        referee=True,
        status=Registration.Status.MATCHED,
    )
    match = MatchFactory.create(
        ambassador_registration=ambassador_reg,
        referee_registration=referee_reg,
    )

    # Ambassador accepts first via the service directly (off-HTMX).
    with TestCase.captureOnCommitCallbacks(execute=False):
        accept_match(match, ambassador_reg)

    match.refresh_from_db()
    assert match.status == Match.Status.PROPOSED  # still proposed, one side accepted

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
