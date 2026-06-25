# Tests for the matching service functions.

from datetime import UTC, datetime, timedelta

import pytest
from django.contrib.auth.models import User
from django.core import mail
from django.db import transaction
from django.test import TestCase, override_settings
from django.utils import timezone

from core.models import StateTransitionLog
from matching.models import Match, Registration
from matching.services import (
    accept_match,
    confirm_registration,
    decline_match,
    is_eligible_pair,
    is_registration_open,
    propose_match,
    record_acceptance,
    record_decline,
    record_flake_and_requeue,
    register_participant,
    report_no_show,
    requeue_to_back,
    requeue_to_front,
    send_match_confirmed_email,
    send_match_notification,
    send_no_show_notification,
    suspend_for_no_show,
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
        status=Registration.Status.WAITING,
    )
    referee = RegistrationFactory.create(
        referee=True,
        status=Registration.Status.WAITING,
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


def test_eligible_pair_rejects_non_waiting_ambassador() -> None:
    """is_eligible_pair rejects an ambassador who is not WAITING."""
    ambassador = RegistrationFactory.create(
        role=Registration.Role.AMBASSADOR,
        prior_pass=Registration.PriorPass.SEASONAL,
        status=Registration.Status.MATCHED,
    )
    referee = RegistrationFactory.create(referee=True)
    assert is_eligible_pair(ambassador, referee) is False


def test_eligible_pair_rejects_non_waiting_referee() -> None:
    """is_eligible_pair rejects a referee who is not WAITING."""
    ambassador = RegistrationFactory.create(
        role=Registration.Role.AMBASSADOR,
        prior_pass=Registration.PriorPass.SEASONAL,
    )
    referee = RegistrationFactory.create(
        referee=True,
        status=Registration.Status.MATCHED,
    )
    assert is_eligible_pair(ambassador, referee) is False


# ---------------------------------------------------------------------------
# propose_match
# ---------------------------------------------------------------------------


def test_propose_match_creates_match_for_ambassador_with_waiting_referee() -> None:
    """propose_match pairs an ambassador with the top waiting eligible referee."""
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

    ambassador.refresh_from_db()
    referee.refresh_from_db()
    assert ambassador.status == Registration.Status.MATCHED
    assert referee.status == Registration.Status.MATCHED


def test_propose_match_creates_match_for_referee_with_waiting_ambassador() -> None:
    """propose_match pairs a referee with the top waiting eligible ambassador."""
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
    # The Thyon referee is still waiting.
    thyon_referee.refresh_from_db()
    assert thyon_referee.status == Registration.Status.WAITING


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
    assert low_priority.status == Registration.Status.WAITING


def test_propose_match_sets_expires_at() -> None:
    """propose_match sets expires_at to now + CONTACT_WINDOW_HOURS."""
    from django.conf import settings

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
    assert later_referee.status == Registration.Status.WAITING


def test_propose_match_single_counterpart_matched_only_once() -> None:
    """A waiting counterpart can be matched by at most one registration.

    Two referees both attempt to match the same sole waiting ambassador.
    Exactly one match is created; the other referee remains WAITING.
    This is the deterministic equivalent of a concurrency safety test — both
    referees call propose_match sequentially; the select_for_update lock on the
    candidate set ensures the second call sees the ambassador already MATCHED.
    """
    RegistrationFactory.create(
        role=Registration.Role.AMBASSADOR,
        prior_pass=Registration.PriorPass.SEASONAL,
    )
    referee_one = RegistrationFactory.create(referee=True, priority=10)
    referee_two = RegistrationFactory.create(referee=True, priority=5)

    with transaction.atomic():
        match_one = propose_match(referee_one)
    # After the first match, the ambassador is MATCHED; the second call must
    # find no eligible counterpart.
    with transaction.atomic():
        match_two = propose_match(referee_two)

    assert match_one is not None
    assert match_two is None
    assert Match.objects.count() == 1
    referee_two.refresh_from_db()
    assert referee_two.status == Registration.Status.WAITING


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
# send_match_notification
# ---------------------------------------------------------------------------


def test_send_match_notification_sends_two_emails() -> None:
    """send_match_notification sends one email to each party."""
    match = MatchFactory.create()
    send_match_notification(match)
    assert len(mail.outbox) == 2


def test_send_match_notification_contains_no_pii() -> None:
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
    send_match_notification(match)

    for message in mail.outbox:
        body = message.body
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
            referee_reg.user.first_name not in body or not referee_reg.user.first_name
        )
        # The match link is present (non-PII — token is opaque).
        assert "/match/" in body


def test_send_match_notification_includes_match_link() -> None:
    """Each notification email body contains the /match/ path."""
    match = MatchFactory.create()
    send_match_notification(match)
    assert len(mail.outbox) == 2
    for message in mail.outbox:
        assert "/match/" in message.body


def test_send_match_notification_respects_preferred_language() -> None:
    """Each recipient's email is rendered in their preferred_language."""
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
    send_match_notification(match)
    assert len(mail.outbox) == 2


