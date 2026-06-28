# End-to-end smoke tests for the most important user journeys.
#
# These tests drive real GET/POST endpoints by URL — no factories, no direct
# model instantiation. They exercise the full stack (URL routing, views, forms,
# services, and templates) for each journey, giving confidence that the pieces
# connect correctly end-to-end.
#
# Three journeys are covered:
#   1. Homepage renders with both role CTAs.
#   2. Ambassador registration flow: form → email confirmation → account page.
#   3. Referee registration creates a match and the match page is accessible.
#
# The ``_register_and_confirm`` helper drives the combined-form flow used by
# both the ambassador and referee journeys. Each registrant uses its own
# ``Client`` instance so sessions do not bleed between the two parties.

import pytest
from allauth.account.models import EmailAddress
from django.test import Client, TestCase, override_settings
from django.urls import reverse

from accounts.tokens import make_match_access_token
from matching.models import Match, Registration

pytestmark = pytest.mark.django_db


# ---------------------------------------------------------------------------
# Harness helpers
# ---------------------------------------------------------------------------


def _ambassador_payload(email: str) -> dict[str, object]:
    """Return a minimal valid ambassador POST payload for the given email.

    Values mirror the ``_valid_ambassador_post`` helper in
    ``tests/public/test_views.py``, parameterised by email.
    """
    return {
        "role": "ambassador",
        "first_name": "Ada",
        "last_name": "Lovelace",
        "email": email,
        "prior_pass": Registration.PriorPass.SEASONAL,
        "prior_pass_attestation": True,
        "terms_accepted": True,
    }


def _referee_payload(email: str) -> dict[str, object]:
    """Return a minimal valid referee POST payload for the given email.

    Values mirror the ``_valid_referee_post`` helper in
    ``tests/public/test_views.py``, parameterised by email. Referees do not
    supply ``prior_pass`` (their eligibility criterion is that they hold none).
    """
    return {
        "role": "referee",
        "first_name": "Grace",
        "last_name": "Hopper",
        "email": email,
        "prior_pass_attestation": True,
        "terms_accepted": True,
    }


def _register_and_confirm(client: Client, payload: dict[str, object]) -> Registration:
    """POST the registration form, then consume the confirm link.

    Drives the two-step registration flow end-to-end:
      1. POST ``payload`` to the registration endpoint (no follow). Asserts
         a 302 redirect to the email-sent page.
      2. Reads the confirm URL stashed by the view in
         ``client.session["debug_verify_url"]`` (only available under
         ``DEBUG=True`` — guard with ``@override_settings(DEBUG=True)`` at
         the call site).
      3. GETs the confirm URL inside ``TestCase.captureOnCommitCallbacks``
         so the ``transaction.on_commit`` matching trigger fires.

    Returns the ``Registration`` freshly read from the database.
    """
    response = client.post(reverse("public:register"), payload)
    assert response.status_code == 302, (
        f"Expected 302 from registration POST, got {response.status_code}"
    )
    assert response.url == reverse("public:register_email_sent"), (
        f"Expected redirect to register_email_sent, got {response.url!r}"
    )

    confirm_url: str = client.session["debug_verify_url"]

    with TestCase.captureOnCommitCallbacks(execute=True):
        client.get(confirm_url)

    email: str = str(payload["email"])
    return Registration.objects.select_related("user").get(user__email=email)


def _match_url(match: Match, registration: Registration) -> str:
    """Return the signed match-access URL for the given party on the match.

    Mirrors ``_make_match_url`` in ``tests/public/test_views.py``.
    """
    token = make_match_access_token(match.pk, registration.pk)
    return reverse("public:match", args=[token])


# ---------------------------------------------------------------------------
# Journey tests
# ---------------------------------------------------------------------------


def test_homepage_renders() -> None:
    """The homepage returns 200 and shows both role call-to-action labels."""
    response = Client().get(reverse("public:home"))
    assert response.status_code == 200
    assert b"I'm an Ambassador" in response.content
    assert b"I'm a Referee" in response.content


@override_settings(DEBUG=True)
def test_ambassador_registration_journey() -> None:
    """An ambassador can register, confirm their email, and see a verified account.

    Journey:
      - GET the registration form with the ambassador role hint → 200.
      - Confirm the email is initially unverified.
      - Submit and confirm the registration via the signed link.
      - GET the account detail page on the now-logged-in client → 200,
        "Email verified" aria-label present, "Unverified" absent.
    """
    email = "ada@example.com"
    client = Client()

    # GET the form — must render without login.
    response = client.get(reverse("public:register") + "?role=ambassador")
    assert response.status_code == 200

    # Email must not be marked verified before registration.
    assert not EmailAddress.objects.filter(email=email, verified=True).exists()

    _register_and_confirm(client, _ambassador_payload(email))

    # Account detail page — client is now logged in after confirmation.
    account_response = client.get(reverse("accounts:detail"))
    assert account_response.status_code == 200
    content = account_response.content
    assert b"Email verified" in content
    assert b"Unverified" not in content


@override_settings(DEBUG=True)
def test_referee_registration_creates_match() -> None:
    """Registering a referee when an ambassador waits creates a match.

    Journey:
      - Register and confirm an ambassador on its own client (so a VERIFIED
        counterpart exists in the pool).
      - Register and confirm a referee on a second client; the matching engine
        fires inside the on_commit callback wrapper.
      - Assert exactly one match exists.
      - Build the referee's match URL and GET it → 200, correct template,
        Accept and Decline buttons present.
    """
    ambassador_email = "ada@example.com"
    referee_email = "grace@example.com"

    # Register the ambassador first so they are VERIFIED when the referee arrives.
    ambassador_client = Client()
    _register_and_confirm(ambassador_client, _ambassador_payload(ambassador_email))

    # Register the referee; the matching engine proposes a match on commit.
    referee_client = Client()
    referee_reg = _register_and_confirm(referee_client, _referee_payload(referee_email))

    assert Match.objects.count() == 1

    match = Match.objects.select_related(
        "ambassador_registration", "referee_registration"
    ).get()

    # GET the match page using the referee's signed URL.
    url = _match_url(match, referee_reg)
    match_response = Client().get(url)
    assert match_response.status_code == 200
    assert "public/match.html" in [t.name for t in match_response.templates]
    assert b"Accept" in match_response.content
    assert b"Decline" in match_response.content
