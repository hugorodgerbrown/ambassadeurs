# Tests for the public site views.
#
# Covers the combined single-step registration flow (VERB-24): anonymous POST
# creates a PENDING registration and emails a confirmation link; confirming
# the link transitions PENDING → WAITING, logs the user in, and redirects to
# register_done. Facebook references are absent from all rendered pages.
#
# Also covers the match accept/decline flow (VERB-19): signed token grants
# access to the match page; HTMX partials for accept/decline are guarded by
# require_htmx; contact PII is only revealed after mutual accept.

import re
from datetime import UTC, datetime
from unittest.mock import patch

import pytest
from django.conf import settings
from django.core import mail
from django.test import Client, TestCase, override_settings
from django.urls import reverse

from accounts.tokens import (
    make_match_access_token,
    make_registration_confirmation_token,
)
from matching.models import Match, Registration
from matching.services import accept_match, register_participant
from public.models import FormDownload
from tests.accounts.factories import UserFactory
from tests.matching.factories import MatchFactory, RegistrationFactory

pytestmark = pytest.mark.django_db


def test_home_renders() -> None:
    """The landing page returns 200 and uses the home template."""
    response = Client().get(reverse("public:home"))
    assert response.status_code == 200
    assert "public/home.html" in [t.name for t in response.templates]


def test_home_shows_both_role_ctas() -> None:
    """The homepage links to the register entry with each role hint."""
    response = Client().get(reverse("public:home"))
    content = response.content
    register = reverse("public:register").encode()
    assert register + b"?role=ambassador" in content
    assert register + b"?role=referee" in content
    assert b"I'm an Ambassador" in content
    assert b"I'm a Referee" in content


@override_settings(
    REGISTRATION_OPENS_AT="2020-01-01T00:00:00+00:00",
    REGISTRATION_CLOSES_AT="2020-12-31T23:59:59+00:00",
)
def test_home_shows_opens_soon_when_registration_closed() -> None:
    """With registration closed the homepage shows the opens-soon notice."""
    response = Client().get(reverse("public:home"))
    assert b"Registration opens soon" in response.content


def test_home_hides_opens_soon_when_registration_open() -> None:
    """With registration open (dev default) the opens-soon notice is hidden."""
    response = Client().get(reverse("public:home"))
    assert b"Registration opens soon" not in response.content


def test_home_contains_hero_image() -> None:
    """The homepage response includes the hero photograph path."""
    response = Client().get(reverse("public:home"))
    assert b"images/hero.jpg" in response.content


# ---------------------------------------------------------------------------
# Combined registration form (anonymous GET)
# ---------------------------------------------------------------------------


def test_register_get_renders_form_without_login() -> None:
    """GET /register/ returns 200 and the combined form without requiring login."""
    response = Client().get(reverse("public:register"))
    assert response.status_code == 200
    assert "public/register_details.html" in [t.name for t in response.templates]
    # Email field is rendered for anonymous users.
    assert b'name="email"' in response.content


def test_register_get_with_ambassador_role_hint() -> None:
    """GET /register/?role=ambassador themes the form for the ambassador."""
    response = Client().get(reverse("public:register") + "?role=ambassador")
    assert response.status_code == 200
    # The form heading is the generic "Your details"; the role is conveyed by
    # the eligibility eyebrow and the (absent) referee theme class.
    assert b"Eligibility \xc2\xb7 Ambassador" in response.content
    assert b"role-theme--referee" not in response.content


def test_register_get_with_referee_role_hint() -> None:
    """GET /register/?role=referee themes the form for the referee."""
    response = Client().get(reverse("public:register") + "?role=referee")
    assert response.status_code == 200
    assert b"Eligibility \xc2\xb7 Referee" in response.content
    assert b"role-theme--referee" in response.content


def test_register_get_defaults_to_ambassador_on_unknown_role() -> None:
    """GET /register/?role=banana silently falls back to the ambassador form."""
    response = Client().get(reverse("public:register") + "?role=banana")
    assert response.status_code == 200
    assert b"Eligibility \xc2\xb7 Ambassador" in response.content
    assert b"role-theme--referee" not in response.content


@override_settings(
    REGISTRATION_OPENS_AT="2020-01-01T00:00:00+00:00",
    REGISTRATION_CLOSES_AT="2020-12-31T23:59:59+00:00",
)
def test_register_closed_when_registration_closed() -> None:
    """With registration closed the register page shows the closed page."""
    response = Client().get(reverse("public:register"))
    assert "public/register_closed.html" in [t.name for t in response.templates]


# ---------------------------------------------------------------------------
# Combined registration form (anonymous POST — creates PENDING)
# ---------------------------------------------------------------------------


def _valid_ambassador_post() -> dict[str, object]:
    """Return a minimal valid ambassador POST payload."""
    return {
        "role": "ambassador",
        "first_name": "Ada",
        "last_name": "Lovelace",
        "email": "ada@example.com",
        "prior_pass": Registration.PriorPass.SEASONAL,
        "prior_pass_attestation": True,
        "terms_accepted": True,
    }


def _valid_referee_post() -> dict[str, object]:
    """Return a minimal valid referee POST payload."""
    return {
        "role": "referee",
        "first_name": "Grace",
        "last_name": "Hopper",
        "email": "grace@example.com",
        "prior_pass_attestation": True,
        "terms_accepted": True,
    }


def test_register_post_creates_pending_registration() -> None:
    """A valid anonymous POST creates an UNVERIFIED registration (not VERIFIED)."""
    response = Client().post(reverse("public:register"), _valid_referee_post())
    assert response.status_code == 302
    assert response.url == reverse("public:register_email_sent")
    assert Registration.objects.count() == 1
    reg = Registration.objects.get()
    assert reg.status == Registration.Status.UNVERIFIED
    assert reg.role == Registration.Role.REFEREE


def test_register_post_sends_confirmation_email() -> None:
    """A valid anonymous POST sends a confirmation email to the supplied address."""
    Client().post(reverse("public:register"), _valid_referee_post())
    assert len(mail.outbox) == 1
    assert mail.outbox[0].to == ["grace@example.com"]
    # The confirmation link must point to the confirm endpoint, not verify.
    assert "register/confirm/" in mail.outbox[0].body


def test_register_post_pending_not_matched() -> None:
    """An UNVERIFIED registration must never trigger a match (Invariant 2)."""
    # Pre-populate a verified ambassador — if matching ran, a Match would be created.
    RegistrationFactory.create(
        role=Registration.Role.AMBASSADOR,
        prior_pass=Registration.PriorPass.SEASONAL,
        status=Registration.Status.VERIFIED,
    )
    Client().post(reverse("public:register"), _valid_referee_post())
    from matching.models import Match

    assert Match.objects.count() == 0
    reg = Registration.objects.filter(role=Registration.Role.REFEREE).get()
    assert reg.status == Registration.Status.UNVERIFIED


def test_register_post_invalid_redisplays_form() -> None:
    """An invalid POST (missing attestation) re-renders the form and creates nothing."""
    payload = _valid_referee_post()
    del payload["prior_pass_attestation"]
    response = Client().post(reverse("public:register"), payload)
    assert response.status_code == 200
    assert not Registration.objects.exists()


def test_register_post_unknown_role_404() -> None:
    """A POST with an unknown role returns 404."""
    response = Client().post(reverse("public:register"), {"role": "banana"})
    assert response.status_code == 404


def test_register_post_resends_for_existing_pending() -> None:
    """A re-submit for an email with an UNVERIFIED registration resends the link.

    No second Registration row is created; exactly one confirmation email is
    sent (plus the initial one that was sent when the row was created by the
    factory — we reset outbox before the second POST).
    """
    # Simulate an existing UNVERIFIED row for this email.
    user = UserFactory.create(username="grace@example.com", email="grace@example.com")
    RegistrationFactory.create(
        user=user,
        role=Registration.Role.REFEREE,
        prior_pass=Registration.PriorPass.NONE,
        status=Registration.Status.UNVERIFIED,
    )
    mail.outbox.clear()

    Client().post(reverse("public:register"), _valid_referee_post())

    assert Registration.objects.filter(role=Registration.Role.REFEREE).count() == 1
    assert len(mail.outbox) == 1
    assert "register/confirm/" in mail.outbox[0].body


def test_register_post_duplicate_waiting_shows_validation_error() -> None:
    """Submitting for an email with an existing VERIFIED registration shows an error."""
    user = UserFactory.create(username="grace@example.com", email="grace@example.com")
    RegistrationFactory.create(
        user=user,
        role=Registration.Role.REFEREE,
        prior_pass=Registration.PriorPass.NONE,
        status=Registration.Status.VERIFIED,
    )

    response = Client().post(reverse("public:register"), _valid_referee_post())
    assert response.status_code == 200
    assert b"already registered" in response.content


def test_register_post_race_integrity_error_does_not_500() -> None:
    """An IntegrityError from a concurrent create must not propagate as a 500.

    Simulates the TOCTOU window: form validation passes (no existing
    registration found), but by the time the view calls register_participant
    a concurrent request has created the row and the OneToOne constraint fires.
    The view must catch that and redirect gracefully rather than 500-ing.
    """
    from unittest.mock import patch

    from django.db import IntegrityError

    # Patch register_participant to simulate the race condition: form validation
    # passes (no existing row), but the create inside the view raises
    # IntegrityError as if a concurrent request won the race.
    with patch(
        "public.views.register_participant",
        side_effect=IntegrityError("unique violation"),
    ):
        # Use an email that has no existing registration so form validation
        # passes; the IntegrityError is raised by the mock at create time.
        response = Client().post(reverse("public:register"), _valid_referee_post())

    # Must redirect to email-sent (no crash), not 500.
    assert response.status_code == 302
    assert response.url == reverse("public:register_email_sent")


