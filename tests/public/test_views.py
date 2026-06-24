# Tests for the public site views.

import pytest
from django.conf import settings
from django.contrib.auth.models import User
from django.core import mail
from django.test import Client, override_settings
from django.urls import reverse

from accounts.tokens import make_email_verification_token
from matching.models import Registration
from public.models import FormDownload
from tests.accounts.factories import UserFactory

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


def test_register_start_renders_email_form() -> None:
    """The entry page asks for an email before anything role-specific."""
    response = Client().get(reverse("public:register"))
    assert response.status_code == 200
    assert "public/register_start.html" in [t.name for t in response.templates]
    assert b'name="email"' in response.content


def test_register_start_seeds_role_hint_in_session() -> None:
    """A ?role= hint from the homepage CTA is remembered in the session."""
    client = Client()
    client.get(reverse("public:register") + "?role=ambassador")
    assert client.session["register_role"] == "ambassador"


@override_settings(
    REGISTRATION_OPENS_AT="2020-01-01T00:00:00+00:00",
    REGISTRATION_CLOSES_AT="2020-12-31T23:59:59+00:00",
)
def test_register_start_closed_when_registration_closed() -> None:
    """With registration closed the closed-registration page is shown."""
    response = Client().get(reverse("public:register"))
    assert "public/register_closed.html" in [t.name for t in response.templates]


def test_register_start_authenticated_redirects_to_details() -> None:
    """An already-signed-in user skips straight to the details step."""
    client = Client()
    client.force_login(UserFactory.create())
    response = client.get(reverse("public:register"))
    assert response.status_code == 302
    assert response.url == reverse("public:register_details")


def test_register_start_post_sends_verification_email() -> None:
    """Submitting an email sends a verification link and shows the sent page."""
    response = Client().post(reverse("public:register"), {"email": "ADA@example.com"})
    assert response.status_code == 302
    assert response.url == reverse("public:register_email_sent")
    assert len(mail.outbox) == 1
    assert "register/verify/" in mail.outbox[0].body
    assert mail.outbox[0].to == ["ada@example.com"]


def test_register_start_post_invalid_email_redisplays() -> None:
    """An invalid email re-renders the entry page and sends nothing."""
    response = Client().post(reverse("public:register"), {"email": "not-an-email"})
    assert response.status_code == 200
    assert len(mail.outbox) == 0


def test_register_email_sent_renders() -> None:
    """The check-your-inbox page renders."""
    response = Client().get(reverse("public:register_email_sent"))
    assert response.status_code == 200
    assert "public/register_email_sent.html" in [t.name for t in response.templates]


@override_settings(DEBUG=True)
def test_register_email_sent_shows_verify_link_in_debug() -> None:
    """In DEBUG the sent page surfaces the verify link for click-through testing."""
    client = Client()
    response = client.post(
        reverse("public:register"), {"email": "ada@example.com"}, follow=True
    )
    assert response.status_code == 200
    assert b"Development shortcut" in response.content
    assert b"register/verify/" in response.content
    # The one-shot value is popped, so a reload no longer shows the link.
    assert "debug_verify_url" not in client.session
    reload = client.get(reverse("public:register_email_sent"))
    assert b"Development shortcut" not in reload.content


@override_settings(DEBUG=False)
def test_register_email_sent_hides_verify_link_outside_debug() -> None:
    """Outside DEBUG the verify link is never stashed or shown."""
    client = Client()
    response = client.post(
        reverse("public:register"), {"email": "ada@example.com"}, follow=True
    )
    assert response.status_code == 200
    assert b"Development shortcut" not in response.content
    assert "debug_verify_url" not in client.session


def test_register_verify_valid_token_logs_in_and_creates_user() -> None:
    """A valid token creates the user, logs them in and goes to details."""
    token = make_email_verification_token("ada@example.com")
    client = Client()
    response = client.get(reverse("public:register_verify", args=[token]))
    assert response.status_code == 302
    assert response.url == reverse("public:register_details")
    assert User.objects.filter(username="ada@example.com").exists()
    assert "_auth_user_id" in client.session


def test_register_verify_invalid_token_returns_400() -> None:
    """A tampered or expired token shows the invalid-link page with 400."""
    response = Client().get(reverse("public:register_verify", args=["not-a-token"]))
    assert response.status_code == 400
    assert "public/register_invalid.html" in [t.name for t in response.templates]


def test_register_details_requires_login() -> None:
    """Anonymous users are redirected away from the details step."""
    response = Client().get(reverse("public:register_details"))
    assert response.status_code == 302
    assert reverse("account_login") in response.url


