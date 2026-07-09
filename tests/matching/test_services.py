# Tests for the matching service functions.

from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import pytest
from django.conf import settings
from django.contrib.auth.models import User
from django.core import mail
from django.db import IntegrityError, transaction
from django.test import TestCase, override_settings
from django.utils import timezone

from core.exceptions import StateTransitionError
from core.models import StateTransitionLog
from matching.models import Match, Registration
from matching.services import (
    accept_match,
    confirm_registration,
    decline_match,
    expire_lapsed_matches,
    is_eligible_pair,
    is_registration_open,
    pause_registration,
    propose_match,
    queue_position,
    queue_snapshot,
    record_acceptance,
    record_decline,
    register_participant,
    rejoin_queue,
    report_no_show,
    requeue_to_front,
    run_matching,
    suspend_for_no_show,
    total_accepted_matches,
    withdraw_acceptance,
)
from matching.side_effects import (
    _email_confirmation,
    _email_no_show,
    _email_partner_accepted,
    _email_proposal,
    _email_requeued,
    _email_window_expired,
)
from tests.accounts.factories import UserFactory
from tests.matching.factories import MatchFactory, RegistrationFactory

pytestmark = pytest.mark.django_db


# ---------------------------------------------------------------------------
# is_registration_open
# ---------------------------------------------------------------------------


def test_registration_open_within_window() -> None:
    """is_registration_open returns True when now is within the window."""
    # Dev defaults are always open.
    assert is_registration_open() is True


@override_settings(
    REGISTRATION_OPENS_AT="2020-01-01",
    REGISTRATION_CLOSES_AT="2020-12-31",
)
def test_registration_closed_outside_window() -> None:
    """is_registration_open returns False when today is outside the window."""
    assert is_registration_open() is False


@override_settings(
    REGISTRATION_OPENS_AT="not-a-date",
    REGISTRATION_CLOSES_AT="also-not-a-date",
)
def test_registration_closed_on_parse_error() -> None:
    """is_registration_open returns False when the date strings are invalid."""
    assert is_registration_open() is False


def test_registration_window_is_date_based_and_inclusive() -> None:
    """Bounds are dates and both ends are inclusive (open on the closing date)."""
    today = timezone.localdate()
    with override_settings(
        REGISTRATION_OPENS_AT=today.isoformat(),
        REGISTRATION_CLOSES_AT=today.isoformat(),
    ):
        assert is_registration_open() is True

    yesterday = (today - timedelta(days=1)).isoformat()
    with override_settings(
        REGISTRATION_OPENS_AT="2020-01-01",
        REGISTRATION_CLOSES_AT=yesterday,
    ):
        assert is_registration_open() is False


# ---------------------------------------------------------------------------
# is_eligible_pair
# ---------------------------------------------------------------------------


def test_eligible_pair_returns_true_for_valid_pair() -> None:
    """is_eligible_pair returns True for an ambassador + referee eligible pair."""
    ambassador = RegistrationFactory.create(
        role=Registration.Role.AMBASSADOR,
        prior_pass=Registration.PriorPass.SEASONAL,
        status=Registration.Status.VERIFIED,
    )
    referee = RegistrationFactory.create(
        referee=True,
        status=Registration.Status.VERIFIED,
    )
    assert is_eligible_pair(ambassador, referee) is True


def test_eligible_pair_rejects_wrong_roles() -> None:
    """is_eligible_pair rejects when roles are swapped."""
    ambassador = RegistrationFactory.create(referee=True)
    referee = RegistrationFactory.create(
        role=Registration.Role.AMBASSADOR,
        prior_pass=Registration.PriorPass.SEASONAL,
    )
    assert is_eligible_pair(ambassador, referee) is False


def test_eligible_pair_rejects_ambassador_with_none_prior_pass() -> None:
    """is_eligible_pair rejects an ambassador whose prior_pass is NONE."""
    ambassador = RegistrationFactory.create(
        role=Registration.Role.AMBASSADOR,
        prior_pass=Registration.PriorPass.NONE,
    )
    referee = RegistrationFactory.create(referee=True)
    assert is_eligible_pair(ambassador, referee) is False


def test_eligible_pair_rejects_referee_with_prior_pass() -> None:
    """is_eligible_pair rejects a referee who holds a prior pass."""
    ambassador = RegistrationFactory.create(
        role=Registration.Role.AMBASSADOR,
        prior_pass=Registration.PriorPass.SEASONAL,
    )
    referee = RegistrationFactory.create(
        role=Registration.Role.REFEREE,
        prior_pass=Registration.PriorPass.SEASONAL,
    )
    assert is_eligible_pair(ambassador, referee) is False


def test_eligible_pair_rejects_non_verified_ambassador() -> None:
    """is_eligible_pair rejects an ambassador who is not VERIFIED."""
    ambassador = RegistrationFactory.create(
        role=Registration.Role.AMBASSADOR,
        prior_pass=Registration.PriorPass.SEASONAL,
        status=Registration.Status.SUSPENDED,
    )
    referee = RegistrationFactory.create(referee=True)
    assert is_eligible_pair(ambassador, referee) is False


def test_eligible_pair_rejects_non_verified_referee() -> None:
    """is_eligible_pair rejects a referee who is not VERIFIED."""
    ambassador = RegistrationFactory.create(
        role=Registration.Role.AMBASSADOR,
        prior_pass=Registration.PriorPass.SEASONAL,
    )
    referee = RegistrationFactory.create(
        referee=True,
        status=Registration.Status.SUSPENDED,
    )
    assert is_eligible_pair(ambassador, referee) is False


# ---------------------------------------------------------------------------
# propose_match
# ---------------------------------------------------------------------------


def test_propose_match_creates_match_for_ambassador_with_verified_referee() -> None:
    """propose_match pairs an ambassador with the top verified eligible referee."""
    referee = RegistrationFactory.create(referee=True)
    ambassador = RegistrationFactory.create(
        role=Registration.Role.AMBASSADOR,
        prior_pass=Registration.PriorPass.SEASONAL,
    )
    with transaction.atomic():
        match = propose_match(ambassador)

    assert match is not None
    assert match.ambassador_registration == ambassador
    assert match.referee_registration == referee
    assert match.status == Match.Status.PROPOSED

    # VERB-44: registrations remain VERIFIED after a match is proposed.
    ambassador.refresh_from_db()
    referee.refresh_from_db()
    assert ambassador.status == Registration.Status.VERIFIED
    assert referee.status == Registration.Status.VERIFIED


def test_propose_match_creates_match_for_referee_with_verified_ambassador() -> None:
    """propose_match pairs a referee with the top verified eligible ambassador."""
    ambassador = RegistrationFactory.create(
        role=Registration.Role.AMBASSADOR,
        prior_pass=Registration.PriorPass.SEASONAL,
    )
    referee = RegistrationFactory.create(referee=True)
    with transaction.atomic():
        match = propose_match(referee)

    assert match is not None
    assert match.ambassador_registration == ambassador
    assert match.referee_registration == referee


def test_propose_match_returns_none_when_no_eligible_counterpart() -> None:
    """propose_match returns None when no eligible counterpart is waiting."""
    ambassador = RegistrationFactory.create(
        role=Registration.Role.AMBASSADOR,
        prior_pass=Registration.PriorPass.SEASONAL,
    )
    with transaction.atomic():
        result = propose_match(ambassador)
    assert result is None
    assert Match.objects.count() == 0


def test_propose_match_prefers_shared_location() -> None:
    """propose_match picks the referee sharing the ambassador's preferred_location."""
    verbier_referee = RegistrationFactory.create(
        referee=True,
        preferred_location="VERBIER",
        priority=0,
    )
    thyon_referee = RegistrationFactory.create(
        referee=True,
        preferred_location="THYON",
        priority=0,
    )
    ambassador = RegistrationFactory.create(
        role=Registration.Role.AMBASSADOR,
        prior_pass=Registration.PriorPass.SEASONAL,
        preferred_location="VERBIER",
    )
    with transaction.atomic():
        match = propose_match(ambassador)

    assert match is not None
    assert match.referee_registration == verbier_referee
    # The Thyon referee is still VERIFIED (not matched away).
    thyon_referee.refresh_from_db()
    assert thyon_referee.status == Registration.Status.VERIFIED


def test_propose_match_uses_priority_as_secondary_rank() -> None:
    """propose_match ranks by priority descending when location is equal."""
    low_priority = RegistrationFactory.create(
        referee=True,
        preferred_location="VERBIER",
        priority=0,
    )
    high_priority = RegistrationFactory.create(
        referee=True,
        preferred_location="VERBIER",
        priority=10,
    )
    ambassador = RegistrationFactory.create(
        role=Registration.Role.AMBASSADOR,
        prior_pass=Registration.PriorPass.SEASONAL,
        preferred_location="VERBIER",
    )
    with transaction.atomic():
        match = propose_match(ambassador)

    assert match is not None
    assert match.referee_registration == high_priority
    low_priority.refresh_from_db()
    assert low_priority.status == Registration.Status.VERIFIED


def test_propose_match_sets_expires_at() -> None:
    """propose_match sets expires_at to now + CONTACT_WINDOW_HOURS."""

    RegistrationFactory.create(referee=True)
    ambassador = RegistrationFactory.create(
        role=Registration.Role.AMBASSADOR,
        prior_pass=Registration.PriorPass.SEASONAL,
    )
    before = timezone.now()
    with transaction.atomic():
        match = propose_match(ambassador)

    assert match is not None
    # expires_at should be approximately CONTACT_WINDOW_HOURS ahead.
    expected_hours = settings.CONTACT_WINDOW_HOURS
    delta = match.expires_at - before
    assert expected_hours * 3600 <= delta.total_seconds() <= expected_hours * 3600 + 5


def test_propose_match_fifo_tiebreak_within_equal_priority() -> None:
    """propose_match picks the earlier-created registration when priority is equal.

    Two referees with equal priority at the same location: the one created first
    (lower created_at) must be matched.
    """
    base_time = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)
    earlier_referee = RegistrationFactory.create(
        referee=True,
        preferred_location="VERBIER",
        priority=0,
    )
    # Use queryset update() to bypass auto_now_add and set created_at directly.
    Registration.objects.filter(pk=earlier_referee.pk).update(created_at=base_time)

    later_referee = RegistrationFactory.create(
        referee=True,
        preferred_location="VERBIER",
        priority=0,
    )
    Registration.objects.filter(pk=later_referee.pk).update(
        created_at=base_time + timedelta(hours=1)
    )

    ambassador = RegistrationFactory.create(
        role=Registration.Role.AMBASSADOR,
        prior_pass=Registration.PriorPass.SEASONAL,
        preferred_location="VERBIER",
    )
    with transaction.atomic():
        match = propose_match(ambassador)

    assert match is not None
    assert match.referee_registration == earlier_referee
    later_referee.refresh_from_db()
    assert later_referee.status == Registration.Status.VERIFIED


def test_propose_match_single_counterpart_matched_only_once() -> None:
    """A verified counterpart can be matched by at most one registration.

    Two referees both attempt to match the same sole waiting ambassador.
    Exactly one match is created; the other referee remains VERIFIED (pool
    availability is controlled by _without_active_match, not status flipping).
    """
    RegistrationFactory.create(
        role=Registration.Role.AMBASSADOR,
        prior_pass=Registration.PriorPass.SEASONAL,
    )
    referee_one = RegistrationFactory.create(referee=True, priority=10)
    referee_two = RegistrationFactory.create(referee=True, priority=5)

    with transaction.atomic():
        match_one = propose_match(referee_one)
    # After the first match, the ambassador holds an active match; the second
    # call must find no eligible (unmatched) counterpart.
    with transaction.atomic():
        match_two = propose_match(referee_two)

    assert match_one is not None
    assert match_two is None
    assert Match.objects.count() == 1
    referee_two.refresh_from_db()
    assert referee_two.status == Registration.Status.VERIFIED


def test_propose_match_skips_ineligible_ambassador() -> None:
    """propose_match does not match an ambassador with prior_pass=NONE."""
    RegistrationFactory.create(
        referee=True,
    )
    ineligible_ambassador = RegistrationFactory.create(
        role=Registration.Role.AMBASSADOR,
        prior_pass=Registration.PriorPass.NONE,
    )
    with transaction.atomic():
        result = propose_match(ineligible_ambassador)
    assert result is None
    assert Match.objects.count() == 0


# ---------------------------------------------------------------------------
# _email_proposal (matching.side_effects) — the match_proposed handlers'
# shared render helper, one recipient per call.
# ---------------------------------------------------------------------------


def test_email_proposal_sends_one_email_per_call() -> None:
    """_email_proposal sends one email per recipient; both sides is two calls."""
    match = MatchFactory.create()
    assert match.ambassador_registration is not None
    assert match.referee_registration is not None
    _email_proposal(match.ambassador_registration, match)
    _email_proposal(match.referee_registration, match)
    assert len(mail.outbox) == 2


def test_email_proposal_contains_no_pii() -> None:
    """Notification emails must not contain any contact PII (Invariant 1).

    The email includes a signed /match/<token>/ link so the recipient can open
    the match page — the link is non-PII (carries only opaque PKs inside the
    signature) and is intentionally present.
    """
    ambassador_reg = RegistrationFactory.create(
        role=Registration.Role.AMBASSADOR,
        prior_pass=Registration.PriorPass.SEASONAL,
        phone="+41790001234",
    )
    referee_reg = RegistrationFactory.create(
        referee=True,
        phone="+41790005678",
    )
    match = MatchFactory.create(
        ambassador_registration=ambassador_reg,
        referee_registration=referee_reg,
    )
    _email_proposal(ambassador_reg, match)
    _email_proposal(referee_reg, match)

    for message in mail.outbox:
        html_body = next(
            content
            for content, mimetype in message.alternatives
            if mimetype == "text/html"
        )
        for body in (message.body, html_body):
            # No phone numbers.
            assert "+41790001234" not in body
            assert "+41790005678" not in body
            # No email addresses (only the recipient's own, which is the To: field).
            assert ambassador_reg.user.email not in body
            assert referee_reg.user.email not in body
            # No names.
            assert (
                ambassador_reg.user.first_name not in body
                or not ambassador_reg.user.first_name
            )
            assert (
                referee_reg.user.first_name not in body
                or not referee_reg.user.first_name
            )
            # The match link is present (non-PII — token is opaque).
            assert "/match/" in body