@override_settings(DEBUG=True)
def test_register_post_stashes_confirm_url_in_debug() -> None:
    """In DEBUG the confirm URL is stashed in the session for the shortcut page."""
    client = Client()
    client.post(reverse("public:register"), _valid_referee_post())
    assert "debug_verify_url" in client.session


@override_settings(DEBUG=False)
def test_register_post_does_not_stash_url_outside_debug() -> None:
    """Outside DEBUG the confirm URL must not be stashed in the session."""
    client = Client()
    client.post(reverse("public:register"), _valid_referee_post())
    assert "debug_verify_url" not in client.session


def test_register_post_persists_nationality() -> None:
    """POSTing nationality persists it on the created Registration."""
    payload = _valid_referee_post()
    payload["nationality"] = "CH"
    Client().post(reverse("public:register"), payload)
    reg = Registration.objects.get(role=Registration.Role.REFEREE)
    assert str(reg.nationality) == "CH"


def test_register_post_nationality_optional() -> None:
    """Omitting nationality from the POST still creates a Registration."""
    Client().post(reverse("public:register"), _valid_referee_post())
    reg = Registration.objects.get(role=Registration.Role.REFEREE)
    assert str(reg.nationality) == ""


# ---------------------------------------------------------------------------
# register_email_sent
# ---------------------------------------------------------------------------


def test_register_email_sent_renders() -> None:
    """The check-your-inbox page renders."""
    response = Client().get(reverse("public:register_email_sent"))
    assert response.status_code == 200
    assert "public/register_email_sent.html" in [t.name for t in response.templates]


def test_register_email_sent_copy_mentions_joining_queue() -> None:
    """The email-sent page explicitly says the user joins the queue after confirming."""
    response = Client().get(reverse("public:register_email_sent"))
    assert b"join the matching queue" in response.content


@override_settings(DEBUG=True)
def test_register_email_sent_shows_confirm_link_in_debug() -> None:
    """In DEBUG the confirm link is shown on the sent page for click-through testing."""
    client = Client()
    response = client.post(
        reverse("public:register"), _valid_referee_post(), follow=True
    )
    assert response.status_code == 200
    assert b"Development shortcut" in response.content
    assert b"register/confirm/" in response.content
    # The one-shot value is popped, so a reload no longer shows the link.
    assert "debug_verify_url" not in client.session
    reload = client.get(reverse("public:register_email_sent"))
    assert b"Development shortcut" not in reload.content


@override_settings(DEBUG=False)
def test_register_email_sent_hides_confirm_link_outside_debug() -> None:
    """Outside DEBUG the confirm link is never stashed or shown."""
    client = Client()
    response = client.post(
        reverse("public:register"), _valid_referee_post(), follow=True
    )
    assert response.status_code == 200
    assert b"Development shortcut" not in response.content
    assert "debug_verify_url" not in client.session


# ---------------------------------------------------------------------------
# register_confirm
# ---------------------------------------------------------------------------


def test_register_confirm_valid_token_transitions_to_verified() -> None:
    """A valid confirm token transitions the registration UNVERIFIED → VERIFIED."""
    user = UserFactory.create(username="ada@example.com", email="ada@example.com")
    reg = RegistrationFactory.create(
        user=user,
        role=Registration.Role.AMBASSADOR,
        prior_pass=Registration.PriorPass.SEASONAL,
        status=Registration.Status.UNVERIFIED,
    )
    token = make_registration_confirmation_token(reg.pk)
    client = Client()
    response = client.get(reverse("public:register_confirm", args=[token]))

    assert response.status_code == 302
    assert response.url == reverse("public:register_done", args=["ambassador"])
    reg.refresh_from_db()
    assert reg.status == Registration.Status.VERIFIED


def test_register_confirm_logs_user_in() -> None:
    """Confirming a registration logs the user in."""
    user = UserFactory.create(username="ada@example.com", email="ada@example.com")
    reg = RegistrationFactory.create(
        user=user,
        role=Registration.Role.REFEREE,
        prior_pass=Registration.PriorPass.NONE,
        status=Registration.Status.UNVERIFIED,
    )
    token = make_registration_confirmation_token(reg.pk)
    client = Client()
    client.get(reverse("public:register_confirm", args=[token]))
    assert "_auth_user_id" in client.session
    assert int(client.session["_auth_user_id"]) == user.pk


def test_register_confirm_triggers_matching() -> None:
    """Confirming an UNVERIFIED registration proposes a match if a counterpart waits."""
    RegistrationFactory.create(
        role=Registration.Role.AMBASSADOR,
        prior_pass=Registration.PriorPass.SEASONAL,
        status=Registration.Status.VERIFIED,
    )
    user = UserFactory.create(username="grace@example.com", email="grace@example.com")
    reg = RegistrationFactory.create(
        user=user,
        role=Registration.Role.REFEREE,
        prior_pass=Registration.PriorPass.NONE,
        status=Registration.Status.UNVERIFIED,
    )
    token = make_registration_confirmation_token(reg.pk)
    with TestCase.captureOnCommitCallbacks(execute=True):
        Client().get(reverse("public:register_confirm", args=[token]))

    from matching.models import Match

    assert Match.objects.count() == 1
    # VERB-44: registration stays VERIFIED (not MATCHED) after a match is proposed.
    reg.refresh_from_db()
    assert reg.status == Registration.Status.VERIFIED


def test_register_confirm_invalid_token_returns_400() -> None:
    """A tampered or expired confirm token shows the invalid-link page with 400."""
    response = Client().get(reverse("public:register_confirm", args=["bad-token"]))
    assert response.status_code == 400
    assert "public/register_invalid.html" in [t.name for t in response.templates]


def test_register_confirm_tampered_token_returns_400() -> None:
    """A tampered token shows the invalid-link page with status 400."""
    user = UserFactory.create(username="ada@example.com", email="ada@example.com")
    reg = RegistrationFactory.create(
        user=user,
        status=Registration.Status.UNVERIFIED,
    )
    token = make_registration_confirmation_token(reg.pk)
    response = Client().get(reverse("public:register_confirm", args=[token + "x"]))
    assert response.status_code == 400
    assert "public/register_invalid.html" in [t.name for t in response.templates]


def test_register_confirm_expired_token_returns_400() -> None:
    """A well-formed but expired confirm token shows the invalid-link page with 400.

    The token is valid (correct signature) but is read with max_age=-1 to
    simulate expiry. The registration must remain UNVERIFIED (unchanged).
    """
    from unittest.mock import patch

    from accounts.tokens import read_registration_confirmation_token

    user = UserFactory.create(username="ada@example.com", email="ada@example.com")
    reg = RegistrationFactory.create(
        user=user,
        status=Registration.Status.UNVERIFIED,
    )
    token = make_registration_confirmation_token(reg.pk)

    # Wrap the real reader so it is called with max_age=-1 (always expired).
    def _expired_reader(t: str, max_age: int = -1) -> None:  # type: ignore[override]
        return read_registration_confirmation_token(t, max_age=-1)

    with patch("public.views.read_registration_confirmation_token", _expired_reader):
        response = Client().get(reverse("public:register_confirm", args=[token]))

    assert response.status_code == 400
    assert "public/register_invalid.html" in [t.name for t in response.templates]
    reg.refresh_from_db()
    assert reg.status == Registration.Status.UNVERIFIED


def test_register_confirm_already_confirmed_returns_400() -> None:
    """A confirm link for a non-UNVERIFIED registration returns 400 (used/replayed)."""
    user = UserFactory.create(username="ada@example.com", email="ada@example.com")
    reg = RegistrationFactory.create(
        user=user,
        status=Registration.Status.VERIFIED,  # already confirmed
    )
    token = make_registration_confirmation_token(reg.pk)
    response = Client().get(reverse("public:register_confirm", args=[token]))
    assert response.status_code == 400


def test_register_confirm_nonexistent_pk_returns_400() -> None:
    """A confirm token for a pk that does not exist returns 400."""
    token = make_registration_confirmation_token(99999)
    response = Client().get(reverse("public:register_confirm", args=[token]))
    assert response.status_code == 400


# ---------------------------------------------------------------------------
# register_details_form (HTMX role swap — no login required)
# ---------------------------------------------------------------------------


def test_details_form_fragment_requires_htmx() -> None:
    """The details form fragment rejects a plain (non-HTMX) request."""
    response = Client().get(
        reverse("public:register_details_form") + "?role=ambassador"
    )
    assert response.status_code == 400


def test_details_form_fragment_anonymous_allowed() -> None:
    """An anonymous HTMX request to the role-swap endpoint is allowed."""
    response = Client().get(
        reverse("public:register_details_form") + "?role=ambassador",
        headers={"hx-request": "true"},
    )
    assert response.status_code == 200


def test_details_form_fragment_ambassador_contains_qualifying_criteria() -> None:
    """The ambassador fragment lists the ambassador qualifying criteria."""
    response = Client().get(
        reverse("public:register_details_form") + "?role=ambassador",
        headers={"hx-request": "true"},
    )
    assert response.status_code == 200
    assert b"What you'll need to qualify" in response.content
    assert b"Eligibility \xc2\xb7 Ambassador" in response.content
    assert b"Mont 4 Card" in response.content


def test_details_form_fragment_referee_contains_qualifying_criteria() -> None:
    """The referee fragment lists the referee qualifying criteria."""
    response = Client().get(
        reverse("public:register_details_form") + "?role=referee",
        headers={"hx-request": "true"},
    )
    assert response.status_code == 200
    assert b"What you'll need to qualify" in response.content
    assert b"Eligibility \xc2\xb7 Referee" in response.content
    # Referee-specific criterion: the no-prior-pass (mid-season) exclusion.
    assert b"mid-season" in response.content