def test_register_details_renders_role_chooser() -> None:
    """The details page renders the role select to a signed-in user."""
    client = Client()
    client.force_login(UserFactory.create())
    response = client.get(reverse("public:register_details"))
    assert response.status_code == 200
    assert b"Confirm your role" in response.content
    assert b"<select" in response.content
    assert b'name="role"' in response.content


def test_register_details_get_with_ambassador_hint_preselects_ambassador() -> None:
    """A session role hint of 'ambassador' pre-selects the ambassador option."""
    client = Client()
    client.force_login(UserFactory.create())
    session = client.session
    session["register_role"] = "ambassador"
    session.save()
    response = client.get(reverse("public:register_details"))
    assert response.status_code == 200
    content = response.content
    # The ambassador option must be selected; the referee option must not be.
    assert b'value="ambassador"' in content
    assert b'value="referee"' in content
    # djangofmt renders selected as an attribute on the option line
    ambassador_idx = content.index(b'value="ambassador"')
    referee_idx = content.index(b'value="referee"')
    ambassador_block = content[ambassador_idx : ambassador_idx + 200]
    referee_block = content[referee_idx : referee_idx + 200]
    assert b"selected" in ambassador_block
    assert b"selected" not in referee_block


def test_register_details_get_with_referee_hint_preselects_referee() -> None:
    """A session role hint of 'referee' pre-selects the referee option."""
    client = Client()
    client.force_login(UserFactory.create())
    session = client.session
    session["register_role"] = "referee"
    session.save()
    response = client.get(reverse("public:register_details"))
    assert response.status_code == 200
    content = response.content
    ambassador_idx = content.index(b'value="ambassador"')
    referee_idx = content.index(b'value="referee"')
    ambassador_block = content[ambassador_idx : ambassador_idx + 200]
    referee_block = content[referee_idx : referee_idx + 200]
    assert b"selected" not in ambassador_block
    assert b"selected" in referee_block


def test_register_details_get_no_hint_shows_blank_prompt() -> None:
    """With no session hint the blank 'Choose your role…' prompt is selected."""
    client = Client()
    client.force_login(UserFactory.create())
    response = client.get(reverse("public:register_details"))
    assert response.status_code == 200
    content = response.content
    assert b"Choose your role" in content
    # Neither role option should be selected.
    ambassador_idx = content.index(b'value="ambassador"')
    referee_idx = content.index(b'value="referee"')
    ambassador_block = content[ambassador_idx : ambassador_idx + 200]
    referee_block = content[referee_idx : referee_idx + 200]
    assert b"selected" not in ambassador_block
    assert b"selected" not in referee_block


def test_details_form_fragment_ambassador_contains_qualifying_criteria() -> None:
    """The ambassador fragment lists the ambassador qualifying criteria."""
    client = Client()
    client.force_login(UserFactory.create())
    response = client.get(
        reverse("public:register_details_form") + "?role=ambassador",
        headers={"hx-request": "true"},
    )
    assert response.status_code == 200
    assert b"To qualify as an Ambassador you must:" in response.content
    assert b"No retroactive refund." in response.content
    # Mont 4 Card clause is ambassador-specific.
    assert b"Mont 4 Card" in response.content


def test_details_form_fragment_referee_contains_qualifying_criteria() -> None:
    """The referee fragment lists the referee qualifying criteria."""
    client = Client()
    client.force_login(UserFactory.create())
    response = client.get(
        reverse("public:register_details_form") + "?role=referee",
        headers={"hx-request": "true"},
    )
    assert response.status_code == 200
    assert b"To qualify as a Referee you must:" in response.content
    assert b"No retroactive refund." in response.content
    # The buy-together / no-online clause is referee-specific.
    assert b"cannot be bought online" in response.content


def test_register_details_post_invalid_reflects_bound_role_as_selected() -> None:
    """A failed POST re-renders with the submitted role pre-selected in the dropdown."""
    client = Client()
    client.force_login(UserFactory.create())
    response = client.post(
        reverse("public:register_details"),
        {
            "role": "ambassador",
            "first_name": "Ada",
            "last_name": "Lovelace",
            "prior_pass": Registration.PriorPass.SEASONAL,
            # attestation omitted — causes validation failure
        },
    )
    assert response.status_code == 200
    content = response.content
    ambassador_idx = content.index(b'value="ambassador"')
    ambassador_block = content[ambassador_idx : ambassador_idx + 200]
    assert b"selected" in ambassador_block


@override_settings(
    REGISTRATION_OPENS_AT="2020-01-01T00:00:00+00:00",
    REGISTRATION_CLOSES_AT="2020-12-31T23:59:59+00:00",
)
def test_register_details_closed_without_open_window() -> None:
    """With registration closed the details step shows the closed page."""
    client = Client()
    client.force_login(UserFactory.create())
    response = client.get(reverse("public:register_details"))
    assert response.status_code == 200
    assert "public/register_closed.html" in [t.name for t in response.templates]