def test_email_proposal_includes_match_link() -> None:
    """Each notification email body contains the /match/ path."""
    match = MatchFactory.create()
    assert match.ambassador_registration is not None
    assert match.referee_registration is not None
    _email_proposal(match.ambassador_registration, match)
    _email_proposal(match.referee_registration, match)
    assert len(mail.outbox) == 2
    for message in mail.outbox:
        assert "/match/" in message.body


def test_email_proposal_attaches_html_alternative() -> None:
    """_email_proposal attaches a non-empty text/html alternative."""
    match = MatchFactory.create()
    assert match.ambassador_registration is not None
    _email_proposal(match.ambassador_registration, match)

    html_alternatives = [
        content
        for content, mimetype in mail.outbox[0].alternatives
        if mimetype == "text/html"
    ]
    assert len(html_alternatives) == 1
    assert html_alternatives[0].strip()


def test_email_proposal_respects_preferred_language() -> None:
    """Each recipient's email is rendered in their preferred_language.

    The French-language recipient must still receive the match link: VERB-29
    tracked a French catalogue entry that dropped the ``%(url)s`` placeholder
    from the old Python-source body, silently mailing a linkless
    notification. Since VERB-108 the body lives in
    ``templates/email/match_proposed/body.txt`` with the URL emitted
    *outside* every ``{% blocktranslate %}`` block, so no catalogue entry
    carries the link and a translator cannot drop it. Assert the ``/match/``
    link survives in the French-rendered body (mirrors
    ``..._includes_match_link`` for English).
    """
    ambassador_reg = RegistrationFactory.create(
        role=Registration.Role.AMBASSADOR,
        prior_pass=Registration.PriorPass.SEASONAL,
        preferred_language="fr",
    )
    referee_reg = RegistrationFactory.create(
        referee=True,
        preferred_language="en",
    )
    match = MatchFactory.create(
        ambassador_registration=ambassador_reg,
        referee_registration=referee_reg,
    )
    _email_proposal(ambassador_reg, match)
    _email_proposal(referee_reg, match)
    assert len(mail.outbox) == 2
    fr_message = next(
        message for message in mail.outbox if ambassador_reg.user.email in message.to
    )
    assert "/match/" in fr_message.body


# ---------------------------------------------------------------------------
# register_participant (integration)
# ---------------------------------------------------------------------------


_AMBASSADOR_STATEMENTS = [
    (
        "I have held a seasonal or annual pass from one of the 4 Vallées"
        " companies in 2024/25 or 2025/26."
    ),
    "I have read and agree to the Terms of Use",
]


def test_register_participant_creates_user_and_registration() -> None:
    """register_participant creates a passwordless user and a Registration."""
    registration = register_participant(
        role=Registration.Role.AMBASSADOR,
        first_name="Ada",
        last_name="Lovelace",
        email="ADA@example.com",
        prior_pass=Registration.PriorPass.SEASONAL,
        preferred_location="VERBIER",
        preferred_language="fr",
        phone="+41790000001",
        accepted_terms=_AMBASSADOR_STATEMENTS,
    )

    user = User.objects.get(username="ada@example.com")
    assert user.email == "ada@example.com"
    assert not user.has_usable_password()
    assert registration.role == Registration.Role.AMBASSADOR
    assert registration.prior_pass == Registration.PriorPass.SEASONAL
    assert registration.preferred_location == "VERBIER"
    assert registration.preferred_language == "fr"
    assert registration.phone == "+41790000001"


def test_register_participant_persists_accepted_terms_and_timestamp() -> None:
    """register_participant saves accepted_terms and a tz-aware terms_accepted_at."""
    before = timezone.now()
    registration = register_participant(
        role=Registration.Role.AMBASSADOR,
        first_name="Ada",
        last_name="Lovelace",
        email="ada2@example.com",
        prior_pass=Registration.PriorPass.SEASONAL,
        accepted_terms=_AMBASSADOR_STATEMENTS,
    )
    after = timezone.now()

    assert registration.accepted_terms == _AMBASSADOR_STATEMENTS
    assert registration.terms_accepted_at is not None
    assert registration.terms_accepted_at.tzinfo is not None
    assert before <= registration.terms_accepted_at <= after


@override_settings(REGISTRATION_FEE_TIERS="2020-01-01:5")
def test_register_participant_stamps_locked_fee_for_both_roles() -> None:
    """register_participant stamps today's tier fee onto fee_chf for both roles."""
    ambassador = register_participant(
        role=Registration.Role.AMBASSADOR,
        first_name="Ada",
        last_name="Lovelace",
        email="fee-ambassador@example.com",
        prior_pass=Registration.PriorPass.SEASONAL,
    )
    referee = register_participant(
        role=Registration.Role.REFEREE,
        first_name="Grace",
        last_name="Hopper",
        email="fee-referee@example.com",
        prior_pass=Registration.PriorPass.NONE,
    )

    assert ambassador.fee_chf == 5
    assert referee.fee_chf == 5
    # Confirm the value round-trips to the database, not just the in-memory object.
    ambassador.refresh_from_db()
    assert ambassador.fee_chf == 5


@override_settings(REGISTRATION_FEE_TIERS="")
def test_register_participant_free_tier_stamps_zero_fee() -> None:
    """With no fee schedule configured, a registration is stamped at 0 (free)."""
    registration = register_participant(
        role=Registration.Role.AMBASSADOR,
        first_name="Ada",
        last_name="Lovelace",
        email="fee-free@example.com",
        prior_pass=Registration.PriorPass.SEASONAL,
    )

    assert registration.fee_chf == 0


def test_register_participant_fee_is_frozen_against_later_tier_change() -> None:
    """The stamped fee is locked at signup and not recomputed on a later save."""
    with override_settings(REGISTRATION_FEE_TIERS="2020-01-01:5"):
        registration = register_participant(
            role=Registration.Role.AMBASSADOR,
            first_name="Ada",
            last_name="Lovelace",
            email="fee-frozen@example.com",
            prior_pass=Registration.PriorPass.SEASONAL,
        )
    assert registration.fee_chf == 5

    # A later, higher tier must not retroactively change an existing fee.
    with override_settings(REGISTRATION_FEE_TIERS="2020-01-01:99"):
        registration.save()
        registration.refresh_from_db()

    assert registration.fee_chf == 5


def test_register_participant_without_accepted_terms_leaves_fields_empty() -> None:
    """Omitting accepted_terms stores an empty list and None timestamp."""
    registration = register_participant(
        role=Registration.Role.AMBASSADOR,
        first_name="Ada",
        last_name="Lovelace",
        email="ada3@example.com",
        prior_pass=Registration.PriorPass.SEASONAL,
    )
    assert registration.accepted_terms == []
    assert registration.terms_accepted_at is None


def test_register_participant_empty_accepted_terms_leaves_timestamp_unset() -> None:
    """An explicit empty accepted_terms list records no acceptance timestamp."""
    registration = register_participant(
        role=Registration.Role.AMBASSADOR,
        first_name="Ada",
        last_name="Lovelace",
        email="ada4@example.com",
        prior_pass=Registration.PriorPass.SEASONAL,
        accepted_terms=[],
    )
    assert registration.accepted_terms == []
    assert registration.terms_accepted_at is None


def test_register_participant_triggers_match_when_counterpart_waiting() -> None:
    """register_participant triggers propose_match when a counterpart waits.

    The notification email is deferred via transaction.on_commit so it only fires
    on a successful commit; captureOnCommitCallbacks(execute=True) runs it here.
    """
    # Pre-populate a verified ambassador.
    RegistrationFactory.create(
        role=Registration.Role.AMBASSADOR,
        prior_pass=Registration.PriorPass.SEASONAL,
    )
    # Registering a referee should trigger matching and send notifications.
    with TestCase.captureOnCommitCallbacks(execute=True):
        register_participant(
            role=Registration.Role.REFEREE,
            first_name="Grace",
            last_name="Hopper",
            email="grace@example.com",
            prior_pass=Registration.PriorPass.NONE,
            accepted_terms=[
                "I have not held a mid-season, seasonal or annual pass from one of"
                " the 4 Vallées companies in 2024/25 or 2025/26.",
                "I have read and agree to the Terms of Use",
            ],
        )
    assert Match.objects.count() == 1
    assert len(mail.outbox) == 2  # both parties notified


def test_register_participant_with_existing_user_reuses_it() -> None:
    """Passing a user reuses it (no new user) and keeps the name current."""
    user = UserFactory.create(email="ada@example.com", first_name="A", last_name="L")
    register_participant(
        role=Registration.Role.REFEREE,
        user=user,
        first_name="Ada",
        last_name="Lovelace",
        prior_pass=Registration.PriorPass.NONE,
        accepted_terms=[
            "I have not held a mid-season, seasonal or annual pass from one of"
            " the 4 Vallées companies in 2024/25 or 2025/26.",
            "I have read and agree to the Terms of Use",
        ],
    )
    assert User.objects.count() == 1
    user.refresh_from_db()
    assert user.first_name == "Ada"
    assert user.last_name == "Lovelace"


def test_register_participant_existing_user_matching_names_no_update() -> None:
    """Passing a user whose name already matches skips the name update."""
    user = UserFactory.create(first_name="Ada", last_name="Lovelace")
    register_participant(
        role=Registration.Role.AMBASSADOR,
        user=user,
        first_name="Ada",
        last_name="Lovelace",
        prior_pass=Registration.PriorPass.SEASONAL,
        accepted_terms=_AMBASSADOR_STATEMENTS,
    )
    # Should not raise; user is unchanged.
    user.refresh_from_db()
    assert user.first_name == "Ada"


# ---------------------------------------------------------------------------
# Analytics events (VERB-124)
# ---------------------------------------------------------------------------


def test_register_participant_fires_registration_event() -> None:
    """register_participant sends a 'registration' event with role/status/prior_pass.

    Deferred via transaction.on_commit — captureOnCommitCallbacks(execute=True)
    runs it here, mirroring the notification-email assertions elsewhere in
    this module.
    """
    with (
        patch("matching.services.capture_event") as mock_capture,
        TestCase.captureOnCommitCallbacks(execute=True),
    ):
        registration = register_participant(
            role=Registration.Role.AMBASSADOR,
            first_name="Ada",
            last_name="Lovelace",
            email="ada-analytics@example.com",
            prior_pass=Registration.PriorPass.SEASONAL,
            accepted_terms=_AMBASSADOR_STATEMENTS,
        )

    mock_capture.assert_called_once_with(
        str(registration.user.pk),
        "registration",
        {
            "role": Registration.Role.AMBASSADOR,
            "prior_pass": Registration.PriorPass.SEASONAL,
            "status": Registration.Status.VERIFIED,
        },
    )
    # No PII (email/phone) in the event payload.
    _, _, properties = mock_capture.call_args[0]
    assert "ada-analytics@example.com" not in str(properties)


def test_register_participant_does_not_fire_event_on_rollback() -> None:
    """A rolled-back registration attempt never sends the analytics event."""
    with (
        patch("matching.services.capture_event") as mock_capture,
        pytest.raises(IntegrityError),
        transaction.atomic(),
    ):
        register_participant(
            role=Registration.Role.AMBASSADOR,
            first_name="Ada",
            last_name="Lovelace",
            email="ada-rollback@example.com",
            prior_pass=Registration.PriorPass.SEASONAL,
            accepted_terms=_AMBASSADOR_STATEMENTS,
        )
        # Force a rollback by violating the OneToOne constraint on Registration.
        Registration.objects.create(
            user=User.objects.get(email="ada-rollback@example.com"),
            role=Registration.Role.REFEREE,
            prior_pass=Registration.PriorPass.NONE,
        )

    mock_capture.assert_not_called()


def test_confirm_registration_fires_email_verified_event() -> None:
    """confirm_registration sends an 'email_verified' event with the role."""
    registration = RegistrationFactory.create(status=Registration.Status.UNVERIFIED)

    with (
        patch("matching.services.capture_event") as mock_capture,
        TestCase.captureOnCommitCallbacks(execute=True),
    ):
        confirm_registration(registration)

    mock_capture.assert_called_once_with(
        str(registration.user.pk),
        "email_verified",
        {"role": registration.role},
    )


def test_confirm_registration_noop_does_not_fire_event() -> None:
    """confirm_registration's no-op path (already VERIFIED) fires no event."""
    registration = RegistrationFactory.create(status=Registration.Status.VERIFIED)

    with (
        patch("matching.services.capture_event") as mock_capture,
        TestCase.captureOnCommitCallbacks(execute=True),
    ):
        confirm_registration(registration)

    mock_capture.assert_not_called()


def test_accept_match_fires_match_accepted_then_match_confirmed_events() -> None:
    """The first accept fires match_accepted; the mutual accept fires match_confirmed.

    No PII (email/phone) is present in either event's properties.
    """
    ambassador_reg = RegistrationFactory.create()
    referee_reg = RegistrationFactory.create(referee=True)
    match = MatchFactory.create(
        ambassador_registration=ambassador_reg,
        referee_registration=referee_reg,
    )

    with patch("matching.side_effects.capture_event") as mock_capture:
        with TestCase.captureOnCommitCallbacks(execute=True):
            accept_match(match, ambassador_reg)

    mock_capture.assert_called_once_with(
        str(ambassador_reg.user.pk),
        "match_accepted",
        {"role": ambassador_reg.role},
    )
    mock_capture.reset_mock()

    with patch("matching.side_effects.capture_event") as mock_capture:
        with TestCase.captureOnCommitCallbacks(execute=True):
            accept_match(match, referee_reg)

    mock_capture.assert_called_once_with(
        str(referee_reg.user.pk),
        "match_confirmed",
        {"role": referee_reg.role},
    )
    # No PII (email/phone) in the event payload.
    _, _, properties = mock_capture.call_args[0]
    assert ambassador_reg.user.email not in str(properties)
    assert referee_reg.phone not in str(properties)