def test_details_form_fragment_returns_role_form() -> None:
    """An HTMX request returns the role-specific form fragment."""
    response = Client().get(
        reverse("public:register_details_form") + "?role=referee",
        headers={"hx-request": "true"},
    )
    assert response.status_code == 200
    assert b"Eligibility \xc2\xb7 Referee" in response.content


def test_details_form_fragment_pushes_canonical_role_url() -> None:
    """The role options push the canonical full-page URL so a refresh keeps the
    selected role (the swap targets the htmx-only fragment endpoint, which a
    refresh must never land on)."""
    register_url = reverse("public:register")
    response = Client().get(
        reverse("public:register_details_form") + "?role=ambassador",
        headers={"hx-request": "true"},
    )
    content = response.content
    assert f'hx-push-url="{register_url}?role=ambassador"'.encode() in content
    assert f'hx-push-url="{register_url}?role=referee"'.encode() in content


def test_details_form_fragment_unknown_role_404() -> None:
    """An unknown role on the fragment endpoint returns 404."""
    response = Client().get(
        reverse("public:register_details_form") + "?role=banana",
        headers={"hx-request": "true"},
    )
    assert response.status_code == 404


@override_settings(
    REGISTRATION_OPENS_AT="2020-01-01T00:00:00+00:00",
    REGISTRATION_CLOSES_AT="2020-12-31T23:59:59+00:00",
)
def test_details_form_fragment_closed_without_open_window_404() -> None:
    """The fragment endpoint 404s when registration is closed."""
    response = Client().get(
        reverse("public:register_details_form") + "?role=ambassador",
        headers={"hx-request": "true"},
    )
    assert response.status_code == 404


# ---------------------------------------------------------------------------
# Already-registered user sees banner and disabled surface (VERB-26)
# ---------------------------------------------------------------------------


def test_register_get_already_registered_shows_banner_and_disabled_inputs() -> None:
    """A logged-in user with a Registration sees the already-registered banner
    and disabled form inputs on GET /register/.

    Checks: banner copy, correct role label, link to accounts:detail, and the
    disabled attribute on an input field and the submit button.
    """
    user = UserFactory.create()
    RegistrationFactory.create(
        user=user,
        role=Registration.Role.AMBASSADOR,
        prior_pass=Registration.PriorPass.SEASONAL,
        status=Registration.Status.VERIFIED,
    )
    client = Client()
    client.force_login(user)
    response = client.get(reverse("public:register"))

    assert response.status_code == 200
    content = response.content.decode()
    # Lock-banner copy.
    assert "You're already registered" in content
    assert "Ambassador" in content
    # Locked submit replacement label.
    assert "Already registered" in content
    # Link to account detail (the exit).
    assert reverse("accounts:detail") in content
    assert "View my account" in content
    # At least one form input element must carry the disabled attribute.
    assert re.search(r"<input[^>]*\bdisabled\b", content)
    # The submit button element itself must be disabled.
    assert re.search(r'<button[^>]*type="submit"[^>]*\bdisabled\b', content)


def test_register_get_already_registered_locks_to_registered_role() -> None:
    """An already-registered user is shown the role they registered with even
    if they arrived via the other role's homepage link.

    A registered ambassador hitting /register/?role=referee must see the
    ambassador-themed, ambassador-labelled locked form — not the referee one.
    """
    user = UserFactory.create()
    RegistrationFactory.create(
        user=user,
        role=Registration.Role.AMBASSADOR,
        prior_pass=Registration.PriorPass.SEASONAL,
        status=Registration.Status.VERIFIED,
    )
    client = Client()
    client.force_login(user)
    response = client.get(reverse("public:register") + "?role=referee")

    assert response.status_code == 200
    content = response.content.decode()
    # The surface is themed for the registered (ambassador) role, not referee.
    assert "role-theme--referee" not in content
    # The hidden role input carries the registered role's slug.
    assert 'name="role" value="ambassador"' in content
    # The submit-area exit copy reflects the locked state.
    assert "Already registered" in content


def test_register_details_form_already_registered_shows_banner() -> None:
    """A logged-in user with a Registration sees the banner and disabled surface
    on the HTMX role-swap partial endpoint (register_details_form).
    """
    user = UserFactory.create()
    RegistrationFactory.create(
        user=user,
        referee=True,
        status=Registration.Status.VERIFIED,
    )
    client = Client()
    client.force_login(user)
    response = client.get(
        reverse("public:register_details_form") + "?role=referee",
        headers={"hx-request": "true"},
    )

    assert response.status_code == 200
    content = response.content.decode()
    # Lock-banner copy with correct role.
    assert "You're already registered" in content
    assert "Referee" in content
    assert reverse("accounts:detail") in content
    assert "View my account" in content
    # Disabled state present.
    assert "disabled" in content


def test_register_get_authenticated_without_registration_shows_normal_form() -> None:
    """A logged-in user who has no Registration sees the normal enabled form
    without any already-registered banner or disabled attributes.
    """
    user = UserFactory.create()
    client = Client()
    client.force_login(user)
    response = client.get(reverse("public:register"))

    assert response.status_code == 200
    content = response.content.decode()
    assert "You're already registered" not in content
    assert "View my account" not in content
    # No disabled inputs or buttons — the form surface is fully enabled.
    assert "disabled" not in content


def test_register_get_anonymous_shows_normal_form() -> None:
    """An anonymous visitor sees the normal enabled form without any banner."""
    response = Client().get(reverse("public:register"))

    assert response.status_code == 200
    content = response.content.decode()
    assert "You're already registered" not in content
    assert "View my account" not in content


# ---------------------------------------------------------------------------
# register_done
# ---------------------------------------------------------------------------


def test_register_done_renders() -> None:
    """The confirmation page renders for a valid role."""
    response = Client().get(reverse("public:register_done", args=["referee"]))
    assert response.status_code == 200
    assert "public/register_done.html" in [t.name for t in response.templates]


def test_register_done_unknown_role_404() -> None:
    """An unknown role slug on the confirmation page returns 404."""
    response = Client().get(reverse("public:register_done", args=["banana"]))
    assert response.status_code == 404


# ---------------------------------------------------------------------------
# Facebook removal — no Facebook references on any rendered page
# ---------------------------------------------------------------------------


def test_home_contains_no_facebook_login() -> None:
    """The homepage must not offer Facebook login (allauth removed in VERB-46).

    The hero copy legitimately names the Facebook *group* as the problem the
    product replaces, so this guards against social-login remnants (an OAuth
    endpoint or a "sign in with Facebook" affordance) rather than the word.
    """
    content = Client().get(reverse("public:home")).content.lower()
    assert b"facebook.com" not in content
    assert b"with facebook" not in content
    assert b"/accounts/facebook" not in content


def test_how_it_works_contains_no_facebook_reference() -> None:
    """The how-it-works page must not mention Facebook."""
    response = Client().get(reverse("public:how_it_works"))
    assert b"Facebook" not in response.content
    assert b"facebook" not in response.content


def test_register_form_contains_no_facebook_reference() -> None:
    """The combined registration form must not mention Facebook."""
    response = Client().get(reverse("public:register"))
    assert b"Facebook" not in response.content
    assert b"facebook" not in response.content


def test_account_detail_contains_no_facebook_reference() -> None:
    """The account detail page must not mention Facebook."""
    client = Client()
    client.force_login(UserFactory.create())
    response = client.get(reverse("accounts:detail"))
    assert b"Facebook" not in response.content
    assert b"facebook" not in response.content


# ---------------------------------------------------------------------------
# Legal pages
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("page", "marker"),
    [
        ("privacy", b"Privacy Policy"),
        ("cookies", b"Cookie Policy"),
        ("terms", b"Terms of Use"),
    ],
)
def test_legal_pages_render(page: str, marker: bytes) -> None:
    """Each legal page renders 200 with its heading and the footer links."""
    response = Client().get(reverse("public:legal", args=[page]))
    assert response.status_code == 200
    assert marker in response.content
    assert reverse("public:legal", args=["privacy"]).encode() in response.content


def test_legal_unknown_page_404() -> None:
    """An unknown legal slug returns 404."""
    response = Client().get(reverse("public:legal", args=["banana"]))
    assert response.status_code == 404


# ---------------------------------------------------------------------------
# How it works page
# ---------------------------------------------------------------------------


def test_how_it_works_renders_for_anonymous_user() -> None:
    """The how-it-works page returns 200 with the correct template (anonymous)."""
    response = Client().get(reverse("public:how_it_works"))
    assert response.status_code == 200
    assert "public/how_it_works.html" in [t.name for t in response.templates]


def test_faq_renders_for_anonymous_user() -> None:
    """The FAQ page returns 200 with the correct template (anonymous)."""
    response = Client().get(reverse("public:faq"))
    assert response.status_code == 200
    assert "public/faq.html" in [t.name for t in response.templates]


def test_faq_contains_eligibility_section() -> None:
    """The FAQ page renders eligibility questions for both roles."""
    response = Client().get(reverse("public:faq"))
    content = response.content
    assert b"Who qualifies as an Ambassador?" in content
    assert b"Who qualifies as a Referee?" in content
    assert b"Can a Mont 4 Card holder act as an Ambassador?" in content


def test_faq_contains_matching_section() -> None:
    """The FAQ page renders the matching-mechanics questions."""
    response = Client().get(reverse("public:faq"))
    content = response.content
    assert b"Do I choose my own partner?" in content
    assert b"How does the system decide who to pair me with?" in content


def test_faq_contains_contact_window_section() -> None:
    """The FAQ page renders the contact-window questions."""
    response = Client().get(reverse("public:faq"))
    content = response.content
    assert b"What is the contact window?" in content
    assert b"What happens if I miss the contact window?" in content
    assert b"What happens if my partner misses the contact window?" in content


