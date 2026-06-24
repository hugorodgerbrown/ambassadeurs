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
from matching.services import accept_match
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
    assert b"Ambassador details" in response.content
    assert b"role-theme--referee" not in response.content


def test_register_get_with_referee_role_hint() -> None:
    """GET /register/?role=referee themes the form for the referee."""
    response = Client().get(reverse("public:register") + "?role=referee")
    assert response.status_code == 200
    assert b"Referee details" in response.content
    assert b"role-theme--referee" in response.content


def test_register_get_defaults_to_ambassador_on_unknown_role() -> None:
    """GET /register/?role=banana silently falls back to the ambassador form."""
    response = Client().get(reverse("public:register") + "?role=banana")
    assert response.status_code == 200
    assert b"Ambassador details" in response.content


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
    """A valid anonymous POST creates a PENDING registration (not WAITING)."""
    response = Client().post(reverse("public:register"), _valid_referee_post())
    assert response.status_code == 302
    assert response.url == reverse("public:register_email_sent")
    assert Registration.objects.count() == 1
    reg = Registration.objects.get()
    assert reg.status == Registration.Status.PENDING
    assert reg.role == Registration.Role.REFEREE


def test_register_post_sends_confirmation_email() -> None:
    """A valid anonymous POST sends a confirmation email to the supplied address."""
    Client().post(reverse("public:register"), _valid_referee_post())
    assert len(mail.outbox) == 1
    assert mail.outbox[0].to == ["grace@example.com"]
    # The confirmation link must point to the confirm endpoint, not verify.
    assert "register/confirm/" in mail.outbox[0].body


def test_register_post_pending_not_matched() -> None:
    """A PENDING registration must never trigger a match (Invariant 2)."""
    # Pre-populate a waiting ambassador — if matching ran, a Match would be created.
    RegistrationFactory.create(
        role=Registration.Role.AMBASSADOR,
        prior_pass=Registration.PriorPass.SEASONAL,
        status=Registration.Status.WAITING,
    )
    Client().post(reverse("public:register"), _valid_referee_post())
    from matching.models import Match

    assert Match.objects.count() == 0
    reg = Registration.objects.filter(role=Registration.Role.REFEREE).get()
    assert reg.status == Registration.Status.PENDING


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
    """A re-submit for an email with a PENDING registration resends the link.

    No second Registration row is created; exactly one confirmation email is
    sent (plus the initial one that was sent when the row was created by the
    factory — we reset outbox before the second POST).
    """
    # Simulate an existing PENDING row for this email.
    user = UserFactory.create(username="grace@example.com", email="grace@example.com")
    RegistrationFactory.create(
        user=user,
        role=Registration.Role.REFEREE,
        prior_pass=Registration.PriorPass.NONE,
        status=Registration.Status.PENDING,
    )
    mail.outbox.clear()

    Client().post(reverse("public:register"), _valid_referee_post())

    assert Registration.objects.filter(role=Registration.Role.REFEREE).count() == 1
    assert len(mail.outbox) == 1
    assert "register/confirm/" in mail.outbox[0].body


def test_register_post_duplicate_waiting_shows_validation_error() -> None:
    """Submitting for an email with an existing WAITING registration shows an error."""
    user = UserFactory.create(username="grace@example.com", email="grace@example.com")
    RegistrationFactory.create(
        user=user,
        role=Registration.Role.REFEREE,
        prior_pass=Registration.PriorPass.NONE,
        status=Registration.Status.WAITING,
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


def test_register_confirm_valid_token_transitions_to_waiting() -> None:
    """A valid confirm token transitions the registration PENDING → WAITING."""
    user = UserFactory.create(username="ada@example.com", email="ada@example.com")
    reg = RegistrationFactory.create(
        user=user,
        role=Registration.Role.AMBASSADOR,
        prior_pass=Registration.PriorPass.SEASONAL,
        status=Registration.Status.PENDING,
    )
    token = make_registration_confirmation_token(reg.pk)
    client = Client()
    response = client.get(reverse("public:register_confirm", args=[token]))

    assert response.status_code == 302
    assert response.url == reverse("public:register_done", args=["ambassador"])
    reg.refresh_from_db()
    assert reg.status == Registration.Status.WAITING


def test_register_confirm_logs_user_in() -> None:
    """Confirming a registration logs the user in."""
    user = UserFactory.create(username="ada@example.com", email="ada@example.com")
    reg = RegistrationFactory.create(
        user=user,
        role=Registration.Role.REFEREE,
        prior_pass=Registration.PriorPass.NONE,
        status=Registration.Status.PENDING,
    )
    token = make_registration_confirmation_token(reg.pk)
    client = Client()
    client.get(reverse("public:register_confirm", args=[token]))
    assert "_auth_user_id" in client.session
    assert int(client.session["_auth_user_id"]) == user.pk


def test_register_confirm_triggers_matching() -> None:
    """Confirming a PENDING registration proposes a match if a counterpart waits."""
    RegistrationFactory.create(
        role=Registration.Role.AMBASSADOR,
        prior_pass=Registration.PriorPass.SEASONAL,
        status=Registration.Status.WAITING,
    )
    user = UserFactory.create(username="grace@example.com", email="grace@example.com")
    reg = RegistrationFactory.create(
        user=user,
        role=Registration.Role.REFEREE,
        prior_pass=Registration.PriorPass.NONE,
        status=Registration.Status.PENDING,
    )
    token = make_registration_confirmation_token(reg.pk)
    with TestCase.captureOnCommitCallbacks(execute=True):
        Client().get(reverse("public:register_confirm", args=[token]))

    from matching.models import Match

    assert Match.objects.count() == 1
    reg.refresh_from_db()
    assert reg.status == Registration.Status.MATCHED


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
        status=Registration.Status.PENDING,
    )
    token = make_registration_confirmation_token(reg.pk)
    response = Client().get(reverse("public:register_confirm", args=[token + "x"]))
    assert response.status_code == 400
    assert "public/register_invalid.html" in [t.name for t in response.templates]