def test_propose_match_skips_ineligible_referee() -> None:
    """propose_match returns None for a referee with a prior pass."""
    RegistrationFactory.create(
        role=Registration.Role.AMBASSADOR,
        prior_pass=Registration.PriorPass.SEASONAL,
    )
    ineligible_referee = RegistrationFactory.create(
        role=Registration.Role.REFEREE,
        prior_pass=Registration.PriorPass.SEASONAL,
    )
    with transaction.atomic():
        result = propose_match(ineligible_referee)
    assert result is None
    assert Match.objects.count() == 0


def test_propose_match_skips_registration_with_active_match() -> None:
    """propose_match skips a registration that already holds an active match.

    VERB-44: pool availability is controlled by _without_active_match (a
    queryset exclusion on the Match table), not by flipping Registration.status
    to MATCHED. A registration stays VERIFIED but is excluded from the pool
    while an active match (PROPOSED, PENDING, or ACCEPTED) exists.
    """
    referee = RegistrationFactory.create(referee=True)
    already_matched = RegistrationFactory.create(
        role=Registration.Role.AMBASSADOR,
        prior_pass=Registration.PriorPass.SEASONAL,
    )
    # Create an active match tying the ambassador to the existing referee.
    MatchFactory.create(
        ambassador_registration=already_matched,
        referee_registration=referee,
    )
    # Create a second referee looking for a partner.
    new_referee = RegistrationFactory.create(referee=True)

    with transaction.atomic():
        result = propose_match(new_referee)
    # The ambassador already has an active match; the second referee finds no partner.
    assert result is None
    assert Match.objects.count() == 1


# ---------------------------------------------------------------------------
# Open-date gate (VERB-83) — propose_match defers before matching_opens_at()
# ---------------------------------------------------------------------------

# A far-future MATCHING_OPENS_AT string: matching is "not yet open" for tests
# that exercise the pre-open gate.
_FUTURE_OPEN = "2099-01-01T00:00:00+00:00"


@override_settings(MATCHING_OPENS_AT=_FUTURE_OPEN)
def test_propose_match_deferred_before_open_date() -> None:
    """propose_match is a no-op (returns None, writes nothing) before the open date."""
    referee = RegistrationFactory.create(referee=True)
    ambassador = RegistrationFactory.create(
        role=Registration.Role.AMBASSADOR,
        prior_pass=Registration.PriorPass.SEASONAL,
    )
    with transaction.atomic():
        result = propose_match(ambassador)

    assert result is None
    assert Match.objects.count() == 0
    # Both parties remain in the eligible pool.
    assert referee in Registration.objects.eligible_referees()
    assert ambassador in Registration.objects.eligible_ambassadors()


@override_settings(MATCHING_OPENS_AT=_FUTURE_OPEN)
def test_register_participant_pre_open_enqueues_without_match() -> None:
    """Pre-open: an eligible counterpart enqueues VERIFIED but no match is proposed."""
    RegistrationFactory.create(
        role=Registration.Role.AMBASSADOR,
        prior_pass=Registration.PriorPass.SEASONAL,
        status=Registration.Status.VERIFIED,
    )
    with TestCase.captureOnCommitCallbacks(execute=True):
        registration = register_participant(
            role=Registration.Role.REFEREE,
            first_name="Grace",
            last_name="Hopper",
            email="grace-preopen@example.com",
            prior_pass=Registration.PriorPass.NONE,
        )

    # The referee is verified and enqueued, but no match exists yet.
    assert registration.status == Registration.Status.VERIFIED
    assert Match.objects.count() == 0
    assert registration in Registration.objects.eligible_referees()


@override_settings(MATCHING_OPENS_AT=_FUTURE_OPEN)
def test_confirm_registration_pre_open_does_not_propose_match() -> None:
    """Pre-open: confirming an UNVERIFIED registration verifies it but proposes nothing.

    Confirmation is a separate chokepoint from register_participant; the gate
    lives in propose_match so a pre-open email confirmation cannot leak a match.
    """
    RegistrationFactory.create(
        role=Registration.Role.AMBASSADOR,
        prior_pass=Registration.PriorPass.SEASONAL,
        status=Registration.Status.VERIFIED,
    )
    unverified = RegistrationFactory.create(
        referee=True,
        status=Registration.Status.UNVERIFIED,
    )

    with TestCase.captureOnCommitCallbacks(execute=True):
        confirm_registration(unverified)

    unverified.refresh_from_db()
    assert unverified.status == Registration.Status.VERIFIED
    assert Match.objects.count() == 0


@override_settings(MATCHING_OPENS_AT=_FUTURE_OPEN)
def test_rejoin_queue_pre_open_does_not_propose_match() -> None:
    """Pre-open: rejoining the queue re-verifies but proposes no match.

    A paused participant self-rejoining before the open date must not leak a
    match — the gate in propose_match covers this caller too.
    """
    RegistrationFactory.create(referee=True)
    ambassador = RegistrationFactory.create(
        role=Registration.Role.AMBASSADOR,
        prior_pass=Registration.PriorPass.SEASONAL,
        status=Registration.Status.PAUSED,
    )

    with TestCase.captureOnCommitCallbacks(execute=True):
        rejoin_queue(ambassador)

    ambassador.refresh_from_db()
    assert ambassador.status == Registration.Status.VERIFIED
    assert Match.objects.count() == 0


def test_register_participant_post_open_still_proposes_match() -> None:
    """Post-open (default past MATCHING_OPENS_AT): rolling synchronous propose fires."""
    RegistrationFactory.create(
        role=Registration.Role.AMBASSADOR,
        prior_pass=Registration.PriorPass.SEASONAL,
        status=Registration.Status.VERIFIED,
    )
    with TestCase.captureOnCommitCallbacks(execute=True):
        register_participant(
            role=Registration.Role.REFEREE,
            first_name="Grace",
            last_name="Hopper",
            email="grace-postopen@example.com",
            prior_pass=Registration.PriorPass.NONE,
        )

    assert Match.objects.count() == 1


# ---------------------------------------------------------------------------
# run_matching service (VERB-83)
# ---------------------------------------------------------------------------


def test_run_matching_dry_run_reports_without_writing() -> None:
    """run_matching(commit=False) reports the count and creates no matches."""
    RegistrationFactory.create(
        role=Registration.Role.AMBASSADOR,
        prior_pass=Registration.PriorPass.SEASONAL,
    )
    RegistrationFactory.create(referee=True)

    proposed, failed = run_matching(commit=False)

    assert (proposed, failed) == (1, 0)
    assert Match.objects.count() == 0


def test_run_matching_commit_drains_the_pool() -> None:
    """run_matching(commit=True) proposes matches for every pairable ambassador."""
    for _ in range(3):
        RegistrationFactory.create(
            role=Registration.Role.AMBASSADOR,
            prior_pass=Registration.PriorPass.SEASONAL,
        )
        RegistrationFactory.create(referee=True)

    with TestCase.captureOnCommitCallbacks(execute=True):
        proposed, failed = run_matching(commit=True)

    assert (proposed, failed) == (3, 0)
    assert Match.objects.filter(status=Match.Status.PROPOSED).count() == 3
    # Pool fully drained on both sides.
    assert not Registration.objects.eligible_ambassadors().exists()
    assert not Registration.objects.eligible_referees().exists()


def test_run_matching_leaves_no_eligible_pair_when_sides_unequal() -> None:
    """With more ambassadors than referees, only the referee count is proposed."""
    for _ in range(3):
        RegistrationFactory.create(
            role=Registration.Role.AMBASSADOR,
            prior_pass=Registration.PriorPass.SEASONAL,
        )
    RegistrationFactory.create(referee=True)

    with TestCase.captureOnCommitCallbacks(execute=True):
        proposed, failed = run_matching(commit=True)

    assert (proposed, failed) == (1, 0)
    assert Match.objects.count() == 1
    # Two ambassadors remain waiting; no referees left.
    assert Registration.objects.eligible_ambassadors().count() == 2
    assert not Registration.objects.eligible_referees().exists()


def test_run_matching_respects_priority_then_fifo_order() -> None:
    """run_matching pairs the highest-priority ambassador first, then FIFO.

    One referee, two ambassadors: the higher-priority ambassador must be the
    one paired. This proves the drain honours the engine's ranking.
    """
    referee = RegistrationFactory.create(referee=True)
    low_priority = RegistrationFactory.create(
        role=Registration.Role.AMBASSADOR,
        prior_pass=Registration.PriorPass.SEASONAL,
        priority=0,
    )
    high_priority = RegistrationFactory.create(
        role=Registration.Role.AMBASSADOR,
        prior_pass=Registration.PriorPass.SEASONAL,
        priority=5,
    )

    with TestCase.captureOnCommitCallbacks(execute=True):
        proposed, failed = run_matching(commit=True)

    assert (proposed, failed) == (1, 0)
    match = Match.objects.get()
    assert match.ambassador_registration == high_priority
    assert match.referee_registration == referee
    # The low-priority ambassador is left waiting.
    assert low_priority in Registration.objects.eligible_ambassadors()


def test_run_matching_dry_run_matches_commit_with_location_preference() -> None:
    """The dry-run predicts the commit exactly, including shared-location ranking.

    VERB-137: the simulate path and the live propose_match path share
    ``_rank_candidates``, so the dry-run count must equal the committed count and
    the same shared-location preference must decide the pairings. The referee
    with the earlier ``created_at`` here is in the *other* location, so a naive
    FIFO would pair it first; the location-aware ranking must instead pair each
    ambassador with the referee in its own location.
    """
    ambassador_thyon = RegistrationFactory.create(
        role=Registration.Role.AMBASSADOR,
        prior_pass=Registration.PriorPass.SEASONAL,
        preferred_location="thyon",
    )
    ambassador_verbier = RegistrationFactory.create(
        role=Registration.Role.AMBASSADOR,
        prior_pass=Registration.PriorPass.SEASONAL,
        preferred_location="verbier",
    )
    # Verbier referee created first (earlier FIFO position) than the Thyon one.
    referee_verbier = RegistrationFactory.create(
        referee=True, preferred_location="verbier"
    )
    referee_thyon = RegistrationFactory.create(referee=True, preferred_location="thyon")

    dry_proposed, dry_failed = run_matching(commit=False)
    assert (dry_proposed, dry_failed) == (2, 0)
    assert Match.objects.count() == 0

    with TestCase.captureOnCommitCallbacks(execute=True):
        proposed, failed = run_matching(commit=True)

    # Dry-run count is exactly the committed count.
    assert (proposed, failed) == (dry_proposed, dry_failed)
    # Each ambassador paired with the referee in its own location (not FIFO).
    thyon_match = Match.objects.get(ambassador_registration=ambassador_thyon)
    assert thyon_match.referee_registration == referee_thyon
    verbier_match = Match.objects.get(ambassador_registration=ambassador_verbier)
    assert verbier_match.referee_registration == referee_verbier


def test_run_matching_skips_ineligible_pairs() -> None:
    """run_matching never proposes an ineligible pair (no eligible counterpart)."""
    RegistrationFactory.create(
        role=Registration.Role.AMBASSADOR,
        prior_pass=Registration.PriorPass.SEASONAL,
    )
    # A referee who is not genuinely new (holds a prior pass) is ineligible.
    RegistrationFactory.create(
        role=Registration.Role.REFEREE,
        prior_pass=Registration.PriorPass.SEASONAL,
    )

    proposed, failed = run_matching(commit=True)

    assert (proposed, failed) == (0, 0)
    assert Match.objects.count() == 0


def test_run_matching_commit_counts_and_isolates_failures() -> None:
    """A failed proposal is counted and isolated; the rest of the batch proceeds.

    Patches propose_match to raise on its first call and delegate afterwards.
    run_matching must return failed=1 and still propose the remaining pair.
    """
    from unittest.mock import patch

    import matching.services as _svc

    for _ in range(2):
        RegistrationFactory.create(
            role=Registration.Role.AMBASSADOR,
            prior_pass=Registration.PriorPass.SEASONAL,
        )
        RegistrationFactory.create(referee=True)

    _call_count = {"n": 0}
    _real = _svc.propose_match

    def _failing_propose(registration: Registration) -> Match | None:
        """Raise on the first invocation; delegate to the real function after."""
        _call_count["n"] += 1
        if _call_count["n"] == 1:
            raise RuntimeError("simulated proposal failure")
        return _real(registration)

    with patch.object(_svc, "propose_match", _failing_propose):
        with TestCase.captureOnCommitCallbacks(execute=True):
            proposed, failed = run_matching(commit=True)

    assert failed == 1
    assert proposed == 1
    assert Match.objects.count() == 1


# ---------------------------------------------------------------------------
# requeue_to_front
# ---------------------------------------------------------------------------


def test_requeue_to_front_sets_verified_and_increments_priority() -> None:
    """requeue_to_front sets status=VERIFIED and increments priority by 1."""
    reg = RegistrationFactory.create(
        status=Registration.Status.VERIFIED,
        priority=0,
    )
    requeue_to_front(reg)

    reg.refresh_from_db()
    assert reg.status == Registration.Status.VERIFIED
    assert reg.priority == 1


def test_requeue_to_front_syncs_in_memory_instance() -> None:
    """requeue_to_front syncs the passed-in instance's fields to the DB values."""
    reg = RegistrationFactory.create(
        status=Registration.Status.VERIFIED,
        priority=5,
    )
    requeue_to_front(reg)

    # In-memory fields must match DB without an extra refresh.
    assert reg.status == Registration.Status.VERIFIED
    assert reg.priority == 6


def test_requeue_to_front_uses_in_memory_priority_not_db() -> None:
    """requeue_to_front increments the in-memory priority; no row lock (VERB-106).

    The DB priority is 5 but the passed-in instance carries a diverged 0. With
    the optimistic lock / re-fetch removed, the increment is computed from the
    in-memory value (0 → 1) and written straight to the row.
    """
    reg = RegistrationFactory.create(
        status=Registration.Status.VERIFIED,
        priority=5,  # DB value is 5
    )
    reg.priority = 0  # in-memory divergence — this is what the service now uses

    requeue_to_front(reg)

    assert reg.priority == 1
    reg.refresh_from_db()
    assert reg.priority == 1


