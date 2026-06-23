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
