# Tests for the Content-Security-Policy configuration (SKI-170, ADR 0027).
#
# The policy is served by django-csp-plus, which builds the header from the
# static baseline in config.settings.base.CSP_DEFAULTS plus any enabled
# csp.CspRule rows. These tests cover the baseline that ships, the runtime
# layer that makes it editable, and the report endpoint that feeds it.
#
# CSP_ENABLED / CSP_REPORT_ONLY are read by the library at import time, so
# @override_settings cannot move them — every test here asserts against the
# configured test environment, which is report-only.

import json
from collections.abc import Iterator

import pytest
from csp.models import CspReport, CspRule
from csp.policy import clear_cache
from django.contrib.auth.models import User
from django.http import HttpResponse
from django.test import Client
from django.urls import reverse

from tests.accounts.factories import UserFactory

# Building the policy reads the CspRule table on every response, so even the
# tests that create no rows need database access.
pytestmark = pytest.mark.django_db

_HEADER = "Content-Security-Policy-Report-Only"


@pytest.fixture(autouse=True)
def _clear_policy_cache() -> Iterator[None]:
    """Drop the cached policy around every test in this module.

    The built policy is cached in LocMemCache for CSP_CACHE_TIMEOUT, which
    outlives a single test — without this a rule created by one test would
    leak into the next, and a stale entry would mask a rule this test just
    created.
    """
    clear_cache()
    yield
    clear_cache()


def _directives(response: HttpResponse) -> dict[str, set[str]]:
    """Return the response's CSP as a {directive: {values}} mapping.

    Values are a set, not a string: the library dedupes each directive through
    a set, so the order within a directive is not stable between builds. Order
    carries no meaning in CSP, so comparing sets asserts what matters.
    """
    header = response.headers.get(_HEADER, "")
    return {
        part.split(" ", 1)[0]: set(part.split(" ", 1)[1].split(" "))
        for part in header.split("; ")
        if " " in part
    }


def test_baseline_policy_is_served_on_a_public_page() -> None:
    """Every directive in the baseline reaches the browser."""
    directives = _directives(Client().get(reverse("public:home")))

    assert directives["default-src"] == {"'self'"}
    assert directives["style-src"] == {"'self'", "https://fonts.googleapis.com"}
    assert directives["font-src"] == {"https://fonts.gstatic.com"}
    assert directives["connect-src"] == {"'self'"}
    assert directives["base-uri"] == {"'self'"}
    assert directives["frame-ancestors"] == {"'none'"}
    assert directives["img-src"] == {"'self'", "data:"}


def test_form_action_allows_stripe_checkout() -> None:
    """form-action carries checkout.stripe.com as well as 'self' (SKI-170).

    Both money flows POST to a local view that 302s to hosted Checkout, and
    Chrome and Safari re-check form-action against the redirect target. With
    'self' alone the payment hop is blocked — and the browser names the local
    action URL in the violation, so the report points at the wrong place.
    """
    directives = _directives(Client().get(reverse("public:home")))

    assert "'self'" in directives["form-action"]
    assert "https://checkout.stripe.com" in directives["form-action"]


def test_script_src_nonce_matches_the_nonce_used_in_the_page() -> None:
    """The header's nonce is the one the inline script actually carries.

    The nonce is substituted per response; a mismatch between header and
    markup would block base.html's font-swap script under enforcement while
    looking correct in either place on its own.
    """
    response = Client().get(reverse("public:home"))

    values = _directives(response)["script-src"]
    assert "'self'" in values
    nonce_value = next(v for v in values if v.startswith("'nonce-"))
    nonce = nonce_value.removeprefix("'nonce-").removesuffix("'")
    assert f'nonce="{nonce}"'.encode() in response.content


def test_report_uri_points_at_the_unprefixed_endpoint() -> None:
    """The report URL is language-neutral, like the other machine routes."""
    directives = _directives(Client().get(reverse("public:home")))

    assert directives["report-uri"] == {reverse("csp:report_uri")}
    assert directives["report-uri"] == {"/csp/report-uri/"}


