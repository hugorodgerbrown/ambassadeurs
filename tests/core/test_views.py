# Tests for core views.

import pytest
from django.test import Client


@pytest.mark.django_db
def test_robots_txt_status_and_content_type() -> None:
    """GET /robots.txt returns 200 with a text/plain content type."""
    client = Client()
    response = client.get("/robots.txt")
    assert response.status_code == 200
    assert response["Content-Type"].startswith("text/plain")


@pytest.mark.django_db
def test_robots_txt_disallows_admin() -> None:
    """The robots.txt body contains a Disallow directive for /admin/."""
    client = Client()
    response = client.get("/robots.txt")
    assert b"Disallow: /admin/" in response.content


@pytest.mark.django_db
def test_robots_txt_disallows_account() -> None:
    """The robots.txt body contains a Disallow directive for /account/."""
    client = Client()
    response = client.get("/robots.txt")
    assert b"Disallow: /account/" in response.content


@pytest.mark.django_db
def test_robots_txt_disallows_match() -> None:
    """The robots.txt body contains a Disallow directive for /match/."""
    client = Client()
    response = client.get("/robots.txt")
    assert b"Disallow: /match/" in response.content


@pytest.mark.django_db
def test_robots_txt_disallows_register_confirm() -> None:
    """The robots.txt body contains a Disallow directive for /register/confirm/."""
    client = Client()
    response = client.get("/robots.txt")
    assert b"Disallow: /register/confirm/" in response.content


@pytest.mark.django_db
def test_robots_txt_sitemap_line() -> None:
    """The robots.txt body contains a Sitemap line ending with /sitemap.xml."""
    client = Client()
    response = client.get("/robots.txt")
    body = response.content.decode()
    sitemap_lines = [line for line in body.splitlines() if line.startswith("Sitemap:")]
    assert len(sitemap_lines) == 1
    assert sitemap_lines[0].endswith("/sitemap.xml")


@pytest.mark.django_db
def test_robots_txt_rejects_post() -> None:
    """POST /robots.txt returns 405 (method not allowed)."""
    client = Client()
    response = client.post("/robots.txt")
    assert response.status_code == 405