# ---------------------------------------------------------------------------
# pause_registration (VERB-74 / ADR 0013)
# ---------------------------------------------------------------------------


def test_pause_registration_sets_paused_status() -> None:
    """pause_registration transitions a VERIFIED registration to PAUSED."""
    reg = RegistrationFactory.create(
        status=Registration.Status.VERIFIED,
        priority=3,
    )
    pause_registration(reg)

    reg.refresh_from_db()
    assert reg.status == Registration.Status.PAUSED
    assert reg.priority == 3  # unchanged


def test_pause_registration_syncs_in_memory_instance() -> None:
    """pause_registration syncs the passed-in instance's status without a refresh."""
    reg = RegistrationFactory.create(status=Registration.Status.VERIFIED)
    pause_registration(reg)

    assert reg.status == Registration.Status.PAUSED


def test_pause_registration_excludes_from_eligible_pool() -> None:
    """A PAUSED registration is not returned by eligible_ambassadors/referees."""
    reg = RegistrationFactory.create(
        role=Registration.Role.AMBASSADOR,
        prior_pass=Registration.PriorPass.SEASONAL,
    )
    pause_registration(reg)

    assert not Registration.objects.eligible_ambassadors().filter(pk=reg.pk).exists()


# ---------------------------------------------------------------------------
# rejoin_queue (VERB-74 / ADR 0013)
# ---------------------------------------------------------------------------


def test_rejoin_queue_transitions_paused_to_verified() -> None:
    """rejoin_queue transitions a PAUSED registration back to VERIFIED."""
    reg = RegistrationFactory.create(
        status=Registration.Status.PAUSED,
        priority=0,
    )
    rejoin_queue(reg)

    reg.refresh_from_db()
    assert reg.status == Registration.Status.VERIFIED
    assert reg.priority == -1  # priority -= 1 on each rejoin


def test_rejoin_queue_syncs_in_memory_instance() -> None:
    """rejoin_queue syncs the passed-in instance's fields without a refresh."""
    reg = RegistrationFactory.create(
        status=Registration.Status.PAUSED,
        priority=5,
    )
    rejoin_queue(reg)

    assert reg.status == Registration.Status.VERIFIED
    assert reg.priority == 4


def test_rejoin_queue_is_noop_for_non_paused_registration() -> None:
    """rejoin_queue on a non-PAUSED registration is a no-op."""
    reg = RegistrationFactory.create(
        status=Registration.Status.VERIFIED,
        priority=0,
    )
    rejoin_queue(reg)

    reg.refresh_from_db()
    assert reg.status == Registration.Status.VERIFIED
    assert reg.priority == 0  # unchanged


def test_rejoin_queue_proposes_match_when_counterpart_waiting() -> None:
    """rejoin_queue calls propose_match; a match is created if a counterpart waits."""
    referee = RegistrationFactory.create(referee=True)
    ambassador = RegistrationFactory.create(
        role=Registration.Role.AMBASSADOR,
        prior_pass=Registration.PriorPass.SEASONAL,
        status=Registration.Status.PAUSED,
    )
    with TestCase.captureOnCommitCallbacks(execute=True):
        rejoin_queue(ambassador)

    assert Match.objects.count() == 1
    match = Match.objects.get()
    assert match.ambassador_registration == ambassador
    assert match.referee_registration == referee


# ---------------------------------------------------------------------------
# suspend_for_no_show
# ---------------------------------------------------------------------------


def test_suspend_for_no_show_sets_suspended() -> None:
    """suspend_for_no_show sets status=SUSPENDED (no flake_count, VERB-74)."""
    reg = RegistrationFactory.create(status=Registration.Status.VERIFIED)
    suspend_for_no_show(reg)

    reg.refresh_from_db()
    assert reg.status == Registration.Status.SUSPENDED


def test_suspend_for_no_show_syncs_in_memory_instance() -> None:
    """suspend_for_no_show syncs the passed-in instance's status field."""
    reg = RegistrationFactory.create(status=Registration.Status.VERIFIED)
    suspend_for_no_show(reg)

    assert reg.status == Registration.Status.SUSPENDED


# ---------------------------------------------------------------------------
# Engine exclusion — SUSPENDED registrations must never enter matching
# ---------------------------------------------------------------------------


def test_suspended_ambassador_excluded_from_eligible_ambassadors() -> None:
    """A SUSPENDED ambassador does not appear in eligible_ambassadors()."""
    RegistrationFactory.create(
        role=Registration.Role.AMBASSADOR,
        prior_pass=Registration.PriorPass.SEASONAL,
        suspended=True,
    )
    assert not Registration.objects.eligible_ambassadors().exists()


def test_suspended_referee_excluded_from_eligible_referees() -> None:
    """A SUSPENDED referee does not appear in eligible_referees()."""
    RegistrationFactory.create(referee=True, suspended=True)
    assert not Registration.objects.eligible_referees().exists()


def test_is_eligible_pair_returns_false_when_ambassador_suspended() -> None:
    """is_eligible_pair returns False when the ambassador is SUSPENDED."""
    ambassador = RegistrationFactory.create(
        role=Registration.Role.AMBASSADOR,
        prior_pass=Registration.PriorPass.SEASONAL,
        suspended=True,
    )
    referee = RegistrationFactory.create(referee=True)
    assert is_eligible_pair(ambassador, referee) is False


def test_is_eligible_pair_returns_false_when_referee_suspended() -> None:
    """is_eligible_pair returns False when the referee is SUSPENDED."""
    ambassador = RegistrationFactory.create(
        role=Registration.Role.AMBASSADOR,
        prior_pass=Registration.PriorPass.SEASONAL,
    )
    referee = RegistrationFactory.create(referee=True, suspended=True)
    assert is_eligible_pair(ambassador, referee) is False


# ---------------------------------------------------------------------------
# register_participant — UNVERIFIED status (VERB-24 / VERB-44)
# ---------------------------------------------------------------------------


def test_register_participant_unverified_creates_unverified_registration() -> None:
    """register_participant(status=UNVERIFIED) creates an UNVERIFIED registration."""
    registration = register_participant(
        role=Registration.Role.REFEREE,
        first_name="Grace",
        last_name="Hopper",
        email="grace@example.com",
        prior_pass=Registration.PriorPass.NONE,
        status=Registration.Status.UNVERIFIED,
    )
    assert registration.status == Registration.Status.UNVERIFIED


def test_register_participant_unverified_does_not_propose_match() -> None:
    """An UNVERIFIED registration must never trigger propose_match (Invariant 2)."""
    RegistrationFactory.create(
        role=Registration.Role.AMBASSADOR,
        prior_pass=Registration.PriorPass.SEASONAL,
        status=Registration.Status.VERIFIED,
    )
    register_participant(
        role=Registration.Role.REFEREE,
        first_name="Grace",
        last_name="Hopper",
        email="grace@example.com",
        prior_pass=Registration.PriorPass.NONE,
        status=Registration.Status.UNVERIFIED,
    )
    assert Match.objects.count() == 0


def test_register_participant_verified_still_proposes_match() -> None:
    """The default (VERIFIED) path still calls propose_match (regression guard)."""
    RegistrationFactory.create(
        role=Registration.Role.AMBASSADOR,
        prior_pass=Registration.PriorPass.SEASONAL,
        status=Registration.Status.VERIFIED,
    )
    with TestCase.captureOnCommitCallbacks(execute=True):
        register_participant(
            role=Registration.Role.REFEREE,
            first_name="Grace",
            last_name="Hopper",
            email="grace2@example.com",
            prior_pass=Registration.PriorPass.NONE,
        )
    assert Match.objects.count() == 1


# ---------------------------------------------------------------------------
# register_participant — nationality kwarg
# ---------------------------------------------------------------------------


def test_register_participant_persists_nationality() -> None:
    """Supplying nationality persists the ISO country code on the Registration."""
    registration = register_participant(
        role=Registration.Role.AMBASSADOR,
        first_name="Ada",
        last_name="Lovelace",
        email="ada-nationality@example.com",
        prior_pass=Registration.PriorPass.SEASONAL,
        nationality="CH",
        accepted_terms=_AMBASSADOR_STATEMENTS,
    )
    assert str(registration.nationality) == "CH"


def test_register_participant_nationality_defaults_to_empty() -> None:
    """Omitting nationality stores an empty string (field is optional)."""
    registration = register_participant(
        role=Registration.Role.AMBASSADOR,
        first_name="Ada",
        last_name="Lovelace",
        email="ada-no-nationality@example.com",
        prior_pass=Registration.PriorPass.SEASONAL,
        accepted_terms=_AMBASSADOR_STATEMENTS,
    )
    assert str(registration.nationality) == ""


# ---------------------------------------------------------------------------
# confirm_registration (VERB-24 / VERB-44)
# ---------------------------------------------------------------------------


def test_confirm_registration_transitions_unverified_to_verified() -> None:
    """confirm_registration transitions an UNVERIFIED registration to VERIFIED."""
    reg = RegistrationFactory.create(
        role=Registration.Role.AMBASSADOR,
        prior_pass=Registration.PriorPass.SEASONAL,
        status=Registration.Status.UNVERIFIED,
    )
    result = confirm_registration(reg)
    assert result.status == Registration.Status.VERIFIED
    reg.refresh_from_db()
    assert reg.status == Registration.Status.VERIFIED


def test_confirm_registration_proposes_match_after_flip() -> None:
    """confirm_registration calls propose_match after transitioning to VERIFIED."""
    RegistrationFactory.create(
        role=Registration.Role.AMBASSADOR,
        prior_pass=Registration.PriorPass.SEASONAL,
        status=Registration.Status.VERIFIED,
    )
    reg = RegistrationFactory.create(
        referee=True,
        status=Registration.Status.UNVERIFIED,
    )
    with TestCase.captureOnCommitCallbacks(execute=True):
        confirm_registration(reg)
    assert Match.objects.count() == 1


def test_confirm_registration_non_unverified_is_noop() -> None:
    """confirm_registration on a non-UNVERIFIED registration is a no-op."""
    reg = RegistrationFactory.create(
        role=Registration.Role.AMBASSADOR,
        prior_pass=Registration.PriorPass.SEASONAL,
        status=Registration.Status.VERIFIED,
    )
    result = confirm_registration(reg)
    # Status unchanged; no match created (no counterpart anyway).
    assert result.status == Registration.Status.VERIFIED
    assert Match.objects.count() == 0


def test_confirm_registration_syncs_in_memory_instance() -> None:
    """confirm_registration syncs the passed-in instance's status field."""
    reg = RegistrationFactory.create(
        status=Registration.Status.UNVERIFIED,
    )
    result = confirm_registration(reg)
    # Must be synced without a separate refresh.
    assert result.status == Registration.Status.VERIFIED
    assert reg.status == Registration.Status.VERIFIED


# ---------------------------------------------------------------------------
# record_acceptance (VERB-44: PROPOSED→PENDING on first, PENDING→ACCEPTED on second)
# ---------------------------------------------------------------------------


def test_record_acceptance_first_accept_by_ambassador_goes_pending() -> None:
    """First accept by ambassador sets ambassador_accepted_at; status goes PENDING.

    VERB-44: the first acceptance transitions PROPOSED → PENDING and writes a
    StateTransitionLog row. Registrations remain VERIFIED.
    """
    ambassador_reg = RegistrationFactory.create()
    referee_reg = RegistrationFactory.create(referee=True)
    match = MatchFactory.create(
        ambassador_registration=ambassador_reg,
        referee_registration=referee_reg,
    )
    before = timezone.now()

    result = record_acceptance(match, ambassador_reg)

    after = timezone.now()
    result.refresh_from_db()
    assert result.status == Match.Status.PENDING
    assert result.ambassador_accepted_at is not None
    assert before <= result.ambassador_accepted_at <= after
    assert result.referee_accepted_at is None
    # One log row written for PROPOSED → PENDING.
    assert StateTransitionLog.objects.count() == 1
    log = StateTransitionLog.objects.get()
    assert log.state_before == Match.Status.PROPOSED
    assert log.state_after == Match.Status.PENDING


def test_record_acceptance_first_accept_by_referee_goes_pending() -> None:
    """First accept by referee sets referee_accepted_at; status goes PENDING."""
    ambassador_reg = RegistrationFactory.create()
    referee_reg = RegistrationFactory.create(referee=True)
    match = MatchFactory.create(
        ambassador_registration=ambassador_reg,
        referee_registration=referee_reg,
    )

    result = record_acceptance(match, referee_reg)

    result.refresh_from_db()
    assert result.status == Match.Status.PENDING
    assert result.referee_accepted_at is not None
    assert result.ambassador_accepted_at is None
    assert StateTransitionLog.objects.count() == 1


def test_record_acceptance_second_accept_transitions_to_accepted() -> None:
    """Second accept transitions Match → ACCEPTED.

    VERB-44: Registration statuses remain VERIFIED — pool standing is
    independent of match progress.
    """
    ambassador_reg = RegistrationFactory.create()
    referee_reg = RegistrationFactory.create(referee=True)
    match = MatchFactory.create(
        ambassador_registration=ambassador_reg,
        referee_registration=referee_reg,
    )

    # First accept — ambassador (PROPOSED → PENDING).
    record_acceptance(match, ambassador_reg)
    match.refresh_from_db()
    assert match.status == Match.Status.PENDING

    # Second accept — referee (PENDING → ACCEPTED).
    result = record_acceptance(match, referee_reg)

    result.refresh_from_db()
    assert result.status == Match.Status.ACCEPTED

    # Registrations stay VERIFIED — they no longer flip to CONFIRMED (VERB-44).
    ambassador_reg.refresh_from_db()
    referee_reg.refresh_from_db()
    assert ambassador_reg.status == Registration.Status.VERIFIED
    assert referee_reg.status == Registration.Status.VERIFIED