def test_faq_contains_after_matching_section() -> None:
    """The FAQ page renders the post-match questions including the off-app note."""
    response = Client().get(reverse("public:faq"))
    content = response.content
    assert b"What happens once both of us have accepted?" in content
    assert b"Does this site handle the application form or pass purchase?" in content


def test_faq_links_to_how_it_works() -> None:
    """The FAQ page links to the how-it-works page."""
    response = Client().get(reverse("public:faq"))
    assert reverse("public:how_it_works").encode() in response.content


def test_home_menu_links_to_faq_and_how_it_works() -> None:
    """The homepage hamburger menu links to the FAQ and how-it-works pages."""
    content = Client().get(reverse("public:home")).content
    assert reverse("public:faq").encode() in content
    assert reverse("public:how_it_works").encode() in content


def test_how_it_works_contains_section_markers() -> None:
    """The how-it-works page renders its section headings."""
    response = Client().get(reverse("public:how_it_works"))
    content = response.content
    assert b"What is the 4 Vall\xc3\xa9es Ambassadors Programme?" in content
    assert b"Who is an Ambassador and who is a Referee?" in content
    assert b"How do I apply?" in content
    assert b"What is the approval process?" in content
    assert b"What are the requirements?" in content
    assert b"So what does this site do?" in content
    assert b"What does this site not do?" in content
    assert b"How does the match work?" in content
    assert b"What happens then?" in content


def test_how_it_works_contains_contact_email() -> None:
    """The how-it-works page shows the customer contact email address."""
    response = Client().get(reverse("public:how_it_works"))
    assert b"customer@televerbier.ch" in response.content


def test_how_it_works_contains_application_form_link() -> None:
    """The how-it-works page contains a link to the application-form download."""
    response = Client().get(reverse("public:how_it_works"))
    application_form_url = reverse("public:application_form").encode()
    assert application_form_url in response.content


def test_how_it_works_link_in_footer() -> None:
    """The footer on the how-it-works page includes the 'How it works' link."""
    response = Client().get(reverse("public:how_it_works"))
    how_it_works_url = reverse("public:how_it_works").encode()
    assert how_it_works_url in response.content


# ---------------------------------------------------------------------------
# Application-form download view
# ---------------------------------------------------------------------------


def test_download_application_form_creates_form_download_row() -> None:
    """Requesting the download view creates exactly one FormDownload row."""
    assert FormDownload.objects.count() == 0
    Client().get(reverse("public:application_form"))
    assert FormDownload.objects.count() == 1


@override_settings(APPLICATION_FORM_URL="https://example.test/form.pdf")
def test_download_application_form_redirects_to_configured_url() -> None:
    """The download view redirects (302) to the configured APPLICATION_FORM_URL."""
    response = Client().get(reverse("public:application_form"))
    assert response.status_code == 302
    assert response.url == settings.APPLICATION_FORM_URL


# ---------------------------------------------------------------------------
# Miscellaneous
# ---------------------------------------------------------------------------


def test_service_worker_served_as_javascript() -> None:
    """/sw.js returns 200 with a JavaScript content type (no 404)."""
    response = Client().get(reverse("public:service_worker"))
    assert response.status_code == 200
    assert "javascript" in response["Content-Type"]


def test_favicon_redirects_to_static_icon() -> None:
    """/favicon.ico redirects to the static SVG icon rather than 404ing."""
    response = Client().get(reverse("public:favicon"))
    assert response.status_code in (301, 302)
    assert response.url.endswith("favicon.svg")


# ---------------------------------------------------------------------------
# Match detail view (VERB-19)
# ---------------------------------------------------------------------------


def _make_match_url(match: Match, registration: Registration) -> str:
    """Return the /match/<token>/ URL for the given registration on the match."""
    token = make_match_access_token(match.pk, registration.pk)
    return reverse("public:match", args=[token])


def test_match_detail_valid_token_renders_match_page() -> None:
    """A valid token returns 200 and uses the public/match.html template."""
    match = MatchFactory.create()
    url = _make_match_url(match, match.ambassador_registration)
    response = Client().get(url)
    assert response.status_code == 200
    assert "public/match.html" in [t.name for t in response.templates]


def test_match_detail_bad_token_returns_400_and_invalid_template() -> None:
    """A tampered token returns 400 and the match_invalid template."""
    response = Client().get(reverse("public:match", args=["not-a-token"]))
    assert response.status_code == 400
    assert "public/match_invalid.html" in [t.name for t in response.templates]


def test_match_detail_expired_token_returns_400() -> None:
    """An expired token returns 400 with the match_invalid template.

    ``read_match_access_token`` is patched to return ``None`` (the same value it
    returns for an expired token) so the view's expiry-gate code path is exercised
    without needing to manipulate the system clock.
    """
    match = MatchFactory.create()
    token = make_match_access_token(match.pk, match.ambassador_registration_id)
    with patch("public.views.read_match_access_token", return_value=None):
        response = Client().get(reverse("public:match", args=[token]))
    assert response.status_code == 400
    assert "public/match_invalid.html" in [t.name for t in response.templates]


def test_match_detail_registration_not_on_match_returns_400() -> None:
    """A token with a registration_pk not on the match returns 400."""
    match = MatchFactory.create()
    # Create a registration that is not on this match.
    other_reg = RegistrationFactory.create()
    token = make_match_access_token(match.pk, other_reg.pk)
    response = Client().get(reverse("public:match", args=[token]))
    assert response.status_code == 400
    assert "public/match_invalid.html" in [t.name for t in response.templates]


def test_match_detail_actionable_state_shows_accept_decline_buttons() -> None:
    """A PROPOSED match within the window shows Accept and Decline buttons."""
    match = MatchFactory.create()  # default: PROPOSED, far-future expires_at
    url = _make_match_url(match, match.ambassador_registration)
    response = Client().get(url)
    content = response.content
    assert b"Accept" in content
    assert b"Decline" in content


def test_match_detail_no_counterpart_pii_before_accept() -> None:
    """A PROPOSED match must not reveal the counterpart's name, email, or phone."""
    ambassador_reg = RegistrationFactory.create(
        role=Registration.Role.AMBASSADOR,
        prior_pass=Registration.PriorPass.SEASONAL,
        phone="+41790009999",
        status=Registration.Status.VERIFIED,
    )
    referee_reg = RegistrationFactory.create(
        referee=True,
        phone="+41790008888",
        status=Registration.Status.VERIFIED,
    )
    match = MatchFactory.create(
        ambassador_registration=ambassador_reg,
        referee_registration=referee_reg,
    )
    url = _make_match_url(match, ambassador_reg)
    response = Client().get(url)
    content = response.content.decode()
    # Referee's phone must not appear in the ambassador's view (not yet accepted).
    assert "+41790008888" not in content
    assert referee_reg.user.email not in content


def test_match_detail_shows_counterpart_nationality_before_accept() -> None:
    """The roster shows the counterpart's nationality before mutual accept
    (non-PII), while email and phone stay hidden (VERB-75, Invariant 1).
    """
    ambassador_reg = RegistrationFactory.create(
        role=Registration.Role.AMBASSADOR,
        prior_pass=Registration.PriorPass.SEASONAL,
        status=Registration.Status.VERIFIED,
    )
    referee_reg = RegistrationFactory.create(
        referee=True,
        nationality="FR",
        phone="+41790008888",
        status=Registration.Status.VERIFIED,
    )
    match = MatchFactory.create(
        ambassador_registration=ambassador_reg,
        referee_registration=referee_reg,
    )
    url = _make_match_url(match, ambassador_reg)
    content = Client().get(url).content.decode()
    # Nationality (country name) is revealed before accept ...
    assert "France" in content
    # ... but contact PII is still withheld.
    assert "+41790008888" not in content
    assert referee_reg.user.email not in content


def test_match_detail_accepted_reveals_counterpart_pii() -> None:
    """After mutual accept the counterpart's contact details are shown."""
    ambassador_reg = RegistrationFactory.create(
        role=Registration.Role.AMBASSADOR,
        prior_pass=Registration.PriorPass.SEASONAL,
        phone="+41790009999",
        status=Registration.Status.VERIFIED,
    )
    referee_reg = RegistrationFactory.create(
        referee=True,
        phone="+41790008888",
        status=Registration.Status.VERIFIED,
    )
    match = MatchFactory.create(
        accepted=True,
        ambassador_registration=ambassador_reg,
        referee_registration=referee_reg,
    )
    url = _make_match_url(match, ambassador_reg)
    response = Client().get(url)
    content = response.content.decode()
    # Referee's PII should now be visible to the ambassador.
    assert referee_reg.phone in content
    assert referee_reg.user.email in content


def test_match_detail_accepted_shows_next_steps_block() -> None:
    """After mutual accept the next-steps application block is shown."""
    ambassador_reg = RegistrationFactory.create(
        role=Registration.Role.AMBASSADOR,
        prior_pass=Registration.PriorPass.SEASONAL,
        status=Registration.Status.VERIFIED,
    )
    referee_reg = RegistrationFactory.create(
        referee=True,
        status=Registration.Status.VERIFIED,
    )
    match = MatchFactory.create(
        accepted=True,
        ambassador_registration=ambassador_reg,
        referee_registration=referee_reg,
    )
    url = _make_match_url(match, ambassador_reg)
    response = Client().get(url)
    content = response.content
    # Stable markers: the next-steps card id, the form download URL, and the
    # application email address must all appear once the match is accepted.
    assert b'id="next-steps"' in content
    assert reverse("public:application_form").encode() in content
    assert b"customer@televerbier.ch" in content


