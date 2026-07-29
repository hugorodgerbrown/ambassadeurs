# Tests for core views.

import re

import pytest
from django.test import Client
from django.urls import reverse


@pytest.mark.django_db
def test_robots_txt_status_and_content_type() -> None:
    """GET /robots.txt returns 200 with a text/plain content type."""
    client = Client()
    response = client.get("/robots.txt")
    assert response.status_code == 200
    assert response["Content-Type"].startswith("text/plain")


@pytest.mark.django_db
def test_healthz_returns_200_with_ok_body() -> None:
    """GET /healthz/ returns HTTP 200 with plain-text body 'ok'."""
    response = Client().get(reverse("healthz"))
    assert response.status_code == 200
    assert response.content == b"ok"
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
def test_robots_txt_disallows_register_sent() -> None:
    """The robots.txt body contains a Disallow directive for /register/sent/."""
    client = Client()
    response = client.get("/robots.txt")
    assert b"Disallow: /register/sent/" in response.content


@pytest.mark.django_db
def test_robots_txt_disallows_register_done() -> None:
    """The robots.txt body contains a Disallow directive for /register/done/."""
    client = Client()
    response = client.get("/robots.txt")
    assert b"Disallow: /register/done/" in response.content


@pytest.mark.django_db
def test_robots_txt_disallows_register_pay() -> None:
    """The robots.txt body contains a Disallow directive for /register/pay/."""
    client = Client()
    response = client.get("/robots.txt")
    assert b"Disallow: /register/pay/" in response.content


@pytest.mark.django_db
def test_robots_txt_disallows_tip() -> None:
    """The robots.txt body contains a Disallow directive for /tip/."""
    client = Client()
    response = client.get("/robots.txt")
    assert b"Disallow: /tip/" in response.content


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
def test_robots_txt_content_signal() -> None:
    """robots.txt carries one Content-Signal line permitting search + ai-input."""
    response = Client().get("/robots.txt")
    body = response.content.decode()
    signal_lines = [
        line for line in body.splitlines() if line.startswith("Content-Signal:")
    ]
    assert signal_lines == ["Content-Signal: search=yes, ai-input=yes, ai-train=no"]


@pytest.mark.django_db
def test_robots_txt_content_signal_inside_user_agent_group() -> None:
    """The Content-Signal line sits inside the ``User-agent: *`` group."""
    lines = Client().get("/robots.txt").content.decode().splitlines()
    assert lines[0] == "User-agent: *"
    assert lines[1].startswith("Content-Signal:")


@pytest.mark.django_db
def test_robots_txt_rejects_post() -> None:
    """POST /robots.txt returns 405 (method not allowed)."""
    client = Client()
    response = client.post("/robots.txt")
    assert response.status_code == 405


@pytest.mark.django_db
def test_llms_txt_status_and_content_type() -> None:
    """GET /llms.txt returns 200 with a text/markdown content type."""
    response = Client().get("/llms.txt")
    assert response.status_code == 200
    assert response["Content-Type"].startswith("text/markdown")


@pytest.mark.django_db
def test_llms_txt_rejects_post() -> None:
    """POST /llms.txt returns 405 (require_GET decorator)."""
    assert Client().post("/llms.txt").status_code == 405


@pytest.mark.django_db
def test_llms_txt_llmstxt_org_structure() -> None:
    """The body opens with an H1 site name followed by a blockquote summary."""
    lines = Client().get("/llms.txt").content.decode().splitlines()
    assert lines[0] == "# Ski Parrainage"
    assert any(line.startswith("> ") for line in lines[:5])
    assert any(line.startswith("## ") for line in lines)


@pytest.mark.django_db
def test_llms_txt_links_are_absolute() -> None:
    """Every Markdown link in the body is an absolute URL on the request host."""
    body = Client().get("/llms.txt").content.decode()
    targets = re.findall(r"\]\((.*?)\)", body)
    assert targets, "expected at least one Markdown link"
    assert all(target.startswith("http://testserver/") for target in targets)


@pytest.mark.django_db
def test_llms_txt_lists_the_indexable_public_pages() -> None:
    """The index links every public content page, and none of the excluded ones."""
    body = Client().get("/llms.txt").content.decode()
    targets = set(re.findall(r"\]\((.*?)\)", body))
    paths = {target.removeprefix("http://testserver") for target in targets}
    assert {
        reverse("public:home"),
        reverse("public:how_it_works"),
        reverse("public:faq"),
        reverse("public:about"),
        reverse("public:colophon"),
        reverse("public:legal", kwargs={"page": "privacy"}),
        reverse("public:legal", kwargs={"page": "cookies"}),
        reverse("public:legal", kwargs={"page": "terms"}),
    } <= paths
    excluded = ("/register/", "/match/", "/account/", "/tip/", "/admin/")
    leaked = [p for p in paths if p.startswith(excluded)]
    assert not leaked, f"transactional paths must not be indexed: {leaked}"


@pytest.mark.django_db
def test_llms_txt_linked_pages_all_resolve() -> None:
    """Every page linked from llms.txt returns HTTP 200."""
    client = Client()
    body = client.get("/llms.txt").content.decode()
    for target in set(re.findall(r"\]\((.*?)\)", body)):
        path = target.removeprefix("http://testserver")
        assert client.get(path).status_code == 200, f"{path} did not return 200"


@pytest.mark.django_db
def test_healthz_rejects_post() -> None:
    """POST /healthz/ returns HTTP 405 (require_GET decorator)."""
    response = Client().post(reverse("healthz"))
    assert response.status_code == 405
