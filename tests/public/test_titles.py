# Tests for the composed <title> tag (VERB-126).
#
# base.html nests {% block page_title %} inside {% block title %}, so
# interior pages that override only page_title inherit " &middot; Ski
# Parrainage"; Home overrides the whole title block and keeps its own
# brand-first + season string. The test env compiles no .mo catalogues, so we
# assert on the rendered English source markup only — never translated copy
# (see project memory test-env-no-compiled-catalogues).

import re

import pytest
from django.test import Client
from django.urls import reverse

pytestmark = pytest.mark.django_db


def _title(url: str) -> str:
    """Return the whitespace-normalised <title>...</title> markup for *url*.

    djangofmt may wrap the <title> tag's contents across lines, so
    whitespace is collapsed before asserting on it.
    """
    response = Client().get(url)
    assert response.status_code == 200
    content = response.content.decode()
    match = re.search(r"<title>(.*?)</title>", content, re.S)
    assert match is not None
    return re.sub(r"\s+", " ", match.group(1)).strip()


def test_interior_page_title_composes_with_brand_suffix() -> None:
    """An interior page's page_title fragment is suffixed with the brand."""
    title = _title(reverse("public:faq"))
    assert title == "FAQ &middot; Ski Parrainage"


def test_home_title_overrides_composed_default() -> None:
    """Home's full title block override wins over the composed default."""
    title = _title(reverse("public:home"))
    assert title == "Ski Parrainage &middot; 2026/27"