def test_match_detail_next_steps_absent_before_mutual_accept() -> None:
    """A PROPOSED match must not show the next-steps application block."""
    ambassador_reg = RegistrationFactory.create(
        role=Registration.Role.AMBASSADOR,
        prior_pass=Registration.PriorPass.SEASONAL,
        status=Registration.Status.VERIFIED,
    )
    referee_reg = RegistrationFactory.create(
        referee=True,
        status=Registration.Status.VERIFIED,
    )
    match = MatchFactory.create(
        ambassador_registration=ambassador_reg,
        referee_registration=referee_reg,
    )
    url = _make_match_url(match, ambassador_reg)
    response = Client().get(url)
    content = response.content
    # Next-steps block must not appear before both parties accept.
    assert b'id="next-steps"' not in content


def test_match_detail_terminal_match_shows_no_action_buttons() -> None:
    """A terminal match (DECLINED) renders no Accept or Decline buttons."""
    match = MatchFactory.create(declined=True)
    # Use the ambassador side (declined_by=AMBASSADOR by default, but we just
    # need any party's view).
    url = _make_match_url(match, match.ambassador_registration)
    response = Client().get(url)
    content = response.content
    assert b'name="action"' not in content
    # The page should not contain the action form buttons.
    assert b"Accept" not in content or b"<button" not in content


def test_match_detail_htmx_accept_transitions_to_waiting_state() -> None:
    """HTMX accept by first party → waiting state, no counterpart PII in response."""
    ambassador_reg = RegistrationFactory.create(
        role=Registration.Role.AMBASSADOR,
        prior_pass=Registration.PriorPass.SEASONAL,
        phone="+41790009999",
        status=Registration.Status.VERIFIED,
    )
    referee_reg = RegistrationFactory.create(
        referee=True,
        phone="+41790008888",
        status=Registration.Status.VERIFIED,
    )
    match = MatchFactory.create(
        ambassador_registration=ambassador_reg,
        referee_registration=referee_reg,
    )
    token = make_match_access_token(match.pk, ambassador_reg.pk)
    url = reverse("public:match_accept", args=[token])
    with TestCase.captureOnCommitCallbacks(execute=True):
        response = Client().post(url, headers={"hx-request": "true"})

    assert response.status_code == 200
    content = response.content.decode()
    # Waiting state markers (the viewer accepted, awaiting the partner).
    assert "You've accepted" in content
    assert "1 of 2 accepted" in content
    # No counterpart contact PII (phone) in the waiting state.
    assert "+41790008888" not in content


def test_match_detail_htmx_second_accept_shows_accepted_state_and_pii() -> None:
    """HTMX second accept → ACCEPTED state; counterpart PII is revealed."""
    ambassador_reg = RegistrationFactory.create(
        role=Registration.Role.AMBASSADOR,
        prior_pass=Registration.PriorPass.SEASONAL,
        phone="+41790009999",
        status=Registration.Status.VERIFIED,
    )
    referee_reg = RegistrationFactory.create(
        referee=True,
        phone="+41790008888",
        status=Registration.Status.VERIFIED,
    )
    match = MatchFactory.create(
        ambassador_registration=ambassador_reg,
        referee_registration=referee_reg,
    )

    # First accept — ambassador side (outside HTMX; use the service directly).
    with TestCase.captureOnCommitCallbacks(execute=False):
        accept_match(match, ambassador_reg)

    match.refresh_from_db()
    assert match.status == Match.Status.PENDING  # VERB-44: first accept → PENDING

    # Second accept — referee via HTMX.
    token = make_match_access_token(match.pk, referee_reg.pk)
    url = reverse("public:match_accept", args=[token])
    with TestCase.captureOnCommitCallbacks(execute=True):
        response = Client().post(url, headers={"hx-request": "true"})

    assert response.status_code == 200
    match.refresh_from_db()
    assert match.status == Match.Status.ACCEPTED

    content = response.content.decode()
    # Counterpart PII (ambassador's phone) is present for the referee.
    assert "+41790009999" in content


def test_match_detail_htmx_decline_shows_declined_state() -> None:
    """HTMX decline → DECLINED state; decliner paused, other party re-queued.

    VERB-74: declining now pauses the decliner's registration (not deletes it).
    """
    ambassador_reg = RegistrationFactory.create(
        role=Registration.Role.AMBASSADOR,
        prior_pass=Registration.PriorPass.SEASONAL,
        status=Registration.Status.VERIFIED,
        priority=0,
    )
    referee_reg = RegistrationFactory.create(
        referee=True,
        status=Registration.Status.VERIFIED,
        priority=0,
    )
    match = MatchFactory.create(
        ambassador_registration=ambassador_reg,
        referee_registration=referee_reg,
    )
    token = make_match_access_token(match.pk, ambassador_reg.pk)
    url = reverse("public:match_decline", args=[token])
    response = Client().post(url, headers={"hx-request": "true"})

    assert response.status_code == 200
    match.refresh_from_db()
    assert match.status == Match.Status.DECLINED

    # Decliner (ambassador) is paused; other party (referee) is re-queued to front.
    ambassador_reg.refresh_from_db()
    assert ambassador_reg.status == Registration.Status.PAUSED
    referee_reg.refresh_from_db()
    assert referee_reg.status == Registration.Status.VERIFIED
    assert referee_reg.priority == 1

    content = response.content.decode()
    assert "paused" in content.lower()


def test_match_detail_htmx_decline_decliner_sees_paused_message() -> None:
    """HTMX decline partial shows the 'registration paused' message to the decliner.

    VERB-74: the partial's DECLINED branch splits on match.declined_by == side.
    The decliner (ambassador here) should see the paused message with a link
    to their account page (not a re-register link).
    """
    ambassador_reg = RegistrationFactory.create(
        role=Registration.Role.AMBASSADOR,
        prior_pass=Registration.PriorPass.SEASONAL,
        status=Registration.Status.VERIFIED,
    )
    referee_reg = RegistrationFactory.create(
        referee=True,
        status=Registration.Status.VERIFIED,
    )
    match = MatchFactory.create(
        ambassador_registration=ambassador_reg,
        referee_registration=referee_reg,
    )
    token = make_match_access_token(match.pk, ambassador_reg.pk)
    url = reverse("public:match_decline", args=[token])
    response = Client().post(url, headers={"hx-request": "true"})

    assert response.status_code == 200
    assert "public/partials/match_actions.html" in [t.name for t in response.templates]
    # The decliner's branch renders a link to the account page (structural URL check,
    # independent of translation).
    account_url = reverse("accounts:detail")
    assert account_url.encode() in response.content
    # The old re-register link must not appear.
    register_url = reverse("public:register")
    assert register_url.encode() not in response.content
    # The match must be DECLINED in the database.
    match.refresh_from_db()
    assert match.status == Match.Status.DECLINED


def test_match_detail_htmx_decline_counterpart_sees_requeued_message() -> None:
    """After a decline the non-declining party's HTMX view shows re-queued message.

    The counterpart (referee here) follows their own token after the ambassador
    declines. The DECLINED branch shows them the re-queued message (not the
    removed message meant for the decliner).
    """
    ambassador_reg = RegistrationFactory.create(
        role=Registration.Role.AMBASSADOR,
        prior_pass=Registration.PriorPass.SEASONAL,
        status=Registration.Status.VERIFIED,
    )
    referee_reg = RegistrationFactory.create(
        referee=True,
        status=Registration.Status.VERIFIED,
    )
    match = MatchFactory.create(
        ambassador_registration=ambassador_reg,
        referee_registration=referee_reg,
    )

    # The ambassador declines via the service directly (to avoid going through HTMX).
    from matching.services import decline_match as svc_decline_match

    svc_decline_match(match, ambassador_reg)

    # The referee now visits their own token.
    match.refresh_from_db()
    referee_token = make_match_access_token(match.pk, referee_reg.pk)
    response = Client().get(
        reverse("public:match", args=[referee_token]),
    )
    # GET on the full match page should render the DECLINED state.
    assert response.status_code == 200
    assert "public/match.html" in [t.name for t in response.templates]
    # The match must remain DECLINED in the database.
    match.refresh_from_db()
    assert match.status == Match.Status.DECLINED
    # The counterpart is the referee — they are NOT the decliner. The template
    # branch for match.declined_by != side omits the "Re-register" button; the
    # decliner's register link (btn--role on the /register/ href) must be absent.
    register_btn_fragment = (
        'href="' + reverse("public:register") + '" class="btn btn--role"'
    ).encode()
    assert register_btn_fragment not in response.content


def test_match_removed_page_has_account_link() -> None:
    """match_removed.html renders a link to the account page (VERB-74: paused)."""
    ambassador_reg = RegistrationFactory.create(
        role=Registration.Role.AMBASSADOR,
        prior_pass=Registration.PriorPass.SEASONAL,
        status=Registration.Status.VERIFIED,
    )
    referee_reg = RegistrationFactory.create(
        referee=True,
        status=Registration.Status.VERIFIED,
    )
    match = MatchFactory.create(
        ambassador_registration=ambassador_reg,
        referee_registration=referee_reg,
    )
    token = make_match_access_token(match.pk, ambassador_reg.pk)
    url = reverse("public:match", args=[token])
    response = Client().post(url, {"action": "decline"})

    assert response.status_code == 200
    assert "public/match_removed.html" in [t.name for t in response.templates]
    # The CTA button must link to the account page.
    account_url = reverse("accounts:detail")
    assert f'href="{account_url}" class="btn btn--role"'.encode() in response.content