# ---------------------------------------------------------------------------
# register_participant (integration)
# ---------------------------------------------------------------------------


_AMBASSADOR_STATEMENTS = [
    (
        "I have held a seasonal or annual pass from one of the 4 Vallées"
        " companies in 2024-25 or 2025-26."
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
    # Pre-populate a waiting ambassador.
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
                " the 4 Vallées companies in 2024-25 or 2025-26.",
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
            " the 4 Vallées companies in 2024-25 or 2025-26.",
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


def test_propose_match_skips_matched_registration() -> None:
    """propose_match returns None for a registration that is already MATCHED."""
    RegistrationFactory.create(referee=True)
    already_matched = RegistrationFactory.create(
        role=Registration.Role.AMBASSADOR,
        prior_pass=Registration.PriorPass.SEASONAL,
        status=Registration.Status.MATCHED,
    )
    with transaction.atomic():
        result = propose_match(already_matched)
    assert result is None
    assert Match.objects.count() == 0


# ---------------------------------------------------------------------------
# requeue_to_front
# ---------------------------------------------------------------------------


def test_requeue_to_front_sets_waiting_and_increments_priority() -> None:
    """requeue_to_front sets status=WAITING and increments priority by 1."""
    reg = RegistrationFactory.create(
        status=Registration.Status.MATCHED,
        priority=0,
        flake_count=1,
    )
    requeue_to_front(reg)

    reg.refresh_from_db()
    assert reg.status == Registration.Status.WAITING
    assert reg.priority == 1
    assert reg.flake_count == 1  # unchanged


def test_requeue_to_front_syncs_in_memory_instance() -> None:
    """requeue_to_front syncs the passed-in instance's fields to the DB values."""
    reg = RegistrationFactory.create(
        status=Registration.Status.MATCHED,
        priority=5,
    )
    requeue_to_front(reg)

    # In-memory fields must match DB without an extra refresh.
    assert reg.status == Registration.Status.WAITING
    assert reg.priority == 6


def test_requeue_to_front_lost_update_guard() -> None:
    """requeue_to_front reads the locked DB row, not the stale instance.

    The DB priority is 5 but the in-memory instance is stale at 0. The
    increment must be computed from the locked row (5 → 6), not the stale 0.
    """
    reg = RegistrationFactory.create(
        status=Registration.Status.MATCHED,
        priority=5,  # DB value is 5
    )
    reg.priority = 0  # stale — must NOT be used by the service

    requeue_to_front(reg)

    reg.refresh_from_db()
    assert reg.priority == 6


# ---------------------------------------------------------------------------
# requeue_to_back
# ---------------------------------------------------------------------------


def test_requeue_to_back_sets_waiting_and_decrements_priority() -> None:
    """requeue_to_back sets status=WAITING and decrements priority by 1."""
    reg = RegistrationFactory.create(
        status=Registration.Status.MATCHED,
        priority=0,
        flake_count=0,
    )
    requeue_to_back(reg)

    reg.refresh_from_db()
    assert reg.status == Registration.Status.WAITING
    assert reg.priority == -1
    assert reg.flake_count == 0  # unchanged


def test_requeue_to_back_syncs_in_memory_instance() -> None:
    """requeue_to_back syncs the passed-in instance's fields to the DB values."""
    reg = RegistrationFactory.create(
        status=Registration.Status.MATCHED,
        priority=3,
    )
    requeue_to_back(reg)

    assert reg.status == Registration.Status.WAITING
    assert reg.priority == 2


def test_requeue_to_back_lost_update_guard() -> None:
    """requeue_to_back reads the locked DB row, not the stale instance.

    The DB priority is 3 but the in-memory instance is stale at 0. The
    decrement must be computed from the locked row (3 → 2), not the stale 0.
    """
    reg = RegistrationFactory.create(
        status=Registration.Status.MATCHED,
        priority=3,  # DB value is 3
    )
    reg.priority = 0  # stale — must NOT be used by the service

    requeue_to_back(reg)

    reg.refresh_from_db()
    assert reg.priority == 2


# ---------------------------------------------------------------------------
# record_flake_and_requeue
# ---------------------------------------------------------------------------


def test_record_flake_first_flake_requeues_to_back() -> None:
    """record_flake_and_requeue: first flake (count 0→1) requeues to back."""
    reg = RegistrationFactory.create(
        status=Registration.Status.MATCHED,
        priority=0,
        flake_count=0,
    )
    record_flake_and_requeue(reg)

    reg.refresh_from_db()
    assert reg.flake_count == 1
    assert reg.status == Registration.Status.WAITING
    assert reg.priority == -1


def test_record_flake_boundary_suspends_at_two() -> None:
    """record_flake_and_requeue: second flake (count 1→2) suspends.

    Priority must not be decremented on the suspend branch.
    """
    starting_priority = 5
    reg = RegistrationFactory.create(
        status=Registration.Status.MATCHED,
        priority=starting_priority,
        flake_count=1,
    )
    record_flake_and_requeue(reg)

    reg.refresh_from_db()
    assert reg.flake_count == 2
    assert reg.status == Registration.Status.SUSPENDED
    assert reg.priority == starting_priority  # must not be decremented


def test_record_flake_lost_update_guard() -> None:
    """record_flake_and_requeue reads the locked DB row, not the stale instance.

    The in-memory instance shows flake_count=0 but the DB already has 1.
    The function must read 1 from DB, increment to 2, and suspend.
    """
    reg = RegistrationFactory.create(
        status=Registration.Status.MATCHED,
        priority=0,
        flake_count=1,  # DB value is 1
    )
    # Simulate a stale in-memory instance by overwriting the Python attribute.
    reg.flake_count = 0  # stale — must NOT be used by the service

    record_flake_and_requeue(reg)

    # DB must reflect the correct incremented value from the locked row.
    reg.refresh_from_db()
    assert reg.flake_count == 2
    assert reg.status == Registration.Status.SUSPENDED


def test_record_flake_lost_update_guard_syncs_instance() -> None:
    """record_flake_and_requeue syncs the passed-in instance after the DB write."""
    reg = RegistrationFactory.create(
        status=Registration.Status.MATCHED,
        priority=0,
        flake_count=1,
    )
    reg.flake_count = 0  # stale

    record_flake_and_requeue(reg)

    # In-memory instance must be synced (no extra refresh needed).
    assert reg.flake_count == 2
    assert reg.status == Registration.Status.SUSPENDED


# ---------------------------------------------------------------------------
# suspend_for_no_show
# ---------------------------------------------------------------------------


def test_suspend_for_no_show_sets_suspended_and_increments_flake() -> None:
    """suspend_for_no_show sets status=SUSPENDED and increments flake_count."""
    reg = RegistrationFactory.create(
        status=Registration.Status.MATCHED,
        flake_count=0,
    )
    suspend_for_no_show(reg)

    reg.refresh_from_db()
    assert reg.status == Registration.Status.SUSPENDED
    assert reg.flake_count == 1


def test_suspend_for_no_show_syncs_in_memory_instance() -> None:
    """suspend_for_no_show syncs the passed-in instance's fields to the DB values."""
    reg = RegistrationFactory.create(
        status=Registration.Status.MATCHED,
        flake_count=0,
    )
    suspend_for_no_show(reg)

    assert reg.status == Registration.Status.SUSPENDED
    assert reg.flake_count == 1


def test_suspend_for_no_show_increments_flake_unconditionally() -> None:
    """suspend_for_no_show increments flake_count regardless of its current value."""
    reg = RegistrationFactory.create(
        status=Registration.Status.MATCHED,
        flake_count=2,
    )
    suspend_for_no_show(reg)

    reg.refresh_from_db()
    assert reg.status == Registration.Status.SUSPENDED
    assert reg.flake_count == 3


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
# register_participant — PENDING status (VERB-24)
# ---------------------------------------------------------------------------


def test_register_participant_pending_creates_pending_registration() -> None:
    """register_participant with status=PENDING creates a PENDING registration."""
    registration = register_participant(
        role=Registration.Role.REFEREE,
        first_name="Grace",
        last_name="Hopper",
        email="grace@example.com",
        prior_pass=Registration.PriorPass.NONE,
        status=Registration.Status.PENDING,
    )
    assert registration.status == Registration.Status.PENDING


def test_register_participant_pending_does_not_propose_match() -> None:
    """A PENDING registration must never trigger propose_match (Invariant 2)."""
    RegistrationFactory.create(
        role=Registration.Role.AMBASSADOR,
        prior_pass=Registration.PriorPass.SEASONAL,
        status=Registration.Status.WAITING,
    )
    register_participant(
        role=Registration.Role.REFEREE,
        first_name="Grace",
        last_name="Hopper",
        email="grace@example.com",
        prior_pass=Registration.PriorPass.NONE,
        status=Registration.Status.PENDING,
    )
    assert Match.objects.count() == 0


def test_register_participant_waiting_still_proposes_match() -> None:
    """The default (WAITING) path still calls propose_match (regression guard)."""
    RegistrationFactory.create(
        role=Registration.Role.AMBASSADOR,
        prior_pass=Registration.PriorPass.SEASONAL,
        status=Registration.Status.WAITING,
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
# confirm_registration (VERB-24)
# ---------------------------------------------------------------------------


def test_confirm_registration_transitions_pending_to_waiting() -> None:
    """confirm_registration transitions a PENDING registration to WAITING."""
    reg = RegistrationFactory.create(
        role=Registration.Role.AMBASSADOR,
        prior_pass=Registration.PriorPass.SEASONAL,
        status=Registration.Status.PENDING,
    )
    result = confirm_registration(reg)
    assert result.status == Registration.Status.WAITING
    reg.refresh_from_db()
    assert reg.status == Registration.Status.WAITING


def test_confirm_registration_proposes_match_after_flip() -> None:
    """confirm_registration calls propose_match after transitioning to WAITING."""
    RegistrationFactory.create(
        role=Registration.Role.AMBASSADOR,
        prior_pass=Registration.PriorPass.SEASONAL,
        status=Registration.Status.WAITING,
    )
    reg = RegistrationFactory.create(
        referee=True,
        status=Registration.Status.PENDING,
    )
    with TestCase.captureOnCommitCallbacks(execute=True):
        confirm_registration(reg)
    assert Match.objects.count() == 1


def test_confirm_registration_non_pending_is_noop() -> None:
    """confirm_registration on a non-PENDING registration is a no-op."""
    reg = RegistrationFactory.create(
        role=Registration.Role.AMBASSADOR,
        prior_pass=Registration.PriorPass.SEASONAL,
        status=Registration.Status.WAITING,
    )
    result = confirm_registration(reg)
    # Status unchanged; no match created (no counterpart anyway).
    assert result.status == Registration.Status.WAITING
    assert Match.objects.count() == 0


def test_confirm_registration_syncs_in_memory_instance() -> None:
    """confirm_registration syncs the passed-in instance's status field."""
    reg = RegistrationFactory.create(
        status=Registration.Status.PENDING,
    )
    result = confirm_registration(reg)
    # Must be synced without a separate refresh.
    assert result.status == Registration.Status.WAITING
    assert reg.status == Registration.Status.WAITING


# ---------------------------------------------------------------------------
# record_acceptance
# ---------------------------------------------------------------------------


def test_record_acceptance_first_accept_sets_ambassador_timestamp_only() -> None:
    """First accept by ambassador sets ambassador_accepted_at; status stays PROPOSED."""
    ambassador_reg = RegistrationFactory.create(status=Registration.Status.MATCHED)
    referee_reg = RegistrationFactory.create(
        referee=True, status=Registration.Status.MATCHED
    )
    match = MatchFactory.create(
        ambassador_registration=ambassador_reg,
        referee_registration=referee_reg,
    )
    before = timezone.now()

    result = record_acceptance(match, ambassador_reg)

    after = timezone.now()
    result.refresh_from_db()
    assert result.status == Match.Status.PROPOSED
    assert result.ambassador_accepted_at is not None
    assert before <= result.ambassador_accepted_at <= after
    assert result.referee_accepted_at is None
    # No log row on first accept.
    assert StateTransitionLog.objects.count() == 0


def test_record_acceptance_first_accept_by_referee_sets_referee_timestamp_only() -> (
    None
):
    """First accept by referee sets referee_accepted_at; status stays PROPOSED."""
    ambassador_reg = RegistrationFactory.create(status=Registration.Status.MATCHED)
    referee_reg = RegistrationFactory.create(
        referee=True, status=Registration.Status.MATCHED
    )
    match = MatchFactory.create(
        ambassador_registration=ambassador_reg,
        referee_registration=referee_reg,
    )

    result = record_acceptance(match, referee_reg)

    result.refresh_from_db()
    assert result.status == Match.Status.PROPOSED
    assert result.referee_accepted_at is not None
    assert result.ambassador_accepted_at is None
    assert StateTransitionLog.objects.count() == 0


def test_record_acceptance_second_accept_transitions_to_accepted() -> None:
    """Second accept transitions Match → ACCEPTED and both registrations → CONFIRMED."""
    ambassador_reg = RegistrationFactory.create(status=Registration.Status.MATCHED)
    referee_reg = RegistrationFactory.create(
        referee=True, status=Registration.Status.MATCHED
    )
    match = MatchFactory.create(
        ambassador_registration=ambassador_reg,
        referee_registration=referee_reg,
    )

    # First accept — ambassador.
    record_acceptance(match, ambassador_reg)
    match.refresh_from_db()
    assert match.status == Match.Status.PROPOSED

    # Second accept — referee triggers the full transition.
    result = record_acceptance(match, referee_reg)

    result.refresh_from_db()
    assert result.status == Match.Status.ACCEPTED

    ambassador_reg.refresh_from_db()
    referee_reg.refresh_from_db()
    assert ambassador_reg.status == Registration.Status.CONFIRMED
    assert referee_reg.status == Registration.Status.CONFIRMED


def test_record_acceptance_second_accept_writes_three_log_rows() -> None:
    """Mutual accept writes exactly three StateTransitionLog rows."""
    from django.contrib.contenttypes.models import ContentType

    ambassador_reg = RegistrationFactory.create(status=Registration.Status.MATCHED)
    referee_reg = RegistrationFactory.create(
        referee=True, status=Registration.Status.MATCHED
    )
    match = MatchFactory.create(
        ambassador_registration=ambassador_reg,
        referee_registration=referee_reg,
    )

    record_acceptance(match, ambassador_reg)
    record_acceptance(match, referee_reg)

    logs = list(StateTransitionLog.objects.order_by("pk"))
    assert len(logs) == 3

    match_ct = ContentType.objects.get_for_model(Match)
    reg_ct = ContentType.objects.get_for_model(Registration)

    # One log for Match.status.
    match_log = next(
        (
            log
            for log in logs
            if log.content_type_id == match_ct.pk and log.object_id == match.pk
        ),
        None,
    )
    assert match_log is not None
    assert match_log.state_before == Match.Status.PROPOSED
    assert match_log.state_after == Match.Status.ACCEPTED

    # One log for ambassador Registration.status.
    amb_log = next(
        (
            log
            for log in logs
            if log.content_type_id == reg_ct.pk and log.object_id == ambassador_reg.pk
        ),
        None,
    )
    assert amb_log is not None
    assert amb_log.state_before == Registration.Status.MATCHED
    assert amb_log.state_after == Registration.Status.CONFIRMED

    # One log for referee Registration.status.
    ref_log = next(
        (
            log
            for log in logs
            if log.content_type_id == reg_ct.pk and log.object_id == referee_reg.pk
        ),
        None,
    )
    assert ref_log is not None
    assert ref_log.state_before == Registration.Status.MATCHED
    assert ref_log.state_after == Registration.Status.CONFIRMED


def test_record_acceptance_re_accept_is_idempotent_for_timestamp() -> None:
    """Re-accepting an already-accepted side does not change the existing timestamp."""
    ambassador_reg = RegistrationFactory.create(status=Registration.Status.MATCHED)
    referee_reg = RegistrationFactory.create(
        referee=True, status=Registration.Status.MATCHED
    )
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
    # Status still PROPOSED (referee hasn't accepted).
    assert result_second.status == Match.Status.PROPOSED


def test_record_acceptance_raises_for_non_proposed_match() -> None:
    """record_acceptance raises ValueError if match.status != PROPOSED."""
    match = MatchFactory.create(status=Match.Status.DECLINED)
    ambassador_reg = match.ambassador_registration

    with pytest.raises(ValueError, match="PROPOSED"):
        record_acceptance(match, ambassador_reg)


# ---------------------------------------------------------------------------
# record_decline
# ---------------------------------------------------------------------------


def test_record_decline_transitions_match_to_declined() -> None:
    """record_decline transitions match PROPOSED → DECLINED and sets declined_by/at."""
    ambassador_reg = RegistrationFactory.create(status=Registration.Status.MATCHED)
    referee_reg = RegistrationFactory.create(
        referee=True, status=Registration.Status.MATCHED
    )
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
    ambassador_reg = RegistrationFactory.create(status=Registration.Status.MATCHED)
    referee_reg = RegistrationFactory.create(
        referee=True, status=Registration.Status.MATCHED
    )
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
    ambassador_reg = RegistrationFactory.create(status=Registration.Status.MATCHED)
    referee_reg = RegistrationFactory.create(
        referee=True, status=Registration.Status.MATCHED
    )
    match = MatchFactory.create(
        ambassador_registration=ambassador_reg,
        referee_registration=referee_reg,
    )

    record_decline(match, ambassador_reg)

    ambassador_reg.refresh_from_db()
    referee_reg.refresh_from_db()
    # Re-queuing belongs to VERB-17; this service must not touch these.
    assert ambassador_reg.status == Registration.Status.MATCHED
    assert referee_reg.status == Registration.Status.MATCHED


def test_record_decline_raises_for_non_proposed_match() -> None:
    """record_decline raises ValueError if match.status != PROPOSED."""
    match = MatchFactory.create(status=Match.Status.ACCEPTED)
    referee_reg = match.referee_registration

    with pytest.raises(ValueError, match="PROPOSED"):
        record_decline(match, referee_reg)


# ---------------------------------------------------------------------------
# send_match_confirmed_email
# ---------------------------------------------------------------------------


def test_send_match_confirmed_email_sends_two_emails() -> None:
    """send_match_confirmed_email sends exactly one email to each party."""
    match = MatchFactory.create(accepted=True)
    send_match_confirmed_email(match)
    assert len(mail.outbox) == 2


def test_send_match_confirmed_email_contains_counterpart_details() -> None:
    """Each confirmed email contains the counterpart's name, email, and phone."""
    ambassador_reg = RegistrationFactory.create(
        role=Registration.Role.AMBASSADOR,
        prior_pass=Registration.PriorPass.SEASONAL,
        phone="+41790001111",
        status=Registration.Status.CONFIRMED,
    )
    referee_reg = RegistrationFactory.create(
        referee=True,
        phone="+41790002222",
        status=Registration.Status.CONFIRMED,
    )
    match = MatchFactory.create(
        accepted=True,
        ambassador_registration=ambassador_reg,
        referee_registration=referee_reg,
    )
    send_match_confirmed_email(match)
    assert len(mail.outbox) == 2

    # Map To: addresses to message bodies.
    body_by_recipient = {msg.to[0]: msg.body for msg in mail.outbox}

    # The ambassador's email contains the referee's contact details.
    amb_body = body_by_recipient[ambassador_reg.user.email]
    assert referee_reg.user.email in amb_body
    assert referee_reg.phone in amb_body
    assert referee_reg.user.first_name in amb_body or not referee_reg.user.first_name

    # The referee's email contains the ambassador's contact details.
    ref_body = body_by_recipient[referee_reg.user.email]
    assert ambassador_reg.user.email in ref_body
    assert ambassador_reg.phone in ref_body
    assert (
        ambassador_reg.user.first_name in ref_body or not ambassador_reg.user.first_name
    )


def test_send_match_confirmed_email_respects_preferred_language() -> None:
    """send_match_confirmed_email renders each email under the recipient's language."""
    ambassador_reg = RegistrationFactory.create(
        role=Registration.Role.AMBASSADOR,
        prior_pass=Registration.PriorPass.SEASONAL,
        preferred_language="fr",
        status=Registration.Status.CONFIRMED,
    )
    referee_reg = RegistrationFactory.create(
        referee=True,
        preferred_language="en",
        status=Registration.Status.CONFIRMED,
    )
    match = MatchFactory.create(
        accepted=True,
        ambassador_registration=ambassador_reg,
        referee_registration=referee_reg,
    )
    send_match_confirmed_email(match)
    assert len(mail.outbox) == 2


# ---------------------------------------------------------------------------
# accept_match
# ---------------------------------------------------------------------------


def test_accept_match_first_accept_stays_proposed_no_email() -> None:
    """First accept leaves match PROPOSED and sends no confirmed email."""
    ambassador_reg = RegistrationFactory.create(status=Registration.Status.MATCHED)
    referee_reg = RegistrationFactory.create(
        referee=True, status=Registration.Status.MATCHED
    )
    match = MatchFactory.create(
        ambassador_registration=ambassador_reg,
        referee_registration=referee_reg,
    )
    with TestCase.captureOnCommitCallbacks(execute=True):
        result = accept_match(match, ambassador_reg)

    assert result.status == Match.Status.PROPOSED
    assert len(mail.outbox) == 0


def test_accept_match_second_accept_transitions_accepted_and_sends_email() -> None:
    """Second accept transitions match to ACCEPTED and sends confirmed emails."""
    ambassador_reg = RegistrationFactory.create(status=Registration.Status.MATCHED)
    referee_reg = RegistrationFactory.create(
        referee=True, status=Registration.Status.MATCHED
    )
    match = MatchFactory.create(
        ambassador_registration=ambassador_reg,
        referee_registration=referee_reg,
    )

    # First accept — ambassador.
    with TestCase.captureOnCommitCallbacks(execute=True):
        accept_match(match, ambassador_reg)

    match.refresh_from_db()
    assert match.status == Match.Status.PROPOSED
    assert len(mail.outbox) == 0

    # Second accept — referee triggers the full transition.
    with TestCase.captureOnCommitCallbacks(execute=True):
        result = accept_match(match, referee_reg)

    assert result.status == Match.Status.ACCEPTED

    ambassador_reg.refresh_from_db()
    referee_reg.refresh_from_db()
    assert ambassador_reg.status == Registration.Status.CONFIRMED
    assert referee_reg.status == Registration.Status.CONFIRMED

    # Two confirmed emails sent (one per party).
    assert len(mail.outbox) == 2


# ---------------------------------------------------------------------------
# decline_match
# ---------------------------------------------------------------------------


def test_decline_match_transitions_to_declined() -> None:
    """decline_match transitions the match to DECLINED."""
    ambassador_reg = RegistrationFactory.create(status=Registration.Status.MATCHED)
    referee_reg = RegistrationFactory.create(
        referee=True, status=Registration.Status.MATCHED
    )
    match = MatchFactory.create(
        ambassador_registration=ambassador_reg,
        referee_registration=referee_reg,
    )
    result = decline_match(match, ambassador_reg)
    result.refresh_from_db()
    assert result.status == Match.Status.DECLINED


def test_decline_match_requeues_decliner_to_back_and_other_to_front() -> None:
    """decline_match sends decliner to back (priority -1) and other to front (+1)."""
    ambassador_reg = RegistrationFactory.create(
        status=Registration.Status.MATCHED, priority=0
    )
    referee_reg = RegistrationFactory.create(
        referee=True, status=Registration.Status.MATCHED, priority=0
    )
    match = MatchFactory.create(
        ambassador_registration=ambassador_reg,
        referee_registration=referee_reg,
    )

    decline_match(match, ambassador_reg)

    ambassador_reg.refresh_from_db()
    referee_reg.refresh_from_db()
    # Decliner (ambassador) goes to back: status WAITING, priority decremented.
    assert ambassador_reg.status == Registration.Status.WAITING
    assert ambassador_reg.priority == -1
    # Other party (referee) goes to front: status WAITING, priority incremented.
    assert referee_reg.status == Registration.Status.WAITING
    assert referee_reg.priority == 1


def test_decline_match_by_referee_requeues_correctly() -> None:
    """decline_match by the referee side re-queues symmetrically in reverse."""
    ambassador_reg = RegistrationFactory.create(
        status=Registration.Status.MATCHED, priority=5
    )
    referee_reg = RegistrationFactory.create(
        referee=True, status=Registration.Status.MATCHED, priority=5
    )
    match = MatchFactory.create(
        ambassador_registration=ambassador_reg,
        referee_registration=referee_reg,
    )

    decline_match(match, referee_reg)

    ambassador_reg.refresh_from_db()
    referee_reg.refresh_from_db()
    # Ambassador (other side) goes to front: priority 5 → 6.
    assert ambassador_reg.status == Registration.Status.WAITING
    assert ambassador_reg.priority == 6
    # Referee (decliner) goes to back: priority 5 → 4.
    assert referee_reg.status == Registration.Status.WAITING
    assert referee_reg.priority == 4


# ---------------------------------------------------------------------------
# report_no_show (VERB-21)
# ---------------------------------------------------------------------------


def test_report_no_show_transitions_match_to_abandoned() -> None:
    """report_no_show transitions match ACCEPTED → ABANDONED."""
    ambassador_reg = RegistrationFactory.create(
        status=Registration.Status.CONFIRMED, priority=0
    )
    referee_reg = RegistrationFactory.create(
        referee=True, status=Registration.Status.CONFIRMED, priority=0
    )
    match = MatchFactory.create(
        accepted=True,
        ambassador_registration=ambassador_reg,
        referee_registration=referee_reg,
    )

    result = report_no_show(match, ambassador_reg)

    result.refresh_from_db()
    assert result.status == Match.Status.ABANDONED
    assert result.no_show_reported_by == Match.Side.AMBASSADOR
    assert result.no_show_reported_at is not None


def test_report_no_show_no_show_reported_at_is_tz_aware() -> None:
    """report_no_show sets a tz-aware no_show_reported_at timestamp."""
    from django.utils import timezone

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
    """Reporter (kept-faith party) is re-queued to the front (WAITING, priority +1)."""
    ambassador_reg = RegistrationFactory.create(
        status=Registration.Status.CONFIRMED, priority=3
    )
    referee_reg = RegistrationFactory.create(
        referee=True, status=Registration.Status.CONFIRMED, priority=0
    )
    match = MatchFactory.create(
        accepted=True,
        ambassador_registration=ambassador_reg,
        referee_registration=referee_reg,
    )

    report_no_show(match, ambassador_reg)

    ambassador_reg.refresh_from_db()
    assert ambassador_reg.status == Registration.Status.WAITING
    assert ambassador_reg.priority == 4  # 3 + 1


def test_report_no_show_accused_suspended() -> None:
    """The accused party is SUSPENDED and flake_count incremented."""
    ambassador_reg = RegistrationFactory.create(
        status=Registration.Status.CONFIRMED, flake_count=0
    )
    referee_reg = RegistrationFactory.create(
        referee=True, status=Registration.Status.CONFIRMED, flake_count=0
    )
    match = MatchFactory.create(
        accepted=True,
        ambassador_registration=ambassador_reg,
        referee_registration=referee_reg,
    )

    report_no_show(match, ambassador_reg)

    # Reporter is ambassador; accused is referee.
    referee_reg.refresh_from_db()
    assert referee_reg.status == Registration.Status.SUSPENDED
    assert referee_reg.flake_count == 1


def test_report_no_show_writes_two_transition_log_rows() -> None:
    """report_no_show writes exactly two StateTransitionLog rows for the objects.

    One for Match.status (ACCEPTED → ABANDONED) and one for the accused
    Registration.status (CONFIRMED → SUSPENDED). The reporter's CONFIRMED →
    WAITING transition is intentionally not logged (consistent with the
    decline path).

    The count is scoped to the specific match and accused-registration PKs so
    the assertion holds even when other rows exist in the table (e.g. from
    earlier tests in the same session).
    """
    from django.contrib.contenttypes.models import ContentType

    ambassador_reg = RegistrationFactory.create(
        status=Registration.Status.CONFIRMED, priority=0
    )
    referee_reg = RegistrationFactory.create(
        referee=True, status=Registration.Status.CONFIRMED, priority=0
    )
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
    assert match_log.state_after == Match.Status.ABANDONED

    # One log for the accused (referee) Registration.status.
    reg_log = next(
        (log for log in relevant_logs if log.content_type_id == reg_ct.pk),
        None,
    )
    assert reg_log is not None
    assert reg_log.object_id == referee_reg.pk
    assert reg_log.state_before == Registration.Status.CONFIRMED
    assert reg_log.state_after == Registration.Status.SUSPENDED


def test_report_no_show_sends_one_email_to_accused() -> None:
    """report_no_show queues one email to the accused party only (no reporter PII)."""
    ambassador_reg = RegistrationFactory.create(
        status=Registration.Status.CONFIRMED,
        phone="+41790001111",
    )
    referee_reg = RegistrationFactory.create(
        referee=True,
        status=Registration.Status.CONFIRMED,
        phone="+41790002222",
    )
    match = MatchFactory.create(
        accepted=True,
        ambassador_registration=ambassador_reg,
        referee_registration=referee_reg,
    )

    with TestCase.captureOnCommitCallbacks(execute=True):
        report_no_show(match, ambassador_reg)

    assert len(mail.outbox) == 1
    # Email sent to the accused (referee), not the reporter.
    assert mail.outbox[0].to == [referee_reg.user.email]


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
        status=Registration.Status.CONFIRMED,
        phone="+41790001111",
    )
    referee_reg = RegistrationFactory.create(
        referee=True,
        status=Registration.Status.CONFIRMED,
        phone="+41790002222",
    )
    match = MatchFactory.create(
        accepted=True,
        ambassador_registration=ambassador_reg,
        referee_registration=referee_reg,
    )

    with TestCase.captureOnCommitCallbacks(execute=True):
        report_no_show(match, ambassador_reg)

    body = mail.outbox[0].body
    # Each reporter PII value must be absent from the accused's email body.
    assert ambassador_reg.phone not in body
    assert ambassador_reg.user.email not in body
    assert ambassador_reg.user.first_name not in body
    assert ambassador_reg.user.last_name not in body


def test_report_no_show_email_respects_accused_preferred_language() -> None:
    """send_no_show_notification renders under the accused's preferred_language."""
    accused_reg = RegistrationFactory.create(
        referee=True,
        status=Registration.Status.CONFIRMED,
        preferred_language="fr",
    )
    ambassador_reg = RegistrationFactory.create(
        status=Registration.Status.CONFIRMED,
        preferred_language="en",
    )
    match = MatchFactory.create(
        accepted=True,
        ambassador_registration=ambassador_reg,
        referee_registration=accused_reg,
    )

    with TestCase.captureOnCommitCallbacks(execute=True):
        report_no_show(match, ambassador_reg)

    # The single notification is addressed to the accused (the suspended party),
    # whose preferred_language drives the render. We assert delivery rather than
    # translated copy: the test env does not compile message catalogues, so
    # gettext falls back to the source string (see the other *_preferred_language
    # tests, which assert the same way).
    assert len(mail.outbox) == 1
    assert mail.outbox[0].to == [accused_reg.user.email]


def test_report_no_show_raises_on_non_accepted_match() -> None:
    """report_no_show raises ValueError if match.status != ACCEPTED."""
    match = MatchFactory.create(status=Match.Status.PROPOSED)

    with pytest.raises(ValueError, match="ACCEPTED"):
        report_no_show(match, match.ambassador_registration)


def test_report_no_show_raises_if_already_reported() -> None:
    """report_no_show raises ValueError if no_show_reported_by is already set.

    Build an ACCEPTED match that already has no_show_reported_by set directly
    so the status guard passes but the already-reported guard fires.
    """
    match = MatchFactory.create(
        accepted=True,
        no_show_reported_by=Match.Side.AMBASSADOR,
    )

    with pytest.raises(ValueError, match="already"):
        report_no_show(match, match.referee_registration)


def test_report_no_show_raises_on_declined_match() -> None:
    """report_no_show raises ValueError on a DECLINED match (not ACCEPTED)."""
    match = MatchFactory.create(declined=True)

    with pytest.raises(ValueError, match="ACCEPTED"):
        report_no_show(match, match.ambassador_registration)


def test_report_no_show_returns_updated_match() -> None:
    """report_no_show returns the updated Match instance."""
    match = MatchFactory.create(accepted=True)

    result = report_no_show(match, match.ambassador_registration)

    assert result.status == Match.Status.ABANDONED
    assert result.pk == match.pk


# ---------------------------------------------------------------------------
# send_no_show_notification (VERB-21)
# ---------------------------------------------------------------------------


def test_send_no_show_notification_sends_one_email() -> None:
    """send_no_show_notification sends exactly one email to the accused."""
    match = MatchFactory.create(accepted=True)
    accused = match.referee_registration

    send_no_show_notification(match, accused)

    assert len(mail.outbox) == 1
    assert mail.outbox[0].to == [accused.user.email]


def test_send_no_show_notification_contains_no_reporter_pii() -> None:
    """send_no_show_notification must not contain any reporter PII (Invariant 1)."""
    ambassador_reg = RegistrationFactory.create(
        status=Registration.Status.CONFIRMED,
        phone="+41790003333",
    )
    referee_reg = RegistrationFactory.create(
        referee=True,
        status=Registration.Status.CONFIRMED,
        phone="+41790004444",
    )
    match = MatchFactory.create(
        accepted=True,
        ambassador_registration=ambassador_reg,
        referee_registration=referee_reg,
    )

    # Reporter is ambassador; accused is referee.
    send_no_show_notification(match, referee_reg)

    body = mail.outbox[0].body
    # Reporter's PII must not appear in the accused's email.
    assert ambassador_reg.phone not in body
    assert ambassador_reg.user.email not in body


def test_send_no_show_notification_respects_preferred_language() -> None:
    """send_no_show_notification renders under the accused's preferred_language."""
    accused_reg = RegistrationFactory.create(
        referee=True,
        status=Registration.Status.CONFIRMED,
        preferred_language="fr",
    )
    match = MatchFactory.create(
        accepted=True,
        referee_registration=accused_reg,
    )

    send_no_show_notification(match, accused_reg)

    # Assert delivery to the accused rather than translated copy: the test env
    # does not compile message catalogues, so gettext returns the source string.
    assert len(mail.outbox) == 1
    assert mail.outbox[0].to == [accused_reg.user.email]
