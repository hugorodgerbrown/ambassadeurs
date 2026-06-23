# Tests for the matching service functions.

from datetime import UTC, datetime, timedelta

import pytest
from django.contrib.auth.models import User
from django.core import mail
from django.db import transaction
from django.test import TestCase, override_settings
from django.utils import timezone

from matching.models import Match, Registration
from matching.services import (
    is_eligible_pair,
    is_registration_open,
    propose_match,
    register_participant,
    send_match_notification,
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
    REGISTRATION_OPENS_AT="2020-01-01T00:00:00+00:00",
    REGISTRATION_CLOSES_AT="2020-12-31T23:59:59+00:00",
)
def test_registration_closed_outside_window() -> None:
    """is_registration_open returns False when now is outside the window."""
    assert is_registration_open() is False


@override_settings(
    REGISTRATION_OPENS_AT="not-a-date",
    REGISTRATION_CLOSES_AT="also-not-a-date",
)
def test_registration_closed_on_parse_error() -> None:
    """is_registration_open returns False when the date strings are invalid."""
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
    """Notification emails must not contain any contact PII (Invariant 1)."""
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
        # No phone numbers
        assert "+41790001234" not in body
        assert "+41790005678" not in body
        # No email addresses (only the recipient's own, which is the To: field)
        assert ambassador_reg.user.email not in body
        assert referee_reg.user.email not in body
        # No names
        assert (
            ambassador_reg.user.first_name not in body
            or not ambassador_reg.user.first_name
        )
        assert (
            referee_reg.user.first_name not in body or not referee_reg.user.first_name
        )
        # No action links
        assert "/accept" not in body
        assert "/decline" not in body
        assert "token" not in body.lower()


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
    )

    user = User.objects.get(username="ada@example.com")
    assert user.email == "ada@example.com"
    assert not user.has_usable_password()
    assert registration.role == Registration.Role.AMBASSADOR
    assert registration.prior_pass == Registration.PriorPass.SEASONAL
    assert registration.preferred_location == "VERBIER"
    assert registration.preferred_language == "fr"
    assert registration.phone == "+41790000001"


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