def test_declining_pauses_registration_not_deletes_it() -> None:
    """Declining a match pauses the decliner's registration (VERB-74: no deletion).

    The registration is retained in PAUSED status; the user account is preserved.
    This replaces test_register_participant_prior_decline_count_set_on_reregistration
    which tested the now-retired account-deletion-on-decline path.
    """
    from django.contrib.auth.models import User as DjangoUser

    from matching.services import decline_match as svc_decline_match

    referee_reg = RegistrationFactory.create(referee=True)
    ambassador_reg = register_participant(
        role=Registration.Role.AMBASSADOR,
        first_name="Ada",
        last_name="Lovelace",
        email="ada_view_reregister@example.com",
        prior_pass=Registration.PriorPass.SEASONAL,
    )

    from matching.models import Match

    match = Match.objects.get(
        ambassador_registration=ambassador_reg,
        referee_registration=referee_reg,
    )
    svc_decline_match(match, ambassador_reg)

    # Registration must still exist — just PAUSED.
    ambassador_reg.refresh_from_db()
    assert ambassador_reg.status == Registration.Status.PAUSED

    # User account must also still exist.
    assert DjangoUser.objects.filter(pk=ambassador_reg.user_id).exists()


def test_match_accept_requires_htmx() -> None:
    """match_accept returns 400 for a plain (non-HTMX) POST (Invariant 7)."""
    match = MatchFactory.create()
    token = make_match_access_token(match.pk, match.ambassador_registration_id)
    url = reverse("public:match_accept", args=[token])
    response = Client().post(url)
    assert response.status_code == 400


def test_match_decline_requires_htmx() -> None:
    """match_decline returns 400 for a plain (non-HTMX) POST (Invariant 7)."""
    match = MatchFactory.create()
    token = make_match_access_token(match.pk, match.ambassador_registration_id)
    url = reverse("public:match_decline", args=[token])
    response = Client().post(url)
    assert response.status_code == 400


def test_match_accept_htmx_get_is_rejected() -> None:
    """An HTMX GET to match_accept is rejected with 405 Method Not Allowed.

    @require_POST must guard the view even when the HX-Request header is present.
    """
    match = MatchFactory.create()
    token = make_match_access_token(match.pk, match.ambassador_registration_id)
    url = reverse("public:match_accept", args=[token])
    response = Client().get(url, headers={"hx-request": "true"})
    assert response.status_code == 405
    # Match must be unchanged.
    match.refresh_from_db()
    assert match.status == Match.Status.PROPOSED


def test_match_decline_htmx_get_is_rejected() -> None:
    """An HTMX GET to match_decline is rejected with 405 Method Not Allowed.

    @require_POST must guard the view even when the HX-Request header is present,
    since decline is destructive (deletes the decliner's User row).
    """
    match = MatchFactory.create()
    token = make_match_access_token(match.pk, match.ambassador_registration_id)
    url = reverse("public:match_decline", args=[token])
    response = Client().get(url, headers={"hx-request": "true"})
    assert response.status_code == 405
    # Match must be unchanged and decliner's registration must still exist.
    match.refresh_from_db()
    assert match.status == Match.Status.PROPOSED
    assert match.ambassador_registration is not None


def test_match_withdraw_requires_htmx() -> None:
    """match_withdraw returns 400 for a plain (non-HTMX) POST (Invariant 7)."""
    match = MatchFactory.create()
    token = make_match_access_token(match.pk, match.ambassador_registration_id)
    url = reverse("public:match_withdraw", args=[token])
    response = Client().post(url)
    assert response.status_code == 400


def test_match_withdraw_htmx_get_is_rejected() -> None:
    """An HTMX GET to match_withdraw is rejected with 405 Method Not Allowed.

    @require_POST must guard the view even when the HX-Request header is present,
    so a GET cannot retract an acceptance.
    """
    ambassador_reg = RegistrationFactory.create(
        role=Registration.Role.AMBASSADOR,
        prior_pass=Registration.PriorPass.SEASONAL,
        status=Registration.Status.VERIFIED,
    )
    referee_reg = RegistrationFactory.create(
        referee=True,
        status=Registration.Status.VERIFIED,
    )
    match = MatchFactory.create(
        ambassador_registration=ambassador_reg,
        referee_registration=referee_reg,
    )
    with TestCase.captureOnCommitCallbacks(execute=False):
        accept_match(match, ambassador_reg)

    token = make_match_access_token(match.pk, ambassador_reg.pk)
    url = reverse("public:match_withdraw", args=[token])
    response = Client().get(url, headers={"hx-request": "true"})
    assert response.status_code == 405
    # The acceptance must be unchanged.
    match.refresh_from_db()
    assert match.ambassador_accepted_at is not None


def test_match_withdraw_htmx_returns_to_proposed_view() -> None:
    """HTMX withdraw after a first accept → actionable view; timestamp cleared."""
    ambassador_reg = RegistrationFactory.create(
        role=Registration.Role.AMBASSADOR,
        prior_pass=Registration.PriorPass.SEASONAL,
        status=Registration.Status.VERIFIED,
    )
    referee_reg = RegistrationFactory.create(
        referee=True,
        status=Registration.Status.VERIFIED,
    )
    match = MatchFactory.create(
        ambassador_registration=ambassador_reg,
        referee_registration=referee_reg,
    )
    # First accept by the ambassador (outside HTMX; use the service directly).
    with TestCase.captureOnCommitCallbacks(execute=False):
        accept_match(match, ambassador_reg)
    match.refresh_from_db()
    assert match.ambassador_accepted_at is not None

    token = make_match_access_token(match.pk, ambassador_reg.pk)
    url = reverse("public:match_withdraw", args=[token])
    response = Client().post(url, headers={"hx-request": "true"})

    assert response.status_code == 200
    match.refresh_from_db()
    assert match.status == Match.Status.PROPOSED
    assert match.ambassador_accepted_at is None

    content = response.content.decode()
    # Back in the actionable view: the accept/decline form actions are present,
    # and the waiting/withdraw control is gone.
    assert reverse("public:match_accept", args=[token]) in content
    assert reverse("public:match_decline", args=[token]) in content
    assert reverse("public:match_withdraw", args=[token]) not in content


def test_match_detail_post_fallback_accept_redirects() -> None:
    """A no-JS POST with action=accept performs PRG redirect back to the match page."""
    ambassador_reg = RegistrationFactory.create(
        role=Registration.Role.AMBASSADOR,
        prior_pass=Registration.PriorPass.SEASONAL,
        status=Registration.Status.VERIFIED,
    )
    referee_reg = RegistrationFactory.create(
        referee=True,
        status=Registration.Status.VERIFIED,
    )
    match = MatchFactory.create(
        ambassador_registration=ambassador_reg,
        referee_registration=referee_reg,
    )
    token = make_match_access_token(match.pk, ambassador_reg.pk)
    url = reverse("public:match", args=[token])
    with TestCase.captureOnCommitCallbacks(execute=True):
        response = Client().post(url, {"action": "accept"})

    assert response.status_code == 302
    assert response.url == url


def test_match_detail_post_fallback_decline_renders_removed_page() -> None:
    """A no-JS POST with action=decline renders match_removed.html directly.

    After decline the decliner's registration is deleted. A PRG redirect would
    re-resolve the token, find the FK NULL, and return 400 on the invalid
    template. Instead the view renders match_removed.html in-place (no redirect).
    """
    ambassador_reg = RegistrationFactory.create(
        role=Registration.Role.AMBASSADOR,
        prior_pass=Registration.PriorPass.SEASONAL,
        status=Registration.Status.VERIFIED,
    )
    referee_reg = RegistrationFactory.create(
        referee=True,
        status=Registration.Status.VERIFIED,
    )
    match = MatchFactory.create(
        ambassador_registration=ambassador_reg,
        referee_registration=referee_reg,
    )
    token = make_match_access_token(match.pk, ambassador_reg.pk)
    url = reverse("public:match", args=[token])
    response = Client().post(url, {"action": "decline"})

    assert response.status_code == 200
    assert "public/match_removed.html" in [t.name for t in response.templates]


# ---------------------------------------------------------------------------
# match_report_no_show (VERB-21)
# ---------------------------------------------------------------------------


def _make_accepted_match() -> tuple[Match, Registration, Registration]:
    """Create a mutually accepted match (VERB-44: both registrations stay VERIFIED)."""
    ambassador_reg = RegistrationFactory.create(
        role=Registration.Role.AMBASSADOR,
        prior_pass=Registration.PriorPass.SEASONAL,
        phone="+41790009999",
    )
    referee_reg = RegistrationFactory.create(
        referee=True,
        phone="+41790008888",
    )
    match = MatchFactory.create(
        accepted=True,
        ambassador_registration=ambassador_reg,
        referee_registration=referee_reg,
    )
    return match, ambassador_reg, referee_reg


def test_match_report_no_show_htmx_post_transitions_to_cancelled() -> None:
    """A valid HTMX POST on an ACCEPTED match transitions it to CANCELLED."""
    match, ambassador_reg, _ = _make_accepted_match()
    token = make_match_access_token(match.pk, ambassador_reg.pk)
    url = reverse("public:match_report_no_show", args=[token])

    with TestCase.captureOnCommitCallbacks(execute=True):
        response = Client().post(url, headers={"hx-request": "true"})

    assert response.status_code == 200
    match.refresh_from_db()
    assert match.status == Match.Status.CANCELLED
    assert match.no_show_reported_by == Match.Side.AMBASSADOR


def test_match_report_no_show_returns_fragment() -> None:
    """The HTMX response renders the match_actions partial (not the full page)."""
    match, ambassador_reg, _ = _make_accepted_match()
    token = make_match_access_token(match.pk, ambassador_reg.pk)
    url = reverse("public:match_report_no_show", args=[token])

    with TestCase.captureOnCommitCallbacks(execute=True):
        response = Client().post(url, headers={"hx-request": "true"})

    assert response.status_code == 200
    assert "public/partials/match_actions.html" in [t.name for t in response.templates]