def test_record_acceptance_second_accept_writes_two_log_rows() -> None:
    """Mutual accept writes exactly two StateTransitionLog rows.

    VERB-44: one for PROPOSED → PENDING (first accept) and one for
    PENDING → ACCEPTED (second accept). Registration statuses are no longer
    logged because they no longer change.
    """
    from django.contrib.contenttypes.models import ContentType

    ambassador_reg = RegistrationFactory.create()
    referee_reg = RegistrationFactory.create(referee=True)
    match = MatchFactory.create(
        ambassador_registration=ambassador_reg,
        referee_registration=referee_reg,
    )

    record_acceptance(match, ambassador_reg)
    record_acceptance(match, referee_reg)

    logs = list(StateTransitionLog.objects.order_by("pk"))
    assert len(logs) == 2

    match_ct = ContentType.objects.get_for_model(Match)
    match_logs = [log for log in logs if log.content_type_id == match_ct.pk]
    assert len(match_logs) == 2

    transitions = {(log.state_before, log.state_after) for log in match_logs}
    assert (Match.Status.PROPOSED, Match.Status.PENDING) in transitions
    assert (Match.Status.PENDING, Match.Status.ACCEPTED) in transitions


def test_record_acceptance_re_accept_is_idempotent_for_timestamp() -> None:
    """Re-accepting an already-accepted side does not change the existing timestamp."""
    ambassador_reg = RegistrationFactory.create()
    referee_reg = RegistrationFactory.create(referee=True)
    match = MatchFactory.create(
        ambassador_registration=ambassador_reg,
        referee_registration=referee_reg,
    )

    result_first = record_acceptance(match, ambassador_reg)
    result_first.refresh_from_db()
    original_ts = result_first.ambassador_accepted_at

    # Accept again — the timestamp must not change.
    result_second = record_acceptance(result_first, ambassador_reg)
    result_second.refresh_from_db()
    assert result_second.ambassador_accepted_at == original_ts
    # Status stays PENDING (ambassador accepted but referee hasn't).
    assert result_second.status == Match.Status.PENDING


def test_record_acceptance_raises_for_non_active_match() -> None:
    """record_acceptance raises StateTransitionError if match.status is terminal."""
    match = MatchFactory.create(status=Match.Status.DECLINED)
    ambassador_reg = match.ambassador_registration

    with pytest.raises(StateTransitionError):
        record_acceptance(match, ambassador_reg)


# ---------------------------------------------------------------------------
# record_decline
# ---------------------------------------------------------------------------


def test_record_decline_transitions_match_to_declined() -> None:
    """record_decline transitions match PROPOSED → DECLINED and sets declined_by/at."""
    ambassador_reg = RegistrationFactory.create()
    referee_reg = RegistrationFactory.create(referee=True)
    match = MatchFactory.create(
        ambassador_registration=ambassador_reg,
        referee_registration=referee_reg,
    )
    before = timezone.now()

    result = record_decline(match, ambassador_reg)

    after = timezone.now()
    result.refresh_from_db()
    assert result.status == Match.Status.DECLINED
    assert result.declined_by == Match.Side.AMBASSADOR
    assert result.declined_at is not None
    assert before <= result.declined_at <= after


def test_record_decline_by_referee_sets_referee_side() -> None:
    """record_decline by the referee side sets declined_by=REFEREE."""
    ambassador_reg = RegistrationFactory.create()
    referee_reg = RegistrationFactory.create(referee=True)
    match = MatchFactory.create(
        ambassador_registration=ambassador_reg,
        referee_registration=referee_reg,
    )

    result = record_decline(match, referee_reg)

    result.refresh_from_db()
    assert result.declined_by == Match.Side.REFEREE


def test_record_decline_writes_one_log_row_for_match_status() -> None:
    """record_decline writes exactly one StateTransitionLog row for Match.status."""
    match = MatchFactory.create()
    ambassador_reg = match.ambassador_registration

    record_decline(match, ambassador_reg)

    logs = list(StateTransitionLog.objects.all())
    assert len(logs) == 1
    log = logs[0]
    assert log.object_id == match.pk
    assert log.field_name == "status"
    assert log.state_before == Match.Status.PROPOSED
    assert log.state_after == Match.Status.DECLINED


def test_record_decline_does_not_change_registration_statuses() -> None:
    """record_decline leaves Registration.status untouched — re-queue is VERB-17."""
    ambassador_reg = RegistrationFactory.create()
    referee_reg = RegistrationFactory.create(referee=True)
    match = MatchFactory.create(
        ambassador_registration=ambassador_reg,
        referee_registration=referee_reg,
    )

    record_decline(match, ambassador_reg)

    ambassador_reg.refresh_from_db()
    referee_reg.refresh_from_db()
    # Re-queuing belongs to VERB-17; this service must not touch these.
    assert ambassador_reg.status == Registration.Status.VERIFIED
    assert referee_reg.status == Registration.Status.VERIFIED


def test_record_decline_raises_for_non_active_match() -> None:
    """record_decline raises StateTransitionError if match.status is terminal."""
    match = MatchFactory.create(status=Match.Status.ACCEPTED)
    referee_reg = match.referee_registration

    with pytest.raises(StateTransitionError) as exc_info:
        record_decline(match, referee_reg)

    assert exc_info.value.current == Match.Status.ACCEPTED
    assert exc_info.value.proposed == Match.Status.DECLINED


# ---------------------------------------------------------------------------
# _email_confirmation (matching.side_effects) — the match_accepted
# confirmation handlers' shared render helper, one recipient per call.
# ---------------------------------------------------------------------------


def test_email_confirmation_sends_one_email_per_call() -> None:
    """_email_confirmation sends one email; both sides is two calls."""
    match = MatchFactory.create(accepted=True)
    assert match.ambassador_registration is not None
    assert match.referee_registration is not None
    _email_confirmation(match.ambassador_registration, match.referee_registration)
    _email_confirmation(match.referee_registration, match.ambassador_registration)
    assert len(mail.outbox) == 2


def test_email_confirmation_contains_counterpart_details() -> None:
    """Each confirmed email contains the counterpart's name, email, and phone."""
    ambassador_reg = RegistrationFactory.create(
        role=Registration.Role.AMBASSADOR,
        prior_pass=Registration.PriorPass.SEASONAL,
        phone="+41790001111",
    )
    referee_reg = RegistrationFactory.create(
        referee=True,
        phone="+41790002222",
    )
    MatchFactory.create(
        accepted=True,
        ambassador_registration=ambassador_reg,
        referee_registration=referee_reg,
    )
    _email_confirmation(ambassador_reg, referee_reg)
    _email_confirmation(referee_reg, ambassador_reg)
    assert len(mail.outbox) == 2

    def _html_part(to_address: str) -> str:
        """Return the text/html alternative body for the message sent to to_address."""
        message = next(msg for msg in mail.outbox if msg.to == [to_address])
        return next(
            content
            for content, mimetype in message.alternatives
            if mimetype == "text/html"
        )

    # Map To: addresses to message bodies (text part).
    body_by_recipient = {msg.to[0]: msg.body for msg in mail.outbox}

    # The ambassador's email contains the referee's contact details, in both
    # the text and the HTML part.
    amb_body = body_by_recipient[ambassador_reg.user.email]
    amb_html = _html_part(ambassador_reg.user.email)
    for amb_part in (amb_body, amb_html):
        assert referee_reg.user.email in amb_part
        assert referee_reg.phone in amb_part
        assert (
            referee_reg.user.first_name in amb_part or not referee_reg.user.first_name
        )

    # The referee's email contains the ambassador's contact details, in both
    # the text and the HTML part.
    ref_body = body_by_recipient[referee_reg.user.email]
    ref_html = _html_part(referee_reg.user.email)
    for ref_part in (ref_body, ref_html):
        assert ambassador_reg.user.email in ref_part
        assert ambassador_reg.phone in ref_part
        assert (
            ambassador_reg.user.first_name in ref_part
            or not ambassador_reg.user.first_name
        )


def test_email_confirmation_attaches_html_alternative() -> None:
    """_email_confirmation attaches a non-empty text/html alternative."""
    match = MatchFactory.create(accepted=True)
    assert match.ambassador_registration is not None
    assert match.referee_registration is not None
    _email_confirmation(match.ambassador_registration, match.referee_registration)

    html_alternatives = [
        content
        for content, mimetype in mail.outbox[0].alternatives
        if mimetype == "text/html"
    ]
    assert len(html_alternatives) == 1
    assert html_alternatives[0].strip()


def test_email_confirmation_respects_preferred_language() -> None:
    """_email_confirmation renders each email under the recipient's language."""
    ambassador_reg = RegistrationFactory.create(
        role=Registration.Role.AMBASSADOR,
        prior_pass=Registration.PriorPass.SEASONAL,
        preferred_language="fr",
    )
    referee_reg = RegistrationFactory.create(
        referee=True,
        preferred_language="en",
    )
    MatchFactory.create(
        accepted=True,
        ambassador_registration=ambassador_reg,
        referee_registration=referee_reg,
    )
    _email_confirmation(ambassador_reg, referee_reg)
    _email_confirmation(referee_reg, ambassador_reg)
    assert len(mail.outbox) == 2


# ---------------------------------------------------------------------------
# accept_match
# ---------------------------------------------------------------------------


def test_accept_match_first_accept_goes_pending_notifies_waiting_partner() -> None:
    """First accept transitions match to PENDING and nudges the waiting partner.

    VERB-44: the first accept moves the match PROPOSED → PENDING. VERB-92: the
    party who has not yet responded receives a partner-accepted notification (no
    confirmed/PII email — that only fires on mutual accept).
    """
    ambassador_reg = RegistrationFactory.create()
    referee_reg = RegistrationFactory.create(referee=True)
    match = MatchFactory.create(
        ambassador_registration=ambassador_reg,
        referee_registration=referee_reg,
    )
    with TestCase.captureOnCommitCallbacks(execute=True):
        result = accept_match(match, ambassador_reg)

    assert result.status == Match.Status.PENDING
    # The waiting referee is notified that the ambassador accepted.
    assert len(mail.outbox) == 1
    assert mail.outbox[0].to == [referee_reg.user.email]


def test_accept_match_second_accept_transitions_accepted_and_sends_email() -> None:
    """Second accept transitions match to ACCEPTED and sends confirmed emails.

    VERB-44: Registration statuses remain VERIFIED after the match is accepted.
    """
    ambassador_reg = RegistrationFactory.create()
    referee_reg = RegistrationFactory.create(referee=True)
    match = MatchFactory.create(
        ambassador_registration=ambassador_reg,
        referee_registration=referee_reg,
    )

    # First accept — ambassador goes to PENDING; the referee is nudged (VERB-92).
    with TestCase.captureOnCommitCallbacks(execute=True):
        accept_match(match, ambassador_reg)

    match.refresh_from_db()
    assert match.status == Match.Status.PENDING
    assert len(mail.outbox) == 1  # partner-accepted nudge to the referee
    mail.outbox.clear()

    # Second accept — referee triggers the full transition.
    with TestCase.captureOnCommitCallbacks(execute=True):
        result = accept_match(match, referee_reg)

    assert result.status == Match.Status.ACCEPTED

    # VERB-44: registrations remain VERIFIED — no longer flipped to CONFIRMED.
    ambassador_reg.refresh_from_db()
    referee_reg.refresh_from_db()
    assert ambassador_reg.status == Registration.Status.VERIFIED
    assert referee_reg.status == Registration.Status.VERIFIED

    # Two confirmed emails sent (one per party).
    assert len(mail.outbox) == 2


# ---------------------------------------------------------------------------
# withdraw_acceptance (VERB-43 / VERB-44)
# ---------------------------------------------------------------------------


def test_withdraw_acceptance_clears_ambassador_timestamp_and_goes_proposed() -> None:
    """Ambassador withdraws: ambassador_accepted_at cleared, status → PROPOSED.

    VERB-44: withdraw_acceptance requires the match to be in PENDING state.
    The first accept has already transitioned it PROPOSED → PENDING.
    """
    ambassador_reg = RegistrationFactory.create()
    referee_reg = RegistrationFactory.create(referee=True)
    match = MatchFactory.create(
        ambassador_registration=ambassador_reg,
        referee_registration=referee_reg,
    )
    record_acceptance(match, ambassador_reg)
    match.refresh_from_db()
    assert match.status == Match.Status.PENDING
    assert match.ambassador_accepted_at is not None

    result = withdraw_acceptance(match, ambassador_reg)

    result.refresh_from_db()
    assert result.status == Match.Status.PROPOSED
    assert result.ambassador_accepted_at is None
    assert result.referee_accepted_at is None


def test_withdraw_acceptance_clears_referee_timestamp_and_goes_proposed() -> None:
    """Referee withdraws from PENDING → referee_accepted_at cleared, status PROPOSED."""
    ambassador_reg = RegistrationFactory.create()
    referee_reg = RegistrationFactory.create(referee=True)
    match = MatchFactory.create(
        ambassador_registration=ambassador_reg,
        referee_registration=referee_reg,
    )
    record_acceptance(match, referee_reg)
    match.refresh_from_db()
    assert match.status == Match.Status.PENDING
    assert match.referee_accepted_at is not None

    result = withdraw_acceptance(match, referee_reg)

    result.refresh_from_db()
    assert result.status == Match.Status.PROPOSED
    assert result.referee_accepted_at is None


def test_withdraw_acceptance_applies_no_penalty_and_writes_one_log() -> None:
    """Withdraw is penalty-free and writes one PENDING→PROPOSED log row."""
    ambassador_reg = RegistrationFactory.create(priority=0)
    referee_reg = RegistrationFactory.create(referee=True)
    match = MatchFactory.create(
        ambassador_registration=ambassador_reg,
        referee_registration=referee_reg,
    )
    # First accept transitions PROPOSED → PENDING (one log row).
    record_acceptance(match, ambassador_reg)
    assert StateTransitionLog.objects.count() == 1

    # Withdraw transitions PENDING → PROPOSED (one more log row).
    withdraw_acceptance(match, ambassador_reg)

    ambassador_reg.refresh_from_db()
    # No re-queue, no penalty — the registration is unchanged.
    assert ambassador_reg.status == Registration.Status.VERIFIED
    assert ambassador_reg.priority == 0
    # Total log rows: PROPOSED→PENDING (first accept) + PENDING→PROPOSED (withdraw).
    logs = list(StateTransitionLog.objects.order_by("pk"))
    assert len(logs) == 2
    assert logs[1].state_before == Match.Status.PENDING
    assert logs[1].state_after == Match.Status.PROPOSED


