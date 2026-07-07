# End-to-end smoke tests for the full participant lifecycle (VERB-91).
#
# These tests drive real GET/POST endpoints by URL — no factories, no direct
# model instantiation. They exercise the full stack (URL routing, views, forms,
# services, templates, the matching engine, and the expiry command), giving
# confidence that the pieces connect correctly end-to-end. They run under the
# ordinary ``tox -e test`` env (pure pytest, no browser), so they are part of CI.
#
# Journeys covered:
#   1. Homepage renders with both role CTAs.
#   2. Ambassador registration flow: form → email confirmation → account page.
#   3. Referee registration creates a proposed match; the match page is accessible.
#   4. Happy path: both accept → ACCEPTED, contact PII revealed; the first
#      accept nudges the waiting partner (VERB-92).
#   5. One party declines → decliner PAUSED, partner requeued and notified.
#   6. Contact window lapses (non-response) → non-responder PAUSED, faithful
#      party requeued; both notified (VERB-92).
#   7. Post-accept no-show → accused SUSPENDED, reporter requeued; both notified.
#   8. A paused party rejoins the queue and is re-matched.
#   9. A paused party deletes their account.
#  10. A verified (unmatched) party deletes their account.
#
# The ``_register_and_confirm`` helper drives the combined-form flow; the
# ``_proposed_pair`` helper builds a confirmed, matched pair; and
# ``_post_match_action`` posts a token-scoped accept/decline/no-show as an HTMX
# request (firing on_commit callbacks so queued emails land in ``mail.outbox``).
# Each party uses its own ``Client`` so sessions do not bleed between them.
#
# Email-verified state is derived from Registration.status (VERB-46 — allauth
# EmailAddress model has been removed).

from datetime import timedelta

import pytest
from django.contrib.auth import get_user_model
from django.core import mail
from django.core.management import call_command
from django.http import HttpResponse
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from accounts.tokens import make_match_access_token
from matching.models import Match, Registration

pytestmark = pytest.mark.django_db