def test_match_report_no_show_requires_htmx() -> None:
    """match_report_no_show returns 400 for a plain (non-HTMX) POST (Invariant 7)."""
    match, ambassador_reg, _ = _make_accepted_match()
    token = make_match_access_token(match.pk, ambassador_reg.pk)
    url = reverse("public:match_report_no_show", args=[token])

    response = Client().post(url)

    assert response.status_code == 400
    # Match must be unchanged.
    match.refresh_from_db()
    assert match.status == Match.Status.ACCEPTED


def test_match_report_no_show_htmx_get_does_not_report() -> None:
    """An HTMX GET to match_report_no_show does not perform the report.

    The action is irreversible (suspends the accused) so a GET, even with the
    HX header, must be rejected — @require_POST returns 405 Method Not Allowed.
    """
    match, ambassador_reg, _ = _make_accepted_match()
    token = make_match_access_token(match.pk, ambassador_reg.pk)
    url = reverse("public:match_report_no_show", args=[token])

    response = Client().get(url, headers={"hx-request": "true"})

    assert response.status_code == 405
    # Match must be unchanged.
    match.refresh_from_db()
    assert match.status == Match.Status.ACCEPTED


def test_match_report_no_show_on_non_accepted_match_is_noop() -> None:
    """An HTMX POST on a PROPOSED match is a no-op (no state change)."""
    match = MatchFactory.create()  # PROPOSED
    token = make_match_access_token(match.pk, match.ambassador_registration_id)
    url = reverse("public:match_report_no_show", args=[token])

    response = Client().post(url, headers={"hx-request": "true"})

    assert response.status_code == 200
    match.refresh_from_db()
    assert match.status == Match.Status.PROPOSED


def test_match_report_no_show_second_report_is_noop() -> None:
    """A second HTMX POST on an already-CANCELLED match is a no-op."""
    match = MatchFactory.create(cancelled=True)
    # The factory sets no_show_reported_by=REFEREE; try to report as ambassador.
    token = make_match_access_token(match.pk, match.ambassador_registration_id)
    url = reverse("public:match_report_no_show", args=[token])

    response = Client().post(url, headers={"hx-request": "true"})

    assert response.status_code == 200
    match.refresh_from_db()
    # Status and reporter unchanged.
    assert match.status == Match.Status.CANCELLED
    assert match.no_show_reported_by == Match.Side.REFEREE


def test_match_report_no_show_abandoned_fragment_shows_reporter_copy() -> None:
    """After reporting, the fragment shows reassurance copy to the reporter."""
    match, ambassador_reg, _ = _make_accepted_match()
    token = make_match_access_token(match.pk, ambassador_reg.pk)
    url = reverse("public:match_report_no_show", args=[token])

    with TestCase.captureOnCommitCallbacks(execute=True):
        response = Client().post(url, headers={"hx-request": "true"})

    content = response.content.decode()
    # Reporter sees the reassurance copy, not the accused's "contact support" line.
    assert "no-show reported" in content.lower()
    assert "contact support" not in content.lower()


def test_match_report_no_show_abandoned_fragment_shows_accused_copy() -> None:
    """The accused sees the 'contact support' copy on the ABANDONED partial."""
    match, ambassador_reg, referee_reg = _make_accepted_match()
    # Report filed by ambassador; accused is referee.
    with TestCase.captureOnCommitCallbacks(execute=False):
        from matching.services import report_no_show

        report_no_show(match, ambassador_reg)

    # Now load the partial for the accused (referee).
    token = make_match_access_token(match.pk, referee_reg.pk)
    url = reverse("public:match_report_no_show", args=[token])
    response = Client().post(url, headers={"hx-request": "true"})

    content = response.content.decode()
    assert "contact support" in content.lower()


def test_match_detail_post_fallback_report_no_show_redirects() -> None:
    """A no-JS POST with action=report_no_show applies the report and PRG-redirects."""
    match, ambassador_reg, _ = _make_accepted_match()
    token = make_match_access_token(match.pk, ambassador_reg.pk)
    url = reverse("public:match", args=[token])

    with TestCase.captureOnCommitCallbacks(execute=True):
        response = Client().post(url, {"action": "report_no_show"})

    assert response.status_code == 302
    assert response.url == url
    match.refresh_from_db()
    assert match.status == Match.Status.CANCELLED


def test_match_detail_post_fallback_report_no_show_already_reported_noop() -> None:
    """A no-JS report_no_show POST on an already-reported match is a no-op."""
    match = MatchFactory.create(cancelled=True)
    token = make_match_access_token(match.pk, match.ambassador_registration_id)
    url = reverse("public:match", args=[token])

    # The factory creates no_show_reported_by=REFEREE; try to report as ambassador
    # on an already-CANCELLED match.
    response = Client().post(url, {"action": "report_no_show"})

    assert response.status_code == 302
    match.refresh_from_db()
    assert match.status == Match.Status.CANCELLED
    assert match.no_show_reported_by == Match.Side.REFEREE  # unchanged


# ---------------------------------------------------------------------------
# match_detail — auth branch (VERB-32)
# ---------------------------------------------------------------------------


def test_match_detail_anonymous_valid_token_renders_200() -> None:
    """An anonymous visitor with a valid token sees the match page."""
    match = MatchFactory.create()
    url = _make_match_url(match, match.ambassador_registration)
    response = Client().get(url)
    assert response.status_code == 200
    assert "public/match.html" in [t.name for t in response.templates]


def test_match_detail_authenticated_participant_renders_own_side() -> None:
    """An authenticated participant sees the page from their own side, regardless of
    which party's token is in the URL."""
    match = MatchFactory.create()
    # Build URL from the ambassador token.
    token = make_match_access_token(match.pk, match.ambassador_registration_id)
    url = reverse("public:match", args=[token])

    # Log in as the referee — their own side should be rendered.
    client = Client()
    client.force_login(match.referee_registration.user)
    response = client.get(url)

    assert response.status_code == 200
    assert response.context["side"] == Match.Side.REFEREE
    assert response.context["registration"].pk == match.referee_registration_id


def test_match_detail_authenticated_non_participant_returns_403() -> None:
    """An authenticated user who is not a party on the match receives 403."""
    match = MatchFactory.create()
    url = _make_match_url(match, match.ambassador_registration)

    # Third user — not on the match.
    other_user = RegistrationFactory.create().user
    client = Client()
    client.force_login(other_user)
    response = client.get(url)

    assert response.status_code == 403
    assert "public/match_forbidden.html" in [t.name for t in response.templates]


def test_match_detail_authenticated_user_without_registration_returns_403() -> None:
    """An authenticated user with no registration at all receives 403 on match pages."""
    match = MatchFactory.create()
    url = _make_match_url(match, match.ambassador_registration)

    no_reg_user = UserFactory.create()
    client = Client()
    client.force_login(no_reg_user)
    response = client.get(url)

    assert response.status_code == 403
    assert "public/match_forbidden.html" in [t.name for t in response.templates]


def test_match_detail_invalid_token_still_returns_400_for_authenticated_user() -> None:
    """An expired or invalid token returns 400 regardless of auth state."""
    other_user = RegistrationFactory.create().user
    client = Client()
    client.force_login(other_user)
    response = client.get(reverse("public:match", args=["bad-token"]))
    assert response.status_code == 400
    assert "public/match_invalid.html" in [t.name for t in response.templates]


# ---------------------------------------------------------------------------
# Both-sides status panel (VERB-32)
# ---------------------------------------------------------------------------


def test_both_sides_panel_shows_pending_for_proposed_match() -> None:
    """On a PROPOSED match with no responses, both sides show Pending."""
    match = MatchFactory.create()
    url = _make_match_url(match, match.ambassador_registration)
    response = Client().get(url)
    content = response.content.decode()
    # Proposed: the viewer's row reads "Your turn", the partner's reads "Pending".
    assert "Your turn" in content
    assert "Pending" in content


def test_both_sides_panel_shows_accepted_after_ambassador_accepts() -> None:
    """After the ambassador accepts (PENDING), the ambassador row shows Accepted."""
    ambassador_reg = RegistrationFactory.create(
        role=Registration.Role.AMBASSADOR,
        prior_pass=Registration.PriorPass.SEASONAL,
    )
    referee_reg = RegistrationFactory.create(referee=True)
    match = MatchFactory.create(
        ambassador_registration=ambassador_reg,
        referee_registration=referee_reg,
    )
    with TestCase.captureOnCommitCallbacks(execute=False):
        accept_match(match, ambassador_reg)

    match.refresh_from_db()
    # VERB-44: first accept transitions PROPOSED → PENDING.
    assert match.status == Match.Status.PENDING
    url = _make_match_url(match, ambassador_reg)
    response = Client().get(url)
    content = response.content.decode()
    # Ambassador row: Accepted; referee row: Pending.
    assert "Accepted" in content
    assert "Pending" in content


def test_both_sides_panel_shows_declined_for_declining_party() -> None:
    """When a party declines, their row shows Declined in the panel."""
    match = MatchFactory.create(declined=True)  # declined_by=AMBASSADOR
    url = _make_match_url(match, match.ambassador_registration)
    response = Client().get(url)
    content = response.content.decode()
    assert "Declined" in content


def test_both_sides_panel_marks_viewer_with_you() -> None:
    """The viewer's own roster row is labelled 'You'; the partner shows their name."""
    match = MatchFactory.create()
    url = _make_match_url(match, match.ambassador_registration)
    response = Client().get(url)
    content = response.content.decode()
    # The viewer's own row reads "You"; the partner's row shows their first name.
    assert "You" in content
    assert match.referee_registration.user.first_name in content