def test_withdraw_acceptance_raises_for_non_pending_match() -> None:
    """withdraw_acceptance raises StateTransitionError if match.status != PENDING."""
    ambassador_reg = RegistrationFactory.create()
    referee_reg = RegistrationFactory.create(referee=True)
    match = MatchFactory.create(
        ambassador_registration=ambassador_reg,
        referee_registration=referee_reg,
        status=Match.Status.ACCEPTED,
    )
    with pytest.raises(StateTransitionError) as exc_info:
        withdraw_acceptance(match, ambassador_reg)

    assert exc_info.value.current == Match.Status.ACCEPTED
    assert exc_info.value.proposed == Match.Status.PROPOSED


def test_withdraw_acceptance_raises_when_side_has_not_accepted() -> None:
    """withdraw_acceptance raises StateTransitionError if the caller never accepted."""
    ambassador_reg = RegistrationFactory.create()
    referee_reg = RegistrationFactory.create(referee=True)
    # Create a PENDING match where only the referee has accepted.
    match = MatchFactory.create(
        ambassador_registration=ambassador_reg,
        referee_registration=referee_reg,
        status=Match.Status.PENDING,
        referee_accepted_at=datetime(2026, 9, 2, 10, 0, 0, tzinfo=UTC),
    )
    # Ambassador has not accepted; nothing to withdraw.
    with pytest.raises(StateTransitionError) as exc_info:
        withdraw_acceptance(match, ambassador_reg)

    assert exc_info.value.current == Match.Status.PENDING
    assert exc_info.value.proposed == Match.Status.PROPOSED


# ---------------------------------------------------------------------------
# decline_match
# ---------------------------------------------------------------------------


def test_decline_match_transitions_to_declined() -> None:
    """decline_match transitions the match to DECLINED."""
    ambassador_reg = RegistrationFactory.create()
    referee_reg = RegistrationFactory.create(referee=True)
    match = MatchFactory.create(
        ambassador_registration=ambassador_reg,
        referee_registration=referee_reg,
    )
    result = decline_match(match, ambassador_reg)
    result.refresh_from_db()
    assert result.status == Match.Status.DECLINED


def test_decline_match_pauses_decliner_and_requeues_other_to_front() -> None:
    """decline_match pauses the decliner and re-queues the other to the front.

    VERB-74: the decliner is paused (not deleted) and the other party is
    re-queued to the front.
    """
    ambassador_reg = RegistrationFactory.create(priority=0)
    ambassador_user_pk = ambassador_reg.user.pk
    referee_reg = RegistrationFactory.create(referee=True, priority=0)
    match = MatchFactory.create(
        ambassador_registration=ambassador_reg,
        referee_registration=referee_reg,
    )

    decline_match(match, ambassador_reg)

    # Decliner (ambassador) is PAUSED; User and Registration rows are retained.
    ambassador_reg.refresh_from_db()
    assert ambassador_reg.status == Registration.Status.PAUSED
    assert User.objects.filter(pk=ambassador_user_pk).exists()
    assert Registration.objects.filter(pk=ambassador_reg.pk).exists()

    # Other party (referee) re-queued to front: status VERIFIED, priority incremented.
    referee_reg.refresh_from_db()
    assert referee_reg.status == Registration.Status.VERIFIED
    assert referee_reg.priority == 1

    # Match survives with both FKs intact (CASCADE, non-null — VERB-74).
    match.refresh_from_db()
    assert match.status == Match.Status.DECLINED
    assert match.ambassador_registration_id == ambassador_reg.pk


def test_decline_match_by_referee_pauses_referee_and_requeues_ambassador() -> None:
    """decline_match by the referee side pauses referee and re-queues ambassador."""
    ambassador_reg = RegistrationFactory.create(priority=5)
    referee_reg = RegistrationFactory.create(referee=True, priority=5)
    referee_user_pk = referee_reg.user.pk
    match = MatchFactory.create(
        ambassador_registration=ambassador_reg,
        referee_registration=referee_reg,
    )

    decline_match(match, referee_reg)

    # Decliner (referee) is PAUSED; row retained.
    referee_reg.refresh_from_db()
    assert referee_reg.status == Registration.Status.PAUSED
    assert User.objects.filter(pk=referee_user_pk).exists()

    # Ambassador (other side) re-queued to front: priority 5 → 6.
    ambassador_reg.refresh_from_db()
    assert ambassador_reg.status == Registration.Status.VERIFIED
    assert ambassador_reg.priority == 6

    # Match survives with FK intact.
    match.refresh_from_db()
    assert match.status == Match.Status.DECLINED
    assert match.referee_registration_id == referee_reg.pk


def test_decline_match_from_pending_by_referee_requeues_ambassador_to_front() -> None:
    """Referee declines a PENDING match (ambassador already accepted).

    VERB-44/74: the match is in PENDING state (one-sided accept). When the referee
    declines, the accepting party (ambassador) must be re-queued to the FRONT
    (priority +1, status VERIFIED). The decliner (referee) is paused.
    """
    ambassador_reg = RegistrationFactory.create(priority=0)
    referee_reg = RegistrationFactory.create(referee=True, priority=0)
    # PENDING: ambassador already accepted.
    match = MatchFactory.create(
        ambassador_registration=ambassador_reg,
        referee_registration=referee_reg,
        status=Match.Status.PENDING,
        ambassador_accepted_at=datetime(2026, 9, 1, 10, 0, 0, tzinfo=UTC),
    )

    decline_match(match, referee_reg)

    # Match is DECLINED.
    match.refresh_from_db()
    assert match.status == Match.Status.DECLINED

    # Decliner (referee) is PAUSED, not deleted.
    referee_reg.refresh_from_db()
    assert referee_reg.status == Registration.Status.PAUSED

    # Ambassador (kept-faith side) re-queued to the FRONT: priority 0 → 1.
    ambassador_reg.refresh_from_db()
    assert ambassador_reg.status == Registration.Status.VERIFIED
    assert ambassador_reg.priority == 1


def test_decline_match_notifies_requeued_partner_only() -> None:
    """decline_match emails the re-queued partner and no one else (VERB-92).

    The decliner is not emailed; only the kept-faith party who was returned to
    the front of the queue receives a (PII-free) re-queued notice.
    """
    ambassador_reg = RegistrationFactory.create(priority=0)
    referee_reg = RegistrationFactory.create(referee=True, priority=0)
    match = MatchFactory.create(
        ambassador_registration=ambassador_reg,
        referee_registration=referee_reg,
    )

    with TestCase.captureOnCommitCallbacks(execute=True):
        decline_match(match, ambassador_reg)

    # Only the re-queued referee is notified; the decliner receives nothing.
    assert len(mail.outbox) == 1
    assert mail.outbox[0].to == [referee_reg.user.email]


# ---------------------------------------------------------------------------
# report_no_show (VERB-21 / VERB-44: ACCEPTED → CANCELLED)
# ---------------------------------------------------------------------------


def test_report_no_show_transitions_match_to_cancelled() -> None:
    """report_no_show transitions match ACCEPTED → CANCELLED."""
    ambassador_reg = RegistrationFactory.create(priority=0)
    referee_reg = RegistrationFactory.create(referee=True, priority=0)
    match = MatchFactory.create(
        accepted=True,
        ambassador_registration=ambassador_reg,
        referee_registration=referee_reg,
    )

    result = report_no_show(match, ambassador_reg)

    result.refresh_from_db()
    assert result.status == Match.Status.CANCELLED
    assert result.no_show_reported_by == Match.Side.AMBASSADOR
    assert result.no_show_reported_at is not None


def test_report_no_show_no_show_reported_at_is_tz_aware() -> None:
    """report_no_show sets a tz-aware no_show_reported_at timestamp."""
    match = MatchFactory.create(accepted=True)
    before = timezone.now()
    result = report_no_show(match, match.ambassador_registration)
    after = timezone.now()

    result.refresh_from_db()
    assert result.no_show_reported_at is not None
    assert result.no_show_reported_at.tzinfo is not None
    assert before <= result.no_show_reported_at <= after


def test_report_no_show_by_referee_sets_referee_side() -> None:
    """report_no_show by the referee side records no_show_reported_by=REFEREE."""
    match = MatchFactory.create(accepted=True)

    result = report_no_show(match, match.referee_registration)

    result.refresh_from_db()
    assert result.no_show_reported_by == Match.Side.REFEREE


def test_report_no_show_reporter_requeued_to_front() -> None:
    """Reporter (kept-faith party) is re-queued to the front (VERIFIED, priority +1)."""
    ambassador_reg = RegistrationFactory.create(priority=3)
    referee_reg = RegistrationFactory.create(referee=True, priority=0)
    match = MatchFactory.create(
        accepted=True,
        ambassador_registration=ambassador_reg,
        referee_registration=referee_reg,
    )

    report_no_show(match, ambassador_reg)

    ambassador_reg.refresh_from_db()
    assert ambassador_reg.status == Registration.Status.VERIFIED
    assert ambassador_reg.priority == 4  # 3 + 1


def test_report_no_show_accused_suspended() -> None:
    """The accused party is SUSPENDED (no flake_count, VERB-74)."""
    ambassador_reg = RegistrationFactory.create()
    referee_reg = RegistrationFactory.create(referee=True)
    match = MatchFactory.create(
        accepted=True,
        ambassador_registration=ambassador_reg,
        referee_registration=referee_reg,
    )

    report_no_show(match, ambassador_reg)

    # Reporter is ambassador; accused is referee.
    referee_reg.refresh_from_db()
    assert referee_reg.status == Registration.Status.SUSPENDED


def test_report_no_show_writes_two_transition_log_rows() -> None:
    """report_no_show writes exactly two StateTransitionLog rows for the objects.

    One for Match.status (ACCEPTED → CANCELLED) and one for the accused
    Registration.status (VERIFIED → SUSPENDED). The reporter's VERIFIED →
    VERIFIED transition is not logged (consistent with the decline path).

    The count is scoped to the specific match and accused-registration PKs so
    the assertion holds even when other rows exist in the table (e.g. from
    earlier tests in the same session).
    """
    from django.contrib.contenttypes.models import ContentType

    ambassador_reg = RegistrationFactory.create(priority=0)
    referee_reg = RegistrationFactory.create(referee=True, priority=0)
    match = MatchFactory.create(
        accepted=True,
        ambassador_registration=ambassador_reg,
        referee_registration=referee_reg,
    )

    report_no_show(match, ambassador_reg)

    match_ct = ContentType.objects.get_for_model(Match)
    reg_ct = ContentType.objects.get_for_model(Registration)

    # Filter to only the rows for this match and the accused registration.
    relevant_logs = list(
        StateTransitionLog.objects.filter(
            content_type_id__in=[match_ct.pk, reg_ct.pk],
            object_id__in=[match.pk, referee_reg.pk],
        ).order_by("pk")
    )
    assert len(relevant_logs) == 2

    # One log for Match.status.
    match_log = next(
        (log for log in relevant_logs if log.content_type_id == match_ct.pk),
        None,
    )
    assert match_log is not None
    assert match_log.object_id == match.pk
    assert match_log.state_before == Match.Status.ACCEPTED
    assert match_log.state_after == Match.Status.CANCELLED

    # One log for the accused (referee) Registration.status.
    reg_log = next(
        (log for log in relevant_logs if log.content_type_id == reg_ct.pk),
        None,
    )
    assert reg_log is not None
    assert reg_log.object_id == referee_reg.pk
    assert reg_log.state_before == Registration.Status.VERIFIED
    assert reg_log.state_after == Registration.Status.SUSPENDED


def test_report_no_show_notifies_both_accused_and_reporter() -> None:
    """report_no_show emails the accused (no-show notice) and the reporter (re-queued).

    VERB-92: both parties are notified on the CANCELLED transition — the accused
    that they were reported, the reporter that they have been returned to the
    front of the queue.
    """
    ambassador_reg = RegistrationFactory.create(phone="+41790001111")
    referee_reg = RegistrationFactory.create(referee=True, phone="+41790002222")
    match = MatchFactory.create(
        accepted=True,
        ambassador_registration=ambassador_reg,
        referee_registration=referee_reg,
    )

    with TestCase.captureOnCommitCallbacks(execute=True):
        report_no_show(match, ambassador_reg)

    assert len(mail.outbox) == 2
    recipients = {message.to[0] for message in mail.outbox}
    assert recipients == {referee_reg.user.email, ambassador_reg.user.email}


def test_report_no_show_email_contains_no_reporter_pii() -> None:
    """The no-show email must not contain any reporter PII (Invariant 1).

    The reporter (ambassador) is built with known non-empty PII values so the
    assertions are real checks rather than vacuously true for empty strings.
    """
    reporter_user = UserFactory.create(
        first_name="Reporter",
        last_name="Jones",
        email="reporter.jones@example.com",
    )
    ambassador_reg = RegistrationFactory.create(
        user=reporter_user,
        role=Registration.Role.AMBASSADOR,
        prior_pass=Registration.PriorPass.SEASONAL,
        phone="+41790001111",
    )
    referee_reg = RegistrationFactory.create(referee=True, phone="+41790002222")
    match = MatchFactory.create(
        accepted=True,
        ambassador_registration=ambassador_reg,
        referee_registration=referee_reg,
    )

    with TestCase.captureOnCommitCallbacks(execute=True):
        report_no_show(match, ambassador_reg)

    # Select the accused's (referee's) message; the reporter also gets a
    # re-queued notice (VERB-92), so index alone is not a safe selector.
    accused_message = next(
        message for message in mail.outbox if message.to == [referee_reg.user.email]
    )
    body = accused_message.body
    # Each reporter PII value must be absent from the accused's email body.
    assert ambassador_reg.phone not in body
    assert ambassador_reg.user.email not in body
    assert ambassador_reg.user.first_name not in body
    assert ambassador_reg.user.last_name not in body