@override_settings(
    REGISTRATION_OPENS_AT="2020-01-01T00:00:00+00:00",
    REGISTRATION_CLOSES_AT="2020-12-31T23:59:59+00:00",
)
def test_details_form_fragment_closed_without_open_window_404() -> None:
    """The fragment endpoint 404s when registration is closed."""
    client = Client()
    client.force_login(UserFactory.create())
    response = client.get(
        reverse("public:register_details_form") + "?role=ambassador",
        headers={"hx-request": "true"},
    )
    assert response.status_code == 404


def test_details_form_fragment_requires_htmx() -> None:
    """The details form fragment rejects a plain (non-HTMX) request."""
    client = Client()
    client.force_login(UserFactory.create())
    response = client.get(reverse("public:register_details_form") + "?role=ambassador")
    assert response.status_code == 400


def test_details_form_fragment_returns_role_form() -> None:
    """An HTMX request returns the role-specific form fragment."""
    client = Client()
    client.force_login(UserFactory.create())
    response = client.get(
        reverse("public:register_details_form") + "?role=referee",
        headers={"hx-request": "true"},
    )
    assert response.status_code == 200
    assert b"Referee details" in response.content
    assert b"To qualify as a Referee you must:" in response.content


def test_details_form_fragment_unknown_role_404() -> None:
    """An unknown role on the fragment endpoint returns 404."""
    client = Client()
    client.force_login(UserFactory.create())
    response = client.get(
        reverse("public:register_details_form") + "?role=banana",
        headers={"hx-request": "true"},
    )
    assert response.status_code == 404


def test_register_details_post_creates_registration() -> None:
    """A valid details POST creates the registration linked to the user."""
    user = UserFactory.create(email="ada@example.com")
    client = Client()
    client.force_login(user)
    response = client.post(
        reverse("public:register_details"),
        {
            "role": "referee",
            "first_name": "Ada",
            "last_name": "Lovelace",
            "prior_pass_attestation": True,
            "terms_accepted": True,
        },
    )
    assert response.status_code == 302
    assert response.url == reverse("public:register_done", args=["referee"])
    assert User.objects.count() == 1
    registration = Registration.objects.get()
    assert registration.user == user
    assert registration.role == Registration.Role.REFEREE
    assert registration.prior_pass == Registration.PriorPass.NONE


def test_register_details_post_invalid_redisplays() -> None:
    """An invalid details POST (no attestation) re-renders, creates nothing."""
    client = Client()
    client.force_login(UserFactory.create())
    response = client.post(
        reverse("public:register_details"),
        {
            "role": "ambassador",
            "first_name": "Ada",
            "last_name": "Lovelace",
            "prior_pass": Registration.PriorPass.SEASONAL,
        },
    )
    assert response.status_code == 200
    assert not Registration.objects.exists()


def test_register_details_post_unknown_role_404() -> None:
    """A details POST with an unknown role returns 404."""
    client = Client()
    client.force_login(UserFactory.create())
    response = client.post(reverse("public:register_details"), {"role": "banana"})
    assert response.status_code == 404


def test_register_start_hides_facebook_button_without_provider() -> None:
    """With no configured Facebook provider, the button is not rendered."""
    response = Client().get(reverse("public:register"))
    assert b"Continue with Facebook" not in response.content


def test_register_done_renders() -> None:
    """The confirmation page renders for a valid role."""
    response = Client().get(reverse("public:register_done", args=["referee"]))
    assert response.status_code == 200
    assert "public/register_done.html" in [t.name for t in response.templates]


def test_register_done_unknown_role_404() -> None:
    """An unknown role slug on the confirmation page returns 404."""
    response = Client().get(reverse("public:register_done", args=["banana"]))
    assert response.status_code == 404


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
# How it works page
# ---------------------------------------------------------------------------


def test_how_it_works_renders_for_anonymous_user() -> None:
    """The how-it-works page returns 200 with the correct template (anonymous)."""
    response = Client().get(reverse("public:how_it_works"))
    assert response.status_code == 200
    assert "public/how_it_works.html" in [t.name for t in response.templates]


def test_how_it_works_contains_section_markers() -> None:
    """The how-it-works page includes all eight section headings."""
    response = Client().get(reverse("public:how_it_works"))
    content = response.content
    assert b"What is the 4 Vall\xc3\xa9es Ambassadors Program?" in content
    assert b"Who is an Ambassador and who is a Referee?" in content
    assert b"How do I apply?" in content
    assert b"What is the process?" in content
    assert b"What are the requirements?" in content
    assert b"What does this site do \xe2\x80\x94 and not do?" in content
    assert b"How does the match work?" in content
    assert b"What happens next?" in content


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