def test_both_sides_panel_reveals_only_first_name_in_proposed_match() -> None:
    """The roster reveals the counterpart's first name on a PROPOSED match, but
    never their email, phone, or full name (Invariant 1, re-scoped — ADR 0009)."""
    ambassador_reg = RegistrationFactory.create(
        role=Registration.Role.AMBASSADOR,
        prior_pass=Registration.PriorPass.SEASONAL,
        phone="+41790009999",
        status=Registration.Status.VERIFIED,
    )
    referee_reg = RegistrationFactory.create(
        referee=True,
        phone="+41790008888",
        status=Registration.Status.VERIFIED,
    )
    match = MatchFactory.create(
        ambassador_registration=ambassador_reg,
        referee_registration=referee_reg,
    )
    # View from the ambassador side.
    url = _make_match_url(match, ambassador_reg)
    response = Client().get(url)
    content = response.content.decode()
    # First name is revealed early (ADR 0009)...
    assert referee_reg.user.first_name in content
    # ...but email, phone, and full name stay hidden until mutual accept.
    assert referee_reg.phone not in content
    assert referee_reg.user.email not in content
    full_name = referee_reg.user.get_full_name()
    if full_name and referee_reg.user.last_name:
        assert full_name not in content


def test_match_detail_partner_accepted_shows_callout_and_actions() -> None:
    """When the partner has accepted but the viewer has not, the page shows the
    'already accepted' callout and still offers Accept/Decline."""
    ambassador_reg = RegistrationFactory.create(
        role=Registration.Role.AMBASSADOR,
        prior_pass=Registration.PriorPass.SEASONAL,
        status=Registration.Status.VERIFIED,
    )
    referee_reg = RegistrationFactory.create(
        referee=True,
        status=Registration.Status.VERIFIED,
    )
    # Referee (the partner) has accepted; ambassador (the viewer) has not.
    match = MatchFactory.create(
        ambassador_registration=ambassador_reg,
        referee_registration=referee_reg,
        referee_accepted_at=datetime(2026, 9, 2, 10, 0, 0, tzinfo=UTC),
    )
    url = _make_match_url(match, ambassador_reg)
    response = Client().get(url)
    content = response.content.decode()
    assert response.context["view"] == "partner_accepted"
    assert "already accepted" in content.lower()
    assert "Accept match" in content
    assert "Decline" in content


def test_match_detail_expired_match_shows_expired_outcome() -> None:
    """An EXPIRED match renders the expired outcome with no action buttons."""
    match = MatchFactory.create(
        status=Match.Status.EXPIRED,
        expires_at=datetime(2020, 1, 1, 0, 0, 0, tzinfo=UTC),
    )
    url = _make_match_url(match, match.ambassador_registration)
    response = Client().get(url)
    content = response.content.decode()
    assert response.context["view"] == "expired"
    assert "This match expired" in content
    assert b"<button" not in response.content


# ---------------------------------------------------------------------------
# Geolocation on registration POST (VERB-49)
# ---------------------------------------------------------------------------


def test_register_post_stores_geo_country_and_region() -> None:
    """An anonymous registration POST resolves geo and stores country + region."""
    url = reverse("public:register") + "?role=ambassador"
    with (
        patch("public.views.get_client_ip", return_value="203.0.113.45"),
        patch("public.views.geolocate", return_value=("Switzerland", "Valais")),
    ):
        response = Client().post(
            url,
            {
                "role": "ambassador",
                "first_name": "Ada",
                "last_name": "Lovelace",
                "email": "ada_geo_view@example.com",
                "prior_pass": "SEASONAL",
                "phone": "+41790001234",
                "preferred_language": "en",
                "preferred_location": "",
                "prior_pass_attestation": True,
                "terms_accepted": True,
            },
        )

    # POST should redirect to register_email_sent.
    assert response.status_code == 302

    from matching.models import Registration

    reg = Registration.objects.get(user__email="ada_geo_view@example.com")
    assert reg.registration_country == "Switzerland"
    assert reg.registration_region == "Valais"
    # The raw IP must never be persisted: assert the source IP string appears in
    # no stored field value on the registration (no-IP-storage invariant).
    field_values = [
        str(getattr(reg, field.attname)) for field in Registration._meta.fields
    ]
    assert all("203.0.113.45" not in value for value in field_values)


def test_register_post_geo_empty_when_private_ip() -> None:
    """A registration from a private IP stores empty strings for geo fields."""
    url = reverse("public:register") + "?role=ambassador"
    with (
        patch("public.views.get_client_ip", return_value="127.0.0.1"),
        patch("public.views.geolocate", return_value=("", "")),
    ):
        response = Client().post(
            url,
            {
                "role": "ambassador",
                "first_name": "Bob",
                "last_name": "Builder",
                "email": "bob_no_geo@example.com",
                "prior_pass": "SEASONAL",
                "phone": "+41790005678",
                "preferred_language": "en",
                "preferred_location": "",
                "prior_pass_attestation": True,
                "terms_accepted": True,
            },
        )

    assert response.status_code == 302

    from matching.models import Registration

    reg = Registration.objects.get(user__email="bob_no_geo@example.com")
    assert reg.registration_country == ""
    assert reg.registration_region == ""


def test_register_post_skips_geolocate_when_no_client_ip() -> None:
    """When no client IP is resolvable, geolocate is not called and geo is empty."""
    url = reverse("public:register") + "?role=ambassador"
    with (
        patch("public.views.get_client_ip", return_value=None),
        patch("public.views.geolocate") as mock_geolocate,
    ):
        response = Client().post(
            url,
            {
                "role": "ambassador",
                "first_name": "Carol",
                "last_name": "Danvers",
                "email": "carol_no_ip@example.com",
                "prior_pass": "SEASONAL",
                "phone": "+41790009012",
                "preferred_language": "en",
                "preferred_location": "",
                "prior_pass_attestation": True,
                "terms_accepted": True,
            },
        )

    assert response.status_code == 302
    mock_geolocate.assert_not_called()

    reg = Registration.objects.get(user__email="carol_no_ip@example.com")
    assert reg.registration_country == ""
    assert reg.registration_region == ""


# ---------------------------------------------------------------------------
# WCAG 2.1 AA — structural accessibility checks (VERB-69)
# ---------------------------------------------------------------------------


def test_base_template_has_skip_link() -> None:
    """Every page built on base.html includes a skip-to-main-content link."""
    response = Client().get(reverse("public:home"))
    content = response.content
    assert b'href="#main"' in content
    assert b"Skip to main content" in content


def test_base_template_main_has_id() -> None:
    """The <main> element carries id=main so the skip link target resolves."""
    response = Client().get(reverse("public:home"))
    assert b'id="main"' in response.content


def test_nav_has_aria_label() -> None:
    """The <nav> element carries an aria-label for landmark disambiguation."""
    response = Client().get(reverse("public:how_it_works"))
    assert b"Main navigation" in response.content


def test_nav_authenticated_shows_account_menu() -> None:
    """A signed-in user sees the profile-icon account menu with My account and
    Sign out (VERB-47).
    """
    user = UserFactory.create()
    client = Client()
    client.force_login(user)
    content = client.get(reverse("public:how_it_works")).content
    assert b"Account menu" in content
    assert b"My account" in content
    assert b"Sign out" in content


def test_nav_anonymous_shows_sign_in() -> None:
    """An anonymous visitor sees Sign in and no account menu (VERB-47)."""
    content = Client().get(reverse("public:how_it_works")).content
    assert b"Sign in" in content
    assert b"Account menu" not in content


def test_register_form_labels_associated_with_inputs() -> None:
    """The registration form renders <label for> matching each input id."""
    response = Client().get(reverse("public:register") + "?role=ambassador")
    content = response.content.decode()
    for field_name in ("first_name", "last_name", "email"):
        widget_id = f"id_{field_name}"
        assert f'for="{widget_id}"' in content, (
            f"No <label for='{widget_id}'> found on register page"
        )
        assert f'id="{widget_id}"' in content, (
            f"No input with id='{widget_id}' found on register page"
        )


def test_register_form_error_has_role_alert() -> None:
    """Field error messages carry role=alert so they are announced on injection."""
    response = Client().post(
        reverse("public:register") + "?role=ambassador",
        {
            "role": "ambassador",
            "first_name": "",
            "last_name": "Test",
            "email": "test@example.com",
            "prior_pass": "SEASONAL",
            "phone": "",
            "preferred_language": "",
            "preferred_location": "",
            "prior_pass_attestation": "",
            "terms_accepted": "",
        },
    )
    assert response.status_code == 200
    assert b'role="alert"' in response.content


def test_hero_image_has_nonempty_alt() -> None:
    """The homepage hero image has a non-empty alt attribute."""
    import re

    response = Client().get(reverse("public:home"))
    content = response.content.decode()
    assert "images/hero.jpg" in content
    hero_img = re.search(r"<img[^>]+hero\.jpg[^>]*>", content, re.DOTALL)
    assert hero_img is not None, "hero img tag not found"
    alt_match = re.search(r'alt=["\']([^"\']*)["\']', hero_img.group(0))
    assert alt_match is not None, "hero img has no alt attribute"
    assert alt_match.group(1).strip() != "", "hero img alt is empty"


def test_match_actions_container_has_aria_live() -> None:
    """The match-actions container carries aria-live so state changes are announced."""
    amb_reg = RegistrationFactory.create(
        role=Registration.Role.AMBASSADOR,
        status=Registration.Status.VERIFIED,
    )
    ref_reg = RegistrationFactory.create(
        role=Registration.Role.REFEREE,
        status=Registration.Status.VERIFIED,
    )
    match = MatchFactory.create(
        ambassador_registration=amb_reg,
        referee_registration=ref_reg,
        status=Match.Status.PROPOSED,
        expires_at=datetime(2026, 12, 31, tzinfo=UTC),
    )
    token = make_match_access_token(match.pk, amb_reg.pk)
    response = Client().get(reverse("public:match", args=[token]))
    assert response.status_code == 200
    assert b'aria-live="polite"' in response.content
    assert b'id="match-actions"' in response.content
