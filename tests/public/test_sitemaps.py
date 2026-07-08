# Tests for the sitemap view (VERB-57).
#
# Verifies that GET /sitemap.xml returns a valid XML response containing the
# expected public page URLs and excluding restricted routes.

import pytest
from django.test import Client
from django.urls import reverse

pytestmark = pytest.mark.django_db


def test_sitemap_returns_200() -> None:
    """GET /sitemap.xml returns HTTP 200."""
    response = Client().get("/sitemap.xml")
    assert response.status_code == 200


def test_sitemap_content_type_is_xml() -> None:
    """The sitemap response has an XML content type."""
    response = Client().get("/sitemap.xml")
    assert "application/xml" in response.get("Content-Type", "")


def test_sitemap_contains_home_url() -> None:
    """The sitemap includes the home page URL."""
    response = Client().get("/sitemap.xml")
    home_url = reverse("public:home")
    assert home_url.encode() in response.content


def test_sitemap_contains_how_it_works_url() -> None:
    """The sitemap includes the how-it-works page URL."""
    response = Client().get("/sitemap.xml")
    url = reverse("public:how_it_works")
    assert url.encode() in response.content


def test_sitemap_contains_faq_url() -> None:
    """The sitemap includes the FAQ page URL."""
    response = Client().get("/sitemap.xml")
    url = reverse("public:faq")
    assert url.encode() in response.content


def test_sitemap_contains_about_url() -> None:
    """The sitemap includes the About page URL."""
    response = Client().get("/sitemap.xml")
    url = reverse("public:about")
    assert url.encode() in response.content


def test_sitemap_contains_legal_page_urls() -> None:
    """The sitemap includes all three legal page URLs."""
    response = Client().get("/sitemap.xml")
    for page in ("privacy", "cookies", "terms"):
        url = reverse("public:legal", kwargs={"page": page})
        assert url.encode() in response.content, f"Missing legal page: {page}"


def test_sitemap_excludes_admin() -> None:
    """The sitemap does not expose any admin routes."""
    response = Client().get("/sitemap.xml")
    assert b"/admin/" not in response.content


def test_sitemap_excludes_account_routes() -> None:
    """The sitemap does not expose account/auth routes."""
    response = Client().get("/sitemap.xml")
    assert b"/account/" not in response.content


def test_sitemap_excludes_match_routes() -> None:
    """The sitemap does not expose match token routes."""
    response = Client().get("/sitemap.xml")
    assert b"/match/" not in response.content