def test_report_no_show_email_respects_accused_preferred_language() -> None:
    """The no-show handler renders the accused's email under their own language."""
    accused_reg = RegistrationFactory.create(referee=True, preferred_language="fr")
    ambassador_reg = RegistrationFactory.create(preferred_language="en")
    match = MatchFactory.create(
        accepted=True,
        ambassador_registration=ambassador_reg,
        referee_registration=accused_reg,
    )

    with TestCase.captureOnCommitCallbacks(execute=True):
        report_no_show(match, ambassador_reg)

    # Both parties are notified (VERB-92); the accused's message is rendered
    # under their preferred_language. We assert delivery rather than translated
    # copy: the test env does not compile message catalogues, so gettext falls
    # back to the source string.
    assert len(mail.outbox) == 2
    assert any(message.to == [accused_reg.user.email] for message in mail.outbox)
    assert mail.outbox[0].to == [accused_reg.user.email]


def test_report_no_show_raises_on_non_accepted_match() -> None:
    """report_no_show raises StateTransitionError if match.status != ACCEPTED."""
    match = MatchFactory.create(status=Match.Status.PROPOSED)

    with pytest.raises(StateTransitionError) as exc_info:
        report_no_show(match, match.ambassador_registration)

    assert exc_info.value.current == Match.Status.PROPOSED
    assert exc_info.value.proposed == Match.Status.CANCELLED


def test_report_no_show_raises_if_already_reported() -> None:
    """report_no_show raises StateTransitionError if already reported.

    Build an ACCEPTED match that already has no_show_reported_by set directly
    so the status guard passes but the already-reported guard fires.
    """
    match = MatchFactory.create(
        accepted=True,
        no_show_reported_by=Match.Side.AMBASSADOR,
    )

    with pytest.raises(StateTransitionError) as exc_info:
        report_no_show(match, match.referee_registration)

    assert exc_info.value.current == Match.Status.ACCEPTED
    assert exc_info.value.proposed == Match.Status.CANCELLED


def test_report_no_show_raises_on_declined_match() -> None:
    """report_no_show raises StateTransitionError on a DECLINED match."""
    match = MatchFactory.create(declined=True)

    with pytest.raises(StateTransitionError) as exc_info:
        report_no_show(match, match.ambassador_registration)

    assert exc_info.value.current == Match.Status.DECLINED
    assert exc_info.value.proposed == Match.Status.CANCELLED


def test_report_no_show_returns_updated_match() -> None:
    """report_no_show returns the updated Match instance."""
    match = MatchFactory.create(accepted=True)

    result = report_no_show(match, match.ambassador_registration)

    assert result.status == Match.Status.CANCELLED
    assert result.pk == match.pk


# ---------------------------------------------------------------------------
# _email_no_show (matching.side_effects) — the match_no_show accused-side
# handler's render helper (VERB-21).
# ---------------------------------------------------------------------------


def test_email_no_show_sends_one_email() -> None:
    """_email_no_show sends exactly one email to the accused."""
    match = MatchFactory.create(accepted=True)
    accused = match.referee_registration

    _email_no_show(accused)

    assert len(mail.outbox) == 1
    assert mail.outbox[0].to == [accused.user.email]


def test_email_no_show_contains_no_reporter_pii() -> None:
    """_email_no_show must not contain any reporter PII (Invariant 1)."""
    ambassador_reg = RegistrationFactory.create(phone="+41790003333")
    referee_reg = RegistrationFactory.create(referee=True, phone="+41790004444")
    MatchFactory.create(
        accepted=True,
        ambassador_registration=ambassador_reg,
        referee_registration=referee_reg,
    )

    # Reporter is ambassador; accused is referee.
    _email_no_show(referee_reg)

    body = mail.outbox[0].body
    # Reporter's PII must not appear in the accused's email.
    assert ambassador_reg.phone not in body
    assert ambassador_reg.user.email not in body


def test_email_no_show_respects_preferred_language() -> None:
    """_email_no_show renders under the accused's preferred_language."""
    accused_reg = RegistrationFactory.create(referee=True, preferred_language="fr")
    MatchFactory.create(
        accepted=True,
        referee_registration=accused_reg,
    )

    _email_no_show(accused_reg)

    # Assert delivery to the accused rather than translated copy: the test env
    # does not compile message catalogues, so gettext returns the source string.
    assert len(mail.outbox) == 1
    assert mail.outbox[0].to == [accused_reg.user.email]


# ---------------------------------------------------------------------------
# queue_position
# ---------------------------------------------------------------------------


def test_queue_position_returns_none_for_non_verified_registration() -> None:
    """queue_position returns None when the registration is not VERIFIED."""
    reg = RegistrationFactory.create(
        role=Registration.Role.AMBASSADOR,
        prior_pass=Registration.PriorPass.SEASONAL,
        status=Registration.Status.SUSPENDED,
    )
    assert queue_position(reg) is None


def test_queue_position_returns_none_for_unverified_registration() -> None:
    """queue_position returns None when the registration is UNVERIFIED."""
    reg = RegistrationFactory.create(
        role=Registration.Role.AMBASSADOR,
        prior_pass=Registration.PriorPass.SEASONAL,
        status=Registration.Status.UNVERIFIED,
    )
    assert queue_position(reg) is None


def test_queue_position_returns_one_for_sole_eligible_participant() -> None:
    """queue_position returns 1 when the registration is the only one in the pool."""
    reg = RegistrationFactory.create(
        role=Registration.Role.AMBASSADOR,
        prior_pass=Registration.PriorPass.SEASONAL,
        status=Registration.Status.VERIFIED,
        priority=0,
    )
    assert queue_position(reg) == 1


def test_queue_position_ranks_by_priority_desc_then_created_at_asc() -> None:
    """queue_position reflects -priority, created_at ordering.

    Three ambassadors: high-priority, mid-priority (our subject), low-priority.
    The subject is second.
    """
    base_time = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)

    high = RegistrationFactory.create(
        role=Registration.Role.AMBASSADOR,
        prior_pass=Registration.PriorPass.SEASONAL,
        priority=10,
    )
    Registration.objects.filter(pk=high.pk).update(created_at=base_time)

    subject = RegistrationFactory.create(
        role=Registration.Role.AMBASSADOR,
        prior_pass=Registration.PriorPass.ANNUAL,
        priority=5,
    )
    Registration.objects.filter(pk=subject.pk).update(
        created_at=base_time + timedelta(hours=1)
    )

    low = RegistrationFactory.create(
        role=Registration.Role.AMBASSADOR,
        prior_pass=Registration.PriorPass.MONT4,
        priority=0,
    )
    Registration.objects.filter(pk=low.pk).update(
        created_at=base_time + timedelta(hours=2)
    )

    # Reload all instances so in-memory created_at reflects the .update() calls.
    high.refresh_from_db()
    subject.refresh_from_db()
    low.refresh_from_db()

    assert queue_position(high) == 1
    assert queue_position(subject) == 2
    assert queue_position(low) == 3


def test_queue_position_equal_priority_fifo_on_created_at() -> None:
    """queue_position uses created_at ascending as the tiebreak for equal priority."""
    base_time = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)

    earlier = RegistrationFactory.create(
        role=Registration.Role.AMBASSADOR,
        prior_pass=Registration.PriorPass.SEASONAL,
        priority=0,
    )
    Registration.objects.filter(pk=earlier.pk).update(created_at=base_time)

    later = RegistrationFactory.create(
        role=Registration.Role.AMBASSADOR,
        prior_pass=Registration.PriorPass.SEASONAL,
        priority=0,
    )
    Registration.objects.filter(pk=later.pk).update(
        created_at=base_time + timedelta(hours=1)
    )

    earlier.refresh_from_db()
    later.refresh_from_db()

    assert queue_position(earlier) == 1
    assert queue_position(later) == 2


def test_queue_position_role_scoped_ambassador_ignores_referees() -> None:
    """An ambassador's queue position is computed only from the ambassador pool.

    Verified referees must not affect the ambassador's ordinal.
    """
    # One verified referee in the pool.
    RegistrationFactory.create(referee=True, priority=99)

    ambassador = RegistrationFactory.create(
        role=Registration.Role.AMBASSADOR,
        prior_pass=Registration.PriorPass.SEASONAL,
        priority=0,
    )

    # Ambassador should be 1st in the ambassador pool regardless of referee count.
    assert queue_position(ambassador) == 1


def test_queue_position_role_scoped_referee_ignores_ambassadors() -> None:
    """A referee's queue position is computed only from the referee pool.

    Verified ambassadors must not affect the referee's ordinal.
    """
    # One verified ambassador in the pool.
    RegistrationFactory.create(
        role=Registration.Role.AMBASSADOR,
        prior_pass=Registration.PriorPass.SEASONAL,
        priority=99,
    )

    referee = RegistrationFactory.create(referee=True, priority=0)

    # Referee should be 1st in the referee pool regardless of ambassador count.
    assert queue_position(referee) == 1


def test_queue_position_returns_none_for_ineligible_verified_ambassador() -> None:
    """queue_position returns None for a VERIFIED ambassador with no prior pass.

    An ambassador with prior_pass=NONE is not in the eligible pool even though
    their status is VERIFIED (e.g. created via admin, bypassing the form
    validation). The function must return None rather than a misleading ordinal.
    """
    reg = RegistrationFactory.create(
        role=Registration.Role.AMBASSADOR,
        prior_pass=Registration.PriorPass.NONE,
        status=Registration.Status.VERIFIED,
    )
    assert queue_position(reg) is None


def test_queue_position_returns_none_for_verified_reg_with_active_match() -> None:
    """queue_position returns None for a VERIFIED registration holding an active match.

    VERB-44: pool availability is controlled by _without_active_match. A
    registration with an active match is excluded from the eligible pool, so
    queue_position must return None rather than an ordinal.
    """
    reg = RegistrationFactory.create(
        role=Registration.Role.AMBASSADOR,
        prior_pass=Registration.PriorPass.SEASONAL,
        status=Registration.Status.VERIFIED,
    )
    MatchFactory.create(ambassador_registration=reg, status=Match.Status.PROPOSED)
    # The reg is VERIFIED but has an active match — it is not in the eligible pool.
    assert queue_position(reg) is None


# ---------------------------------------------------------------------------
# total_accepted_matches
# ---------------------------------------------------------------------------


def test_total_accepted_matches_returns_zero_with_no_matches() -> None:
    """total_accepted_matches returns 0 when no matches exist."""
    assert total_accepted_matches() == 0


def test_total_accepted_matches_counts_only_accepted() -> None:
    """total_accepted_matches counts only ACCEPTED matches, not other statuses.

    Includes a CANCELLED match (a no-show that was previously ACCEPTED) to
    confirm it does not inflate the count — CANCELLED is a distinct terminal
    status and must not be conflated with ACCEPTED.
    """
    MatchFactory.create(accepted=True)
    MatchFactory.create(accepted=True)
    MatchFactory.create()  # PROPOSED
    MatchFactory.create(declined=True)
    MatchFactory.create(cancelled=True)  # was ACCEPTED, then reported as no-show

    assert total_accepted_matches() == 2


# ---------------------------------------------------------------------------
# queue_snapshot (VERB-145)
# ---------------------------------------------------------------------------


def test_queue_snapshot_all_zero_with_no_registrations() -> None:
    """queue_snapshot returns an all-zero snapshot when the pool is empty."""
    snapshot = queue_snapshot()
    assert snapshot.ambassadors_unmatched == 0
    assert snapshot.ambassadors_matched == 0
    assert snapshot.referees_unmatched == 0
    assert snapshot.referees_matched == 0


def test_queue_snapshot_counts_only_verified_registrations() -> None:
    """Only VERIFIED registrations feed the snapshot; other statuses are excluded.

    A non-VERIFIED registration (UNVERIFIED, PAUSED, WITHDRAWN, SUSPENDED) holds
    no active match, so if it were mistakenly counted it would inflate
    "unmatched" — asserting the totals stay at 1 per role confirms it is
    excluded altogether, not miscounted as matched.
    """
    RegistrationFactory.create(unverified=True)
    RegistrationFactory.create(paused=True)
    RegistrationFactory.create(status=Registration.Status.WITHDRAWN)
    RegistrationFactory.create(suspended=True)
    RegistrationFactory.create(status=Registration.Status.VERIFIED)

    RegistrationFactory.create(referee=True, unverified=True)
    RegistrationFactory.create(referee=True, paused=True)
    RegistrationFactory.create(referee=True, status=Registration.Status.WITHDRAWN)
    RegistrationFactory.create(referee=True, suspended=True)
    RegistrationFactory.create(referee=True, status=Registration.Status.VERIFIED)

    snapshot = queue_snapshot()

    assert snapshot.ambassadors_unmatched == 1
    assert snapshot.ambassadors_matched == 0
    assert snapshot.referees_unmatched == 1
    assert snapshot.referees_matched == 0


def test_queue_snapshot_proposed_match_counts_as_matched() -> None:
    """A PROPOSED match moves both its registrations into "matched"."""
    MatchFactory.create()  # PROPOSED by default

    snapshot = queue_snapshot()

    assert snapshot.ambassadors_unmatched == 0
    assert snapshot.ambassadors_matched == 1
    assert snapshot.referees_unmatched == 0
    assert snapshot.referees_matched == 1


def test_queue_snapshot_pending_match_counts_as_matched() -> None:
    """A PENDING (one-sided accept) match counts as matched, not unmatched."""
    MatchFactory.create(pending=True)

    snapshot = queue_snapshot()

    assert snapshot.ambassadors_matched == 1
    assert snapshot.referees_matched == 1


def test_queue_snapshot_accepted_match_counts_as_matched() -> None:
    """An ACCEPTED (mutual-accept) match counts as matched."""
    MatchFactory.create(accepted=True)

    snapshot = queue_snapshot()

    assert snapshot.ambassadors_matched == 1
    assert snapshot.referees_matched == 1