def test_register_confirm_expired_token_returns_400() -> None:
    """A well-formed but expired confirm token shows the invalid-link page with 400.

    The token is valid (correct signature) but is read with max_age=-1 to
    simulate expiry. The registration must remain PENDING (unchanged).
    """
    from unittest.mock import patch

    from accounts.tokens import read_registration_confirmation_token

    user = UserFactory.create(username="ada@example.com", email="ada@example.com")
    reg = RegistrationFactory.create(
        user=user,
        status=Registration.Status.PENDING,
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
    assert reg.status == Registration.Status.PENDING


def test_register_confirm_already_confirmed_returns_400() -> None:
    """A confirm link for a non-PENDING registration returns 400 (used/replayed)."""
    user = UserFactory.create(username="ada@example.com", email="ada@example.com")
    reg = RegistrationFactory.create(
        user=user,
        status=Registration.Status.WAITING,  # already confirmed
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
    assert b"cannot be bought online" in response.content


def test_details_form_fragment_returns_role_form() -> None:
    """An HTMX request returns the role-specific form fragment."""
    response = Client().get(
        reverse("public:register_details_form") + "?role=referee",
        headers={"hx-request": "true"},
    )
    assert response.status_code == 200
    assert b"Referee details" in response.content


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


def test_home_contains_no_facebook_reference() -> None:
    """The homepage must not mention Facebook."""
    response = Client().get(reverse("public:home"))
    assert b"Facebook" not in response.content
    assert b"facebook" not in response.content


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


def test_how_it_works_contains_section_markers() -> None:
    """The how-it-works page renders its section headings."""
    response = Client().get(reverse("public:how_it_works"))
    content = response.content
    assert b"What is the 4 Vall\xc3\xa9es Ambassadors Program?" in content
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
        status=Registration.Status.MATCHED,
    )
    referee_reg = RegistrationFactory.create(
        referee=True,
        phone="+41790008888",
        status=Registration.Status.MATCHED,
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


def test_match_detail_accepted_reveals_counterpart_pii() -> None:
    """After mutual accept the counterpart's contact details are shown."""
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
        status=Registration.Status.MATCHED,
    )
    referee_reg = RegistrationFactory.create(
        referee=True,
        phone="+41790008888",
        status=Registration.Status.MATCHED,
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
    # Waiting state: no action buttons, no counterpart PII.
    assert "Waiting for your partner to respond" in content
    assert "+41790008888" not in content


def test_match_detail_htmx_second_accept_shows_accepted_state_and_pii() -> None:
    """HTMX second accept → ACCEPTED state; counterpart PII is revealed."""
    ambassador_reg = RegistrationFactory.create(
        role=Registration.Role.AMBASSADOR,
        prior_pass=Registration.PriorPass.SEASONAL,
        phone="+41790009999",
        status=Registration.Status.MATCHED,
    )
    referee_reg = RegistrationFactory.create(
        referee=True,
        phone="+41790008888",
        status=Registration.Status.MATCHED,
    )
    match = MatchFactory.create(
        ambassador_registration=ambassador_reg,
        referee_registration=referee_reg,
    )

    # First accept — ambassador side (outside HTMX; use the service directly).
    with TestCase.captureOnCommitCallbacks(execute=False):
        accept_match(match, ambassador_reg)

    match.refresh_from_db()
    assert match.status == Match.Status.PROPOSED

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
    """HTMX decline → DECLINED state; both parties re-queued."""
    ambassador_reg = RegistrationFactory.create(
        role=Registration.Role.AMBASSADOR,
        prior_pass=Registration.PriorPass.SEASONAL,
        status=Registration.Status.MATCHED,
        priority=0,
    )
    referee_reg = RegistrationFactory.create(
        referee=True,
        status=Registration.Status.MATCHED,
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

    # Re-queue side effects: decliner back, other front.
    ambassador_reg.refresh_from_db()
    referee_reg.refresh_from_db()
    assert ambassador_reg.status == Registration.Status.WAITING
    assert ambassador_reg.priority == -1
    assert referee_reg.status == Registration.Status.WAITING
    assert referee_reg.priority == 1

    content = response.content.decode()
    assert "declined" in content.lower()


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


def test_match_detail_post_fallback_accept_redirects() -> None:
    """A no-JS POST with action=accept performs PRG redirect back to the match page."""
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
    token = make_match_access_token(match.pk, ambassador_reg.pk)
    url = reverse("public:match", args=[token])
    with TestCase.captureOnCommitCallbacks(execute=True):
        response = Client().post(url, {"action": "accept"})

    assert response.status_code == 302
    assert response.url == url


def test_match_detail_post_fallback_decline_redirects() -> None:
    """A no-JS POST with action=decline performs PRG redirect back to the match page."""
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
    token = make_match_access_token(match.pk, ambassador_reg.pk)
    url = reverse("public:match", args=[token])
    response = Client().post(url, {"action": "decline"})

    assert response.status_code == 302
    assert response.url == url
