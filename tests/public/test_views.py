# Tests for the public site views.

import pytest
from django.contrib.auth.models import User
from django.test import Client
from django.urls import reverse

from matching.models import Registration
from tests.matching.factories import PriceCategoryFactory, SeasonFactory

pytestmark = pytest.mark.django_db


def test_home_renders() -> None:
    """The landing page returns 200 and uses the home template."""
    response = Client().get(reverse("public:home"))
    assert response.status_code == 200
    assert "public/home.html" in [t.name for t in response.templates]


def test_home_shows_both_role_ctas() -> None:
    """The homepage links to both registration routes with disambiguation copy."""
    SeasonFactory.create()
    response = Client().get(reverse("public:home"))
    content = response.content
    assert reverse("public:register", args=["ambassador"]).encode() in content
    assert reverse("public:register", args=["referee"]).encode() in content
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


def test_register_get_renders_role_form() -> None:
    """The ambassador registration page renders the form with role copy."""
    SeasonFactory.create()
    response = Client().get(reverse("public:register", args=["ambassador"]))
    assert response.status_code == 200
    assert "public/register.html" in [t.name for t in response.templates]
    assert b"Ambassador registration" in response.content


def test_register_unknown_role_404() -> None:
    """An unknown role slug returns 404."""
    SeasonFactory.create()
    response = Client().get(reverse("public:register", args=["banana"]))
    assert response.status_code == 404


def test_register_closed_when_no_active_season() -> None:
    """With no active season the closed-registration page is shown."""
    SeasonFactory.create(is_active=False)
    response = Client().get(reverse("public:register", args=["ambassador"]))
    assert response.status_code == 200
    assert "public/register_closed.html" in [t.name for t in response.templates]


def test_register_post_creates_registration_and_redirects() -> None:
    """A valid POST creates the registration and redirects to the done page."""
    season = SeasonFactory.create()
    category = PriceCategoryFactory.create(season=season)
    response = Client().post(
        reverse("public:register", args=["ambassador"]),
        {
            "first_name": "Ada",
            "last_name": "Lovelace",
            "email": "ada@example.com",
            "price_category": category.pk,
            "attestation": True,
        },
    )
    assert response.status_code == 302
    assert response.url == reverse("public:register_done", args=["ambassador"])
    assert User.objects.filter(username="ada@example.com").exists()
    registration = Registration.objects.get()
    assert registration.role == Registration.Role.AMBASSADOR


def test_register_post_invalid_redisplays_form() -> None:
    """An invalid POST (no attestation) re-renders the form, creates nothing."""
    season = SeasonFactory.create()
    category = PriceCategoryFactory.create(season=season)
    response = Client().post(
        reverse("public:register", args=["ambassador"]),
        {
            "first_name": "Ada",
            "last_name": "Lovelace",
            "email": "ada@example.com",
            "price_category": category.pk,
        },
    )
    assert response.status_code == 200
    assert not Registration.objects.exists()


def test_register_done_renders() -> None:
    """The confirmation page renders for a valid role."""
    response = Client().get(reverse("public:register_done", args=["referee"]))
    assert response.status_code == 200
    assert "public/register_done.html" in [t.name for t in response.templates]


def test_register_done_unknown_role_404() -> None:
    """An unknown role slug on the confirmation page returns 404."""
    response = Client().get(reverse("public:register_done", args=["banana"]))
    assert response.status_code == 404


def test_referee_get_renders_referee_copy_and_cross_link() -> None:
    """The referee page shows the referee banner and the Ambassador cross-link."""
    SeasonFactory.create()
    response = Client().get(reverse("public:register", args=["referee"]))
    assert response.status_code == 200
    assert b"Referee registration" in response.content
    assert b"genuinely new" in response.content
    assert reverse("public:register", args=["ambassador"]).encode() in response.content


def test_referee_post_creates_genuinely_new_registration() -> None:
    """A referee POST creates a REFEREE registration with held_prior_pass False."""
    season = SeasonFactory.create()
    category = PriceCategoryFactory.create(season=season)
    response = Client().post(
        reverse("public:register", args=["referee"]),
        {
            "first_name": "Grace",
            "last_name": "Hopper",
            "email": "grace@example.com",
            "price_category": category.pk,
            "attestation": True,
        },
    )
    assert response.status_code == 302
    assert response.url == reverse("public:register_done", args=["referee"])
    registration = Registration.objects.get()
    assert registration.role == Registration.Role.REFEREE
    assert registration.held_prior_pass is False


def test_referee_done_renders_referee_copy() -> None:
    """The referee confirmation page renders the referee message."""
    response = Client().get(reverse("public:register_done", args=["referee"]))
    assert response.status_code == 200
    assert b"Ambassador" in response.content


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
