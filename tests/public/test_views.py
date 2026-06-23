# Tests for the public site views.

import pytest
from django.contrib.auth.models import User
from django.core import mail
from django.test import Client
from django.urls import reverse

from accounts.tokens import make_email_verification_token
from matching.models import Registration
from tests.accounts.factories import UserFactory
from tests.matching.factories import PriceCategoryFactory, SeasonFactory

pytestmark = pytest.mark.django_db


def test_home_renders() -> None:
    """The landing page returns 200 and uses the home template."""
    response = Client().get(reverse("public:home"))
    assert response.status_code == 200
    assert "public/home.html" in [t.name for t in response.templates]


def test_home_shows_both_role_ctas() -> None:
    """The homepage links to the register entry with each role hint."""
    SeasonFactory.create()
    response = Client().get(reverse("public:home"))
    content = response.content
    register = reverse("public:register").encode()
    assert register + b"?role=ambassador" in content
    assert register + b"?role=referee" in content
    assert b"I'm an Ambassador" in content
    assert b"I'm a Referee" in content


def test_home_shows_opens_soon_when_no_active_season() -> None:
    """With no active season the homepage shows the opens-soon notice."""
    response = Client().get(reverse("public:home"))
    assert b"Registration opens soon" in response.content


def test_home_hides_opens_soon_when_season_active() -> None:
    """With an active season the opens-soon notice is hidden."""
    SeasonFactory.create()
    response = Client().get(reverse("public:home"))
    assert b"Registration opens soon" not in response.content


def test_register_start_renders_email_form() -> None:
    """The entry page asks for an email before anything role-specific."""
    SeasonFactory.create()
    response = Client().get(reverse("public:register"))
    assert response.status_code == 200
    assert "public/register_start.html" in [t.name for t in response.templates]
    assert b'name="email"' in response.content


def test_register_start_seeds_role_hint_in_session() -> None:
    """A ?role= hint from the homepage CTA is remembered in the session."""
    SeasonFactory.create()
    client = Client()
    client.get(reverse("public:register") + "?role=ambassador")
    assert client.session["register_role"] == "ambassador"


def test_register_start_closed_when_no_active_season() -> None:
    """With no active season the closed-registration page is shown."""
    response = Client().get(reverse("public:register"))
    assert "public/register_closed.html" in [t.name for t in response.templates]


def test_register_start_authenticated_redirects_to_details() -> None:
    """An already-signed-in user skips straight to the details step."""
    SeasonFactory.create()
    client = Client()
    client.force_login(UserFactory.create())
    response = client.get(reverse("public:register"))
    assert response.status_code == 302
    assert response.url == reverse("public:register_details")


def test_register_start_post_sends_verification_email() -> None:
    """Submitting an email sends a verification link and shows the sent page."""
    SeasonFactory.create()
    response = Client().post(reverse("public:register"), {"email": "ADA@example.com"})
    assert response.status_code == 302
    assert response.url == reverse("public:register_email_sent")
    assert len(mail.outbox) == 1
    assert "register/verify/" in mail.outbox[0].body
    assert mail.outbox[0].to == ["ada@example.com"]


def test_register_start_post_invalid_email_redisplays() -> None:
    """An invalid email re-renders the entry page and sends nothing."""
    SeasonFactory.create()
    response = Client().post(reverse("public:register"), {"email": "not-an-email"})
    assert response.status_code == 200
    assert len(mail.outbox) == 0


def test_register_email_sent_renders() -> None:
    """The check-your-inbox page renders."""
    response = Client().get(reverse("public:register_email_sent"))
    assert response.status_code == 200
    assert "public/register_email_sent.html" in [t.name for t in response.templates]


def test_register_verify_valid_token_logs_in_and_creates_user() -> None:
    """A valid token creates the user, logs them in and goes to details."""
    SeasonFactory.create()
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
    SeasonFactory.create()
    response = Client().get(reverse("public:register_details"))
    assert response.status_code == 302
    assert reverse("account_login") in response.url