def test_enabled_rule_extends_the_baseline() -> None:
    """A CspRule adds to a directive rather than replacing it.

    This is the point of the swap: unblocking an origin is an admin edit.
    """
    CspRule.objects.create(
        directive="img-src", value="https://cdn.example.test", enabled=True
    )
    clear_cache()

    directives = _directives(Client().get(reverse("public:home")))

    assert "https://cdn.example.test" in directives["img-src"]
    assert "'self'" in directives["img-src"]


def test_disabled_rule_is_not_applied() -> None:
    """A rule that is not enabled has no effect on the served policy."""
    CspRule.objects.create(
        directive="img-src", value="https://cdn.example.test", enabled=False
    )
    clear_cache()

    directives = _directives(Client().get(reverse("public:home")))

    assert "https://cdn.example.test" not in directives["img-src"]


def test_rule_can_add_a_directive_absent_from_the_baseline() -> None:
    """A directive the baseline never sets can be introduced at runtime."""
    CspRule.objects.create(
        directive="frame-src", value="https://js.stripe.com", enabled=True
    )
    clear_cache()

    directives = _directives(Client().get(reverse("public:home")))

    assert directives["frame-src"] == {"https://js.stripe.com"}


def test_report_endpoint_stores_a_violation() -> None:
    """A posted violation report is recorded, so the admin has something to act on."""
    payload = {
        "csp-report": {
            "blocked-uri": "https://checkout.stripe.com/c/pay/cs_test",
            "effective-directive": "form-action",
            "document-uri": "https://example.test/tip/",
            "disposition": "enforce",
            "status-code": "200",
        }
    }

    response = Client().post(
        reverse("csp:report_uri"),
        data=json.dumps(payload),
        content_type="application/csp-report",
    )

    assert response.status_code in (200, 201, 204)
    report = CspReport.objects.get()
    assert report.effective_directive == "form-action"
    assert report.blocked_uri == "https://checkout.stripe.com/c/pay/cs_test"


def test_report_endpoint_is_not_language_prefixed() -> None:
    """The endpoint exists once, unprefixed — /fr/csp/ is not a route.

    The report URL is baked into the header by reverse(), so a prefixed
    variant would make the reporting path depend on the language of the page
    that generated the violation.
    """
    assert Client().get("/fr/csp/report-uri/").status_code == 404


def test_error_page_carries_no_inline_style_block() -> None:
    """500.html links its critical CSS instead of inlining it (SKI-170).

    django-csp-plus lowercases directive values, which a base64 hash does not
    survive, so the hash that used to whitelist the inline block cannot work.
    The styles moved to a file rather than the policy gaining 'unsafe-inline';
    this test is what stops them moving back.
    """
    from django.template.loader import render_to_string

    # Rendered, not the source: the template explains the rule in a
    # {% comment %} that names the tag it is banning.
    html = render_to_string("500.html")

    assert "<style>" not in html
    assert '<link rel="stylesheet" href="/static/css/500.css">' in html


# ---------------------------------------------------------------------------
# Admin — the reason for the swap (SKI-170)
# ---------------------------------------------------------------------------


def _staff_user() -> User:
    """Create and return a superuser for admin access in tests."""
    user = UserFactory.create(
        username="csp_staff_admin", is_staff=True, is_superuser=True
    )
    user.set_password("password")
    user.save()
    return user


def test_rule_changelist_returns_200(client: Client) -> None:
    """The CspRule changelist loads — this is where a blocked origin is unblocked."""
    CspRule.objects.create(directive="img-src", value="https://cdn.example.test")
    client.force_login(_staff_user())

    response = client.get(reverse("admin:csp_csprule_changelist"))

    assert response.status_code == 200


def test_rule_change_view_returns_200(client: Client) -> None:
    """The CspRule change form loads for an existing rule."""
    rule = CspRule.objects.create(directive="img-src", value="https://cdn.example.test")
    client.force_login(_staff_user())

    response = client.get(reverse("admin:csp_csprule_change", args=[rule.pk]))

    assert response.status_code == 200


def test_report_changelist_returns_200(client: Client) -> None:
    """The CspReport changelist loads — this is where violations are triaged."""
    CspReport.objects.create(
        effective_directive="form-action", blocked_uri="https://checkout.stripe.com/"
    )
    client.force_login(_staff_user())

    response = client.get(reverse("admin:csp_cspreport_changelist"))

    assert response.status_code == 200