User = get_user_model()


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
        "phone": "+41791112233",
        "prior_pass": Registration.PriorPass.SEASONAL,
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
        "phone": "+41794445566",
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

    Email-verified state is now derived from Registration.status (VERB-46 —
    allauth EmailAddress removed).
    """
    email = "ada@example.com"
    client = Client()

    # GET the form — must render without login.
    response = client.get(reverse("public:register") + "?role=ambassador")
    assert response.status_code == 200

    # Before registration, no Registration exists; email is not verified.
    assert not Registration.objects.filter(user__email=email).exists()

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


# ---------------------------------------------------------------------------
# Lifecycle harness helpers (VERB-91)
# ---------------------------------------------------------------------------

# Contact phone numbers set on the payload helpers, asserted when contact PII
# is (or is not) revealed. Kept in sync with ``_ambassador_payload`` /
# ``_referee_payload`` above.
_AMBASSADOR_PHONE = "+41791112233"
_REFEREE_PHONE = "+41794445566"


def _post_match_action(url_name: str, token: str) -> HttpResponse:
    """POST a token-scoped match action as an HTMX request.

    Match actions (accept, decline, report-no-show) are guarded by
    ``require_htmx`` (Invariant 7) and ``require_POST``, and are reachable
    anonymously via the signed per-party access ``token``. Each call uses a
    fresh ``Client`` so sessions do not bleed between parties.

    The POST runs inside ``captureOnCommitCallbacks(execute=True)`` so any
    notification email queued via ``transaction.on_commit`` (the mutual-accept
    confirmation, the no-show notice) is actually sent and observable in
    ``mail.outbox``.
    """
    url = reverse(url_name, args=[token])
    with TestCase.captureOnCommitCallbacks(execute=True):
        response = Client().post(url, headers={"hx-request": "true"})
    return response


def _proposed_pair(
    ambassador_email: str = "ada@example.com",
    referee_email: str = "grace@example.com",
) -> tuple[Client, Registration, Client, Registration, Match]:
    """Register and confirm an eligible pair and return both clients and the match.

    Registers the ambassador first (so a VERIFIED counterpart is waiting), then
    the referee, whose confirmation fires the synchronous matching engine and
    proposes a single match. Both clients are logged in as their own party (the
    confirm link authenticates them), so they can drive their account pages.

    Call sites must run under ``@override_settings(DEBUG=True)`` because
    ``_register_and_confirm`` reads the confirm URL from the debug-only session
    key.
    """
    ambassador_client = Client()
    ambassador_reg = _register_and_confirm(
        ambassador_client, _ambassador_payload(ambassador_email)
    )
    referee_client = Client()
    referee_reg = _register_and_confirm(referee_client, _referee_payload(referee_email))
    match = Match.objects.select_related(
        "ambassador_registration", "referee_registration"
    ).get()
    assert match.status == Match.Status.PROPOSED
    return ambassador_client, ambassador_reg, referee_client, referee_reg, match


# ---------------------------------------------------------------------------
# Journey 4 — happy path: both accept, contact revealed
# ---------------------------------------------------------------------------


@override_settings(DEBUG=True)
def test_match_happy_path_both_accept_reveals_contact() -> None:
    """Both parties accept → match ACCEPTED and contact details are revealed.

    Covers VERB-91 journey 2 (happy path):
      - A proposed pair is created end-to-end.
      - Before any accept, neither party's contact PII is visible on the match
        page (Invariant 1).
      - The ambassador accepts (PROPOSED → PENDING); still no PII.
      - The referee accepts (PENDING → ACCEPTED); the accepting response shows
        the counterpart's email and phone, and both parties receive a
        confirmation email carrying the other's contact details.
    """
    ambassador_email = "ada@example.com"
    referee_email = "grace@example.com"
    _amb_client, amb_reg, _ref_client, ref_reg, match = _proposed_pair(
        ambassador_email, referee_email
    )

    amb_token = make_match_access_token(match.pk, amb_reg.pk)
    ref_token = make_match_access_token(match.pk, ref_reg.pk)

    # Invariant 1: before acceptance, contact PII is hidden on the match page.
    proposed_page = Client().get(reverse("public:match", args=[ref_token]))
    assert proposed_page.status_code == 200
    assert ambassador_email.encode() not in proposed_page.content
    assert _AMBASSADOR_PHONE.encode() not in proposed_page.content

    # Ambassador accepts first: PROPOSED → PENDING, still no PII revealed.
    mail.outbox.clear()
    first = _post_match_action("public:match_accept", amb_token)
    assert first.status_code == 200
    match.refresh_from_db()
    assert match.status == Match.Status.PENDING
    assert referee_email.encode() not in first.content

    # VERB-92: the waiting referee is nudged that the ambassador accepted; the
    # nudge carries no counterpart contact PII (only revealed on mutual accept).
    assert len(mail.outbox) == 1
    assert mail.outbox[0].to == [referee_email]
    assert _AMBASSADOR_PHONE not in mail.outbox[0].body

    # Referee accepts second: PENDING → ACCEPTED, PII revealed.
    mail.outbox.clear()
    second = _post_match_action("public:match_accept", ref_token)
    assert second.status_code == 200
    match.refresh_from_db()
    assert match.status == Match.Status.ACCEPTED

    # The referee now sees the ambassador's contact details in the response.
    assert ambassador_email.encode() in second.content
    assert _AMBASSADOR_PHONE.encode() in second.content

    # Both parties are emailed with the counterpart's contact details.
    assert len(mail.outbox) == 2
    recipients = {addr for message in mail.outbox for addr in message.to}
    assert recipients == {ambassador_email, referee_email}
    bodies = "\n".join(message.body for message in mail.outbox)
    assert ambassador_email in bodies
    assert referee_email in bodies
    assert _AMBASSADOR_PHONE in bodies
    assert _REFEREE_PHONE in bodies


# ---------------------------------------------------------------------------
# Journey 5 — one party declines
# ---------------------------------------------------------------------------


@override_settings(DEBUG=True)
def test_match_decline_pauses_decliner_and_requeues_partner() -> None:
    """One party declines → decliner PAUSED, partner requeued and notified.

    Covers VERB-91 journey 3 (one party declines):
      - The ambassador declines a proposed match.
      - The match becomes DECLINED and the decliner's registration is PAUSED.
      - The partner (left hanging) stays VERIFIED and is requeued to the front
        (priority += 1).
      - VERB-92: the requeued partner is notified (the decliner is not); the
        notice carries no counterpart contact PII.
      - The paused decliner's account page offers both a rejoin and a delete
        control, so they can choose to requeue or delete (journeys 6 and 9).
    """
    amb_client, amb_reg, _ref_client, ref_reg, match = _proposed_pair()
    amb_token = make_match_access_token(match.pk, amb_reg.pk)

    mail.outbox.clear()
    declined = _post_match_action("public:match_decline", amb_token)
    assert declined.status_code == 200

    match.refresh_from_db()
    assert match.status == Match.Status.DECLINED

    amb_reg.refresh_from_db()
    assert amb_reg.status == Registration.Status.PAUSED

    ref_reg.refresh_from_db()
    assert ref_reg.status == Registration.Status.VERIFIED
    assert ref_reg.priority == 1  # requeued to the front

    # VERB-92: only the requeued partner is notified; the decliner is not, and
    # the notice discloses no counterpart contact PII.
    assert len(mail.outbox) == 1
    assert mail.outbox[0].to == [ref_reg.user.email]
    assert _AMBASSADOR_PHONE not in mail.outbox[0].body

    # The paused decliner can requeue or delete from their account page.
    detail = amb_client.get(reverse("accounts:detail"))
    assert detail.status_code == 200
    assert reverse("accounts:rejoin_queue").encode() in detail.content
    assert reverse("accounts:delete").encode() in detail.content


# ---------------------------------------------------------------------------
# Journey 6 — contact window lapses (non-response)
# ---------------------------------------------------------------------------


@override_settings(DEBUG=True)
def test_match_expiry_pauses_non_responder_and_requeues_faithful_party() -> None:
    """Window lapses → non-responder PAUSED, faithful party requeued; both notified.

    Covers VERB-91 journey 4 (one party does not respond before the window
    closes):
      - The ambassador accepts; the referee never responds.
      - The contact window is forced closed and the ``expire_matches`` command
        sweeps the match to EXPIRED.
      - The non-responding referee is PAUSED (removed from the pool, may
        self-rejoin) and receives the window-expired notification.
      - The ambassador (kept faith) stays VERIFIED and is requeued to the front.
      - VERB-92: both parties are notified (previously only the non-responder).
    """
    ambassador_email = "ada@example.com"
    referee_email = "grace@example.com"
    _amb_client, amb_reg, _ref_client, ref_reg, match = _proposed_pair(
        ambassador_email, referee_email
    )
    amb_token = make_match_access_token(match.pk, amb_reg.pk)

    # Ambassador accepts; the referee stays silent.
    _post_match_action("public:match_accept", amb_token)
    match.refresh_from_db()
    assert match.status == Match.Status.PENDING

    # Force the contact window closed and run the scheduled expiry sweep.
    match.expires_at = timezone.now() - timedelta(hours=1)
    match.save(update_fields=["expires_at", "updated_at"])

    mail.outbox.clear()
    with TestCase.captureOnCommitCallbacks(execute=True):
        call_command("expire_matches")

    match.refresh_from_db()
    assert match.status == Match.Status.EXPIRED

    amb_reg.refresh_from_db()
    assert amb_reg.status == Registration.Status.VERIFIED
    assert amb_reg.priority == 1  # kept faith → requeued to the front

    ref_reg.refresh_from_db()
    assert ref_reg.status == Registration.Status.PAUSED

    # VERB-92: both the faithful ambassador (re-queued) and the paused referee
    # (window-expired) are notified.
    assert len(mail.outbox) == 2
    recipients = {addr for message in mail.outbox for addr in message.to}
    assert recipients == {ambassador_email, referee_email}


# ---------------------------------------------------------------------------
# Journey 7 — post-accept no-show
# ---------------------------------------------------------------------------


@override_settings(DEBUG=True)
def test_post_accept_no_show_suspends_accused_and_requeues_reporter() -> None:
    """A post-accept no-show report suspends the accused; both parties notified.

    Covers the post-accept no-show path — the ``SUSPENDED`` outcome, distinct
    from the recoverable ``PAUSED`` outcomes above:
      - Both parties accept (ACCEPTED).
      - The ambassador reports the referee as a no-show.
      - The match becomes CANCELLED; the accused referee is SUSPENDED (removed
        from the pool, not self-recoverable) and is notified.
      - The reporting ambassador stays VERIFIED and is requeued to the front.
      - VERB-92: both are notified — the accused of the report, the reporter of
        the re-queue (previously only the accused).
    """
    ambassador_email = "ada@example.com"
    referee_email = "grace@example.com"
    _amb_client, amb_reg, _ref_client, ref_reg, match = _proposed_pair(
        ambassador_email, referee_email
    )
    amb_token = make_match_access_token(match.pk, amb_reg.pk)
    ref_token = make_match_access_token(match.pk, ref_reg.pk)

    # Drive the match to ACCEPTED.
    _post_match_action("public:match_accept", amb_token)
    _post_match_action("public:match_accept", ref_token)
    match.refresh_from_db()
    assert match.status == Match.Status.ACCEPTED

    # The ambassador reports the referee as a no-show.
    mail.outbox.clear()
    cancelled = _post_match_action("public:match_report_no_show", amb_token)
    assert cancelled.status_code == 200

    match.refresh_from_db()
    assert match.status == Match.Status.CANCELLED
    assert match.no_show_reported_by == Match.Side.AMBASSADOR

    ref_reg.refresh_from_db()
    assert ref_reg.status == Registration.Status.SUSPENDED

    amb_reg.refresh_from_db()
    assert amb_reg.status == Registration.Status.VERIFIED
    assert amb_reg.priority == 1  # reporter requeued to the front

    # VERB-92: both parties are notified — the accused (referee) and the
    # re-queued reporter (ambassador).
    assert len(mail.outbox) == 2
    recipients = {addr for message in mail.outbox for addr in message.to}
    assert recipients == {ambassador_email, referee_email}


# ---------------------------------------------------------------------------
# Journey 8 — paused party rejoins the queue
# ---------------------------------------------------------------------------


@override_settings(DEBUG=True)
def test_paused_party_rejoins_queue() -> None:
    """A paused party rejoins from their account page and re-enters the pool.

    Covers VERB-91 journey 5:
      - The ambassador declines (→ PAUSED) while the referee is requeued and
        still waiting.
      - The ambassador rejoins via ``accounts:rejoin_queue``; their
        registration returns to VERIFIED and the matching engine re-pairs them
        with the waiting referee, producing a fresh active match.
    """
    amb_client, amb_reg, _ref_client, _ref_reg, match = _proposed_pair()
    amb_token = make_match_access_token(match.pk, amb_reg.pk)

    _post_match_action("public:match_decline", amb_token)
    amb_reg.refresh_from_db()
    assert amb_reg.status == Registration.Status.PAUSED

    # Rejoin from the account page; re-matching fires on commit.
    with TestCase.captureOnCommitCallbacks(execute=True):
        response = amb_client.post(reverse("accounts:rejoin_queue"))
    assert response.status_code == 302

    amb_reg.refresh_from_db()
    assert amb_reg.status == Registration.Status.VERIFIED

    # Back in the pool → re-paired with the still-waiting referee.
    assert Match.objects.active().count() == 1


# ---------------------------------------------------------------------------
# Journey 9 — paused party deletes their account
# ---------------------------------------------------------------------------


@override_settings(DEBUG=True)
def test_paused_party_deletes_account() -> None:
    """A paused party deletes their account and is fully removed.

    Covers VERB-91 journey 6:
      - The ambassador declines (→ PAUSED).
      - They delete their account; the User and Registration rows are removed.
    """
    amb_client, amb_reg, _ref_client, _ref_reg, match = _proposed_pair()
    amb_token = make_match_access_token(match.pk, amb_reg.pk)

    _post_match_action("public:match_decline", amb_token)
    amb_reg.refresh_from_db()
    assert amb_reg.status == Registration.Status.PAUSED

    user_pk = amb_reg.user_id
    response = amb_client.post(reverse("accounts:delete"))
    assert response.status_code == 302

    assert not User.objects.filter(pk=user_pk).exists()
    assert not Registration.objects.filter(pk=amb_reg.pk).exists()


# ---------------------------------------------------------------------------
# Journey 10 — verified (unmatched) party deletes their account
# ---------------------------------------------------------------------------


@override_settings(DEBUG=True)
def test_verified_party_deletes_account() -> None:
    """A verified party with no match deletes their account.

    Covers VERB-91 journey 7:
      - A lone ambassador registers and confirms (VERIFIED, no counterpart).
      - They delete their account; the User and Registration rows are removed.
    """
    client = Client()
    registration = _register_and_confirm(
        client, _ambassador_payload("solo@example.com")
    )
    assert registration.status == Registration.Status.VERIFIED

    user_pk = registration.user_id
    response = client.post(reverse("accounts:delete"))
    assert response.status_code == 302

    assert not User.objects.filter(pk=user_pk).exists()
    assert not Registration.objects.filter(pk=registration.pk).exists()