def test_register_details_renders_role_chooser() -> None:
    """The details page offers the role choice to a signed-in user."""
    SeasonFactory.create()
    client = Client()
    client.force_login(UserFactory.create())
    response = client.get(reverse("public:register_details"))
    assert response.status_code == 200
    assert b"Which one are you?" in response.content


def test_register_details_closed_without_season() -> None:
    """With no active season the details step shows the closed page."""
    client = Client()
    client.force_login(UserFactory.create())
    response = client.get(reverse("public:register_details"))
    assert response.status_code == 200
    assert "public/register_closed.html" in [t.name for t in response.templates]


def test_details_form_fragment_closed_without_season_404() -> None:
    """The fragment endpoint 404s when no season is open."""
    client = Client()
    client.force_login(UserFactory.create())
    response = client.get(
        reverse("public:register_details_form") + "?role=ambassador",
        headers={"hx-request": "true"},
    )
    assert response.status_code == 404


def test_details_form_fragment_requires_htmx() -> None:
    """The details form fragment rejects a plain (non-HTMX) request."""
    SeasonFactory.create()
    client = Client()
    client.force_login(UserFactory.create())
    response = client.get(reverse("public:register_details_form") + "?role=ambassador")
    assert response.status_code == 400


def test_details_form_fragment_returns_role_form() -> None:
    """An HTMX request returns the role-specific form fragment."""
    season = SeasonFactory.create()
    PriceCategoryFactory.create(season=season)
    client = Client()
    client.force_login(UserFactory.create())
    response = client.get(
        reverse("public:register_details_form") + "?role=referee",
        headers={"hx-request": "true"},
    )
    assert response.status_code == 200
    assert b"Referee details" in response.content
    assert b"genuinely new" in response.content


def test_details_form_fragment_unknown_role_404() -> None:
    """An unknown role on the fragment endpoint returns 404."""
    SeasonFactory.create()
    client = Client()
    client.force_login(UserFactory.create())
    response = client.get(
        reverse("public:register_details_form") + "?role=banana",
        headers={"hx-request": "true"},
    )
    assert response.status_code == 404


def test_register_details_post_creates_registration() -> None:
    """A valid details POST creates the registration linked to the user."""
    season = SeasonFactory.create()
    category = PriceCategoryFactory.create(season=season)
    user = UserFactory.create(email="ada@example.com")
    client = Client()
    client.force_login(user)
    response = client.post(
        reverse("public:register_details"),
        {
            "role": "referee",
            "first_name": "Ada",
            "last_name": "Lovelace",
            "price_category": category.pk,
            "attestation": True,
        },
    )
    assert response.status_code == 302
    assert response.url == reverse("public:register_done", args=["referee"])
    assert User.objects.count() == 1
    registration = Registration.objects.get()
    assert registration.account.user == user
    assert registration.role == Registration.Role.REFEREE
    assert registration.held_prior_pass is False


def test_register_details_post_invalid_redisplays() -> None:
    """An invalid details POST (no attestation) re-renders, creates nothing."""
    season = SeasonFactory.create()
    category = PriceCategoryFactory.create(season=season)
    client = Client()
    client.force_login(UserFactory.create())
    response = client.post(
        reverse("public:register_details"),
        {
            "role": "ambassador",
            "first_name": "Ada",
            "last_name": "Lovelace",
            "price_category": category.pk,
        },
    )
    assert response.status_code == 200
    assert not Registration.objects.exists()


def test_register_details_post_unknown_role_404() -> None:
    """A details POST with an unknown role returns 404."""
    SeasonFactory.create()
    client = Client()
    client.force_login(UserFactory.create())
    response = client.post(reverse("public:register_details"), {"role": "banana"})
    assert response.status_code == 404


def test_register_start_hides_facebook_button_without_provider() -> None:
    """With no configured Facebook provider, the button is not rendered."""
    SeasonFactory.create()
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
