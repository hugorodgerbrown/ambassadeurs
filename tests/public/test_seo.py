# Tests for SEO metadata on public pages (VERB-56).
#
# Asserts that each public page emits a <meta name="description"> tag, Open
# Graph title and description tags, and a canonical <link> element. The test
# env compiles no .mo catalogues, so we assert on tag presence and attribute
# names only — never on the translated copy itself.
#
# Pages under test:
#   - home          (public:home)
#   - how-it-works  (public:how_it_works)
#   - faq           (public:faq)
#   - register      (public:register_role — the role chooser; the bare
#                    public:register name is now a redirect view with no
#                    metadata of its own, VERB-131)
#   - legal/privacy (public:legal, page="privacy")

import re

import pytest
from django.test import Client
from django.urls import reverse

pytestmark = pytest.mark.django_db


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _get(url: str) -> bytes:
    """Return the response body for a GET request to *url*."""
    response = Client().get(url)
    assert response.status_code == 200
    return response.content


# ---------------------------------------------------------------------------
# meta description
# ---------------------------------------------------------------------------


def test_home_has_meta_description() -> None:
    """The home page emits a <meta name="description"> tag."""
    content = _get(reverse("public:home"))
    assert b'name="description"' in content


def test_how_it_works_has_meta_description() -> None:
    """The how-it-works page emits a <meta name="description"> tag."""
    content = _get(reverse("public:how_it_works"))
    assert b'name="description"' in content


def test_faq_has_meta_description() -> None:
    """The FAQ page emits a <meta name="description"> tag."""
    content = _get(reverse("public:faq"))
    assert b'name="description"' in content


def test_register_has_meta_description() -> None:
    """The register page emits a <meta name="description"> tag."""
    content = _get(reverse("public:register_role"))
    assert b'name="description"' in content


def test_legal_privacy_has_meta_description() -> None:
    """The privacy-policy legal page emits a <meta name="description"> tag."""
    content = _get(reverse("public:legal", kwargs={"page": "privacy"}))
    assert b'name="description"' in content


# ---------------------------------------------------------------------------
# og:title
# ---------------------------------------------------------------------------


def test_home_has_og_title() -> None:
    """The home page emits an og:title Open Graph tag."""
    content = _get(reverse("public:home"))
    assert b'property="og:title"' in content


def test_how_it_works_has_og_title() -> None:
    """The how-it-works page emits an og:title Open Graph tag."""
    content = _get(reverse("public:how_it_works"))
    assert b'property="og:title"' in content


def test_faq_has_og_title() -> None:
    """The FAQ page emits an og:title Open Graph tag."""
    content = _get(reverse("public:faq"))
    assert b'property="og:title"' in content


def test_register_has_og_title() -> None:
    """The register page emits an og:title Open Graph tag."""
    content = _get(reverse("public:register_role"))
    assert b'property="og:title"' in content


def test_legal_privacy_has_og_title() -> None:
    """The privacy-policy legal page emits an og:title Open Graph tag."""
    content = _get(reverse("public:legal", kwargs={"page": "privacy"}))
    assert b'property="og:title"' in content


# ---------------------------------------------------------------------------
# og:description
# ---------------------------------------------------------------------------


def test_home_has_og_description() -> None:
    """The home page emits an og:description Open Graph tag."""
    content = _get(reverse("public:home"))
    assert b'property="og:description"' in content


def test_how_it_works_has_og_description() -> None:
    """The how-it-works page emits an og:description Open Graph tag."""
    content = _get(reverse("public:how_it_works"))
    assert b'property="og:description"' in content


def test_faq_has_og_description() -> None:
    """The FAQ page emits an og:description Open Graph tag."""
    content = _get(reverse("public:faq"))
    assert b'property="og:description"' in content


def test_register_has_og_description() -> None:
    """The register page emits an og:description Open Graph tag."""
    content = _get(reverse("public:register_role"))
    assert b'property="og:description"' in content


def test_legal_privacy_has_og_description() -> None:
    """The privacy-policy legal page emits an og:description Open Graph tag."""
    content = _get(reverse("public:legal", kwargs={"page": "privacy"}))
    assert b'property="og:description"' in content


# ---------------------------------------------------------------------------
# canonical link
# ---------------------------------------------------------------------------


def test_home_has_canonical_link() -> None:
    """The home page emits a <link rel="canonical"> element."""
    content = _get(reverse("public:home"))
    assert b'rel="canonical"' in content


def test_how_it_works_has_canonical_link() -> None:
    """The how-it-works page emits a <link rel="canonical"> element."""
    content = _get(reverse("public:how_it_works"))
    assert b'rel="canonical"' in content


def test_faq_has_canonical_link() -> None:
    """The FAQ page emits a <link rel="canonical"> element."""
    content = _get(reverse("public:faq"))
    assert b'rel="canonical"' in content


def test_register_has_canonical_link() -> None:
    """The register page emits a <link rel="canonical"> element."""
    content = _get(reverse("public:register_role"))
    assert b'rel="canonical"' in content


def test_legal_privacy_has_canonical_link() -> None:
    """The privacy-policy legal page emits a <link rel="canonical"> element."""
    content = _get(reverse("public:legal", kwargs={"page": "privacy"}))
    assert b'rel="canonical"' in content


def test_register_form_canonical_link_is_role_specific() -> None:
    """Each role's hardwired registration form declares its own canonical URL.

    VERB-131 replaced the single ``?role=`` query-branched page (VERB-128's
    dedup concern) with a distinct URL per role — ``/register/ambassador/``
    and ``/register/referee/`` are genuinely different pages, so each
    correctly self-declares its own canonical rather than sharing one.
    """
    for role in ("ambassador", "referee"):
        role_url = reverse("public:register_form", kwargs={"role": role})
        content = _get(role_url).decode()
        match = re.search(r'rel="canonical"\s+href="([^"]+)"', content, re.DOTALL)
        assert match is not None, role
        href = match.group(1)
        assert href.endswith(role_url), (role, href)


# ---------------------------------------------------------------------------
# Twitter card
# ---------------------------------------------------------------------------


def test_home_has_twitter_card() -> None:
    """The home page emits a twitter:card meta tag."""
    content = _get(reverse("public:home"))
    assert b'name="twitter:card"' in content


def test_how_it_works_has_twitter_card() -> None:
    """The how-it-works page emits a twitter:card meta tag."""
    content = _get(reverse("public:how_it_works"))
    assert b'name="twitter:card"' in content


# ---------------------------------------------------------------------------
# og:image — share image uses the dedicated Open Graph card (VERB-148)
# ---------------------------------------------------------------------------


def test_home_og_image_references_card() -> None:
    """The home page og:image points to the dedicated OG share card."""
    content = _get(reverse("public:home"))
    assert b"images/og-card.jpg" in content
    assert b'property="og:image"' in content


def test_how_it_works_og_image_references_card() -> None:
    """The how-it-works page og:image points to the dedicated OG share card."""
    content = _get(reverse("public:how_it_works"))
    assert b"images/og-card.jpg" in content
    assert b'property="og:image"' in content


def test_home_og_image_declares_dimensions_and_alt() -> None:
    """The OG card declares its type, dimensions and alt text.

    Facebook needs the width/height up front to lay the card out on a first
    share (without them it often renders blank); the alt text is what screen
    readers announce.
    """
    content = _get(reverse("public:home"))
    assert b'property="og:image:type"' in content
    assert b'property="og:image:width"' in content
    assert b'content="1200"' in content
    assert b'property="og:image:height"' in content
    assert b'content="630"' in content
    assert b'property="og:image:secure_url"' in content
    assert b'property="og:image:alt"' in content