def test_queue_snapshot_declined_match_counts_as_unmatched() -> None:
    """A DECLINED match's registrations are unmatched — it holds no active match."""
    MatchFactory.create(declined=True)

    snapshot = queue_snapshot()

    assert snapshot.ambassadors_unmatched == 1
    assert snapshot.ambassadors_matched == 0
    assert snapshot.referees_unmatched == 1
    assert snapshot.referees_matched == 0


def test_queue_snapshot_cancelled_match_counts_as_unmatched() -> None:
    """A CANCELLED (post-accept no-show) match's registrations are unmatched."""
    MatchFactory.create(cancelled=True)

    snapshot = queue_snapshot()

    assert snapshot.ambassadors_unmatched == 1
    assert snapshot.ambassadors_matched == 0
    assert snapshot.referees_unmatched == 1
    assert snapshot.referees_matched == 0


def test_queue_snapshot_expired_match_counts_as_unmatched() -> None:
    """An EXPIRED (lapsed, no mutual accept) match's registrations are unmatched.

    No ``expired`` factory trait exists, so the status and a past ``expires_at``
    are set directly, matching how a real expired match is stored.
    """
    MatchFactory.create(
        status=Match.Status.EXPIRED, expires_at=datetime(2026, 9, 1, tzinfo=UTC)
    )

    snapshot = queue_snapshot()

    assert snapshot.ambassadors_unmatched == 1
    assert snapshot.ambassadors_matched == 0
    assert snapshot.referees_unmatched == 1
    assert snapshot.referees_matched == 0


def test_queue_snapshot_complement_invariant() -> None:
    """unmatched + matched always equals the VERIFIED total, per role.

    A mixed pool: one unmatched ambassador/referee pair, one PROPOSED match
    (matched), and one non-VERIFIED registration per role (excluded entirely).
    """
    RegistrationFactory.create(status=Registration.Status.VERIFIED)
    RegistrationFactory.create(referee=True, status=Registration.Status.VERIFIED)
    MatchFactory.create()  # PROPOSED — one matched ambassador + one matched referee
    RegistrationFactory.create(paused=True)
    RegistrationFactory.create(referee=True, paused=True)

    snapshot = queue_snapshot()

    ambassador_total = Registration.objects.verified().ambassadors().count()
    referee_total = Registration.objects.verified().referees().count()

    assert (
        snapshot.ambassadors_unmatched + snapshot.ambassadors_matched
        == ambassador_total
    )
    assert snapshot.referees_unmatched + snapshot.referees_matched == referee_total
    assert snapshot.ambassadors_unmatched == 1
    assert snapshot.ambassadors_matched == 1
    assert snapshot.referees_unmatched == 1
    assert snapshot.referees_matched == 1


# ---------------------------------------------------------------------------
# expire_lapsed_matches (VERB-74)
# ---------------------------------------------------------------------------


def test_expire_lapsed_matches_transitions_proposed_to_expired() -> None:
    """expire_lapsed_matches transitions a lapsed PROPOSED match to EXPIRED."""
    past = datetime(2020, 1, 1, tzinfo=UTC)
    match = MatchFactory.create(expires_at=past)
    count = expire_lapsed_matches(cutoff=timezone.now())
    match.refresh_from_db()
    assert match.status == Match.Status.EXPIRED
    assert count == 1


def test_expire_lapsed_matches_non_responder_gets_paused() -> None:
    """Non-responders (no *_accepted_at) are paused on expiry (VERB-74)."""
    past = datetime(2020, 1, 1, tzinfo=UTC)
    ambassador_reg = RegistrationFactory.create(priority=0)
    referee_reg = RegistrationFactory.create(referee=True, priority=0)
    MatchFactory.create(
        ambassador_registration=ambassador_reg,
        referee_registration=referee_reg,
        expires_at=past,
    )

    expire_lapsed_matches(cutoff=timezone.now())

    ambassador_reg.refresh_from_db()
    referee_reg.refresh_from_db()
    assert ambassador_reg.status == Registration.Status.PAUSED
    assert referee_reg.status == Registration.Status.PAUSED


def test_expire_lapsed_matches_faithful_party_requeued_to_front() -> None:
    """A party who accepted before expiry is re-queued to the front (priority +1)."""
    past = datetime(2020, 1, 1, tzinfo=UTC)
    accepted_at = datetime(2019, 12, 31, tzinfo=UTC)
    ambassador_reg = RegistrationFactory.create(priority=0)
    referee_reg = RegistrationFactory.create(referee=True, priority=0)
    MatchFactory.create(
        ambassador_registration=ambassador_reg,
        referee_registration=referee_reg,
        expires_at=past,
        ambassador_accepted_at=accepted_at,  # ambassador accepted; referee did not
    )

    expire_lapsed_matches(cutoff=timezone.now())

    ambassador_reg.refresh_from_db()
    referee_reg.refresh_from_db()
    assert ambassador_reg.status == Registration.Status.VERIFIED
    assert ambassador_reg.priority == 1  # re-queued to front
    assert referee_reg.status == Registration.Status.PAUSED  # non-responder


def test_expire_lapsed_matches_idempotent_on_second_run() -> None:
    """expire_lapsed_matches skips already-EXPIRED matches on a second run."""
    past = datetime(2020, 1, 1, tzinfo=UTC)
    MatchFactory.create(expires_at=past)

    first_count = expire_lapsed_matches(cutoff=timezone.now())
    second_count = expire_lapsed_matches(cutoff=timezone.now())

    assert first_count == 1
    assert second_count == 0


def test_expire_lapsed_matches_sends_notification_to_non_responders(
    mailoutbox: list,
) -> None:
    """A window-expired notification is sent to each non-responding party on commit."""
    from django.test import TestCase as DjangoTestCase

    past = datetime(2020, 1, 1, tzinfo=UTC)
    ambassador_reg = RegistrationFactory.create(priority=0)
    referee_reg = RegistrationFactory.create(referee=True, priority=0)
    MatchFactory.create(
        ambassador_registration=ambassador_reg,
        referee_registration=referee_reg,
        expires_at=past,
    )

    with DjangoTestCase.captureOnCommitCallbacks(execute=True):
        expire_lapsed_matches(cutoff=timezone.now())

    # Both parties are non-responders — each receives a notification.
    assert len(mailoutbox) == 2
    recipients = {msg.to[0] for msg in mailoutbox}
    assert ambassador_reg.user.email in recipients
    assert referee_reg.user.email in recipients


def test_expire_lapsed_matches_notifies_both_faithful_and_non_responder(
    mailoutbox: list,
) -> None:
    """Both parties are notified on expiry (VERB-92).

    The faithful party (re-queued) gets a re-queued notice; the non-responder
    (paused) gets the window-expired notice.
    """
    from django.test import TestCase as DjangoTestCase

    past = datetime(2020, 1, 1, tzinfo=UTC)
    accepted_at = datetime(2019, 12, 31, tzinfo=UTC)
    ambassador_reg = RegistrationFactory.create(priority=0)
    referee_reg = RegistrationFactory.create(referee=True, priority=0)
    MatchFactory.create(
        ambassador_registration=ambassador_reg,
        referee_registration=referee_reg,
        expires_at=past,
        ambassador_accepted_at=accepted_at,
    )

    with DjangoTestCase.captureOnCommitCallbacks(execute=True):
        expire_lapsed_matches(cutoff=timezone.now())

    # Both the faithful ambassador and the non-responding referee are notified.
    assert len(mailoutbox) == 2
    recipients = {msg.to[0] for msg in mailoutbox}
    assert recipients == {ambassador_reg.user.email, referee_reg.user.email}


# ---------------------------------------------------------------------------
# _email_window_expired (matching.side_effects) — VERB-74
# ---------------------------------------------------------------------------


def test_email_window_expired_sends_one_email() -> None:
    """_email_window_expired sends one email to the non-responder."""
    reg = RegistrationFactory.create()
    _email_window_expired(reg)
    assert len(mail.outbox) == 1
    assert mail.outbox[0].to == [reg.user.email]


def test_email_window_expired_includes_account_url() -> None:
    """The expiry email body includes the account detail URL so the user can rejoin."""

    reg = RegistrationFactory.create()
    _email_window_expired(reg)
    body = mail.outbox[0].body
    assert settings.BASE_URL in body
    assert "/account/" in body


def test_email_window_expired_respects_preferred_language() -> None:
    """_email_window_expired uses the recipient's preferred_language."""
    reg = RegistrationFactory.create(preferred_language="fr")
    _email_window_expired(reg)
    # Assert delivery — test env has no compiled catalogues, so only check To:.
    assert len(mail.outbox) == 1
    assert mail.outbox[0].to == [reg.user.email]


def test_email_window_expired_offers_cancel_and_refund() -> None:
    """The expiry email mentions cancelling for a refund (VERB-88)."""
    reg = RegistrationFactory.create()
    _email_window_expired(reg)
    body = mail.outbox[0].body
    assert "cancel" in body
    assert "refund" in body


# ---------------------------------------------------------------------------
# _email_requeued (matching.side_effects) — VERB-92
# ---------------------------------------------------------------------------


def test_email_requeued_sends_one_email() -> None:
    """_email_requeued sends one email to the re-queued party."""
    reg = RegistrationFactory.create()
    _email_requeued(reg)
    assert len(mail.outbox) == 1
    assert mail.outbox[0].to == [reg.user.email]


def test_email_requeued_contains_no_counterpart_pii() -> None:
    """The re-queued notice must reveal no counterpart PII or reason (Invariant 1).

    The email is addressed to the faithful party; it must not carry any contact
    detail of the (unnamed) counterpart. There is no counterpart on the
    registration itself, so we assert the neutral copy carries no email/phone
    markers rather than a specific person's PII.
    """
    reg = RegistrationFactory.create(preferred_language="en")
    _email_requeued(reg)
    body = mail.outbox[0].body
    # Neutral reassurance copy — no address markers that would imply PII.
    assert "@" not in body
    assert "+41" not in body


def test_email_requeued_respects_preferred_language() -> None:
    """_email_requeued renders under the recipient's preferred_language."""
    reg = RegistrationFactory.create(preferred_language="fr")
    _email_requeued(reg)
    # Assert delivery — test env has no compiled catalogues, so only check To:.
    assert len(mail.outbox) == 1
    assert mail.outbox[0].to == [reg.user.email]


# ---------------------------------------------------------------------------
# _email_partner_accepted (matching.side_effects) — VERB-92
# ---------------------------------------------------------------------------


def test_email_partner_accepted_sends_one_email_to_waiting_party() -> None:
    """_email_partner_accepted emails only the waiting party."""
    ambassador_reg = RegistrationFactory.create()
    referee_reg = RegistrationFactory.create(referee=True)
    match = MatchFactory.create(
        ambassador_registration=ambassador_reg,
        referee_registration=referee_reg,
        status=Match.Status.PENDING,
    )
    _email_partner_accepted(referee_reg, match)
    assert len(mail.outbox) == 1
    assert mail.outbox[0].to == [referee_reg.user.email]


def test_email_partner_accepted_includes_match_link_no_pii() -> None:
    """The nudge carries the recipient's signed match link but no counterpart PII."""
    from accounts.tokens import read_match_access_token

    ambassador_reg = RegistrationFactory.create(phone="+41790001111")
    referee_reg = RegistrationFactory.create(referee=True)
    match = MatchFactory.create(
        ambassador_registration=ambassador_reg,
        referee_registration=referee_reg,
        status=Match.Status.PENDING,
    )
    _email_partner_accepted(referee_reg, match)
    message = mail.outbox[0]
    body = message.body
    html_body = next(
        content for content, mimetype in message.alternatives if mimetype == "text/html"
    )

    # The link is scoped to the recipient (the referee), not the counterpart.
    assert "/match/" in body
    assert "/match/" in html_body
    # No counterpart contact PII is disclosed before mutual accept (Invariant 1),
    # in either the text or the HTML part.
    assert ambassador_reg.phone not in body
    assert ambassador_reg.user.email not in body
    assert ambassador_reg.phone not in html_body
    assert ambassador_reg.user.email not in html_body

    # The embedded token decodes to (match, recipient).
    token = body.split("/match/")[1].split("/")[0]
    decoded = read_match_access_token(token)
    assert decoded == (match.pk, referee_reg.pk)


def test_email_partner_accepted_attaches_html_alternative() -> None:
    """_email_partner_accepted attaches a non-empty text/html alternative."""
    ambassador_reg = RegistrationFactory.create()
    referee_reg = RegistrationFactory.create(referee=True)
    match = MatchFactory.create(
        ambassador_registration=ambassador_reg,
        referee_registration=referee_reg,
        status=Match.Status.PENDING,
    )
    _email_partner_accepted(referee_reg, match)

    html_alternatives = [
        content
        for content, mimetype in mail.outbox[0].alternatives
        if mimetype == "text/html"
    ]
    assert len(html_alternatives) == 1
    assert html_alternatives[0].strip()


# ---------------------------------------------------------------------------
# register_participant — geolocation fields (VERB-49)
# ---------------------------------------------------------------------------


def test_register_participant_stores_country_and_region() -> None:
    """register_participant persists registration_country and registration_region."""
    registration = register_participant(
        role=Registration.Role.AMBASSADOR,
        first_name="Ada",
        last_name="Lovelace",
        email="ada_geo@example.com",
        prior_pass=Registration.PriorPass.SEASONAL,
        registration_country="Switzerland",
        registration_region="Valais",
    )

    assert registration.registration_country == "Switzerland"
    assert registration.registration_region == "Valais"

    # Verify the values are persisted to the database.
    from_db = Registration.objects.get(pk=registration.pk)
    assert from_db.registration_country == "Switzerland"
    assert from_db.registration_region == "Valais"


def test_register_participant_geo_defaults_to_empty_strings() -> None:
    """register_participant defaults geo fields to empty strings when not passed."""
    registration = register_participant(
        role=Registration.Role.AMBASSADOR,
        first_name="Ada",
        last_name="Lovelace",
        email="ada_no_geo@example.com",
        prior_pass=Registration.PriorPass.SEASONAL,
    )

    assert registration.registration_country == ""
    assert registration.registration_region == ""
