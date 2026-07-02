# Tests for the Registration and Match models.
#
# Match.accept_side (VERB-101 / ADR 0017) covers the pure, in-memory
# model-logic half of the accept transition; Match.expire, Registration.pause
# and Registration.requeue are covered separately in
# tests/matching/test_expire_match.py (VERB-100) and are not duplicated here.

from datetime import UTC, datetime

import pytest
from django.db import IntegrityError

from core.exceptions import StateTransitionError
from matching.models import Match, Registration
from tests.accounts.factories import UserFactory
from tests.matching.factories import MatchFactory, RegistrationFactory

pytestmark = pytest.mark.django_db


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------


def test_registration_to_string_contains_user_and_role() -> None:
    """Registration.to_string contains the user identifier and role."""
    user = UserFactory.create(username="ada@example.com")
    registration = RegistrationFactory.create(
        user=user,
        role=Registration.Role.AMBASSADOR,
    )
    s = str(registration)
    assert "ada@example.com" in s
    assert "Ambassador" in s


def test_one_registration_per_user() -> None:
    """A user cannot have two registrations (OneToOneField constraint)."""
    user = UserFactory.create()
    RegistrationFactory.create(user=user)
    with pytest.raises(IntegrityError):
        RegistrationFactory.create(user=user)


def test_registration_queryset_ambassadors_filter() -> None:
    """RegistrationQuerySet.ambassadors returns only ambassador registrations."""
    ambassador = RegistrationFactory.create(role=Registration.Role.AMBASSADOR)
    RegistrationFactory.create(referee=True)
    assert list(Registration.objects.ambassadors()) == [ambassador]


def test_registration_queryset_referees_filter() -> None:
    """RegistrationQuerySet.referees returns only referee registrations."""
    RegistrationFactory.create(role=Registration.Role.AMBASSADOR)
    referee = RegistrationFactory.create(referee=True)
    assert list(Registration.objects.referees()) == [referee]


def test_registration_queryset_verified_filter() -> None:
    """RegistrationQuerySet.verified returns only VERIFIED registrations."""
    verified = RegistrationFactory.create(status=Registration.Status.VERIFIED)
    RegistrationFactory.create(status=Registration.Status.UNVERIFIED)
    assert list(Registration.objects.verified()) == [verified]


def test_eligible_ambassadors_excludes_none_prior_pass() -> None:
    """eligible_ambassadors excludes ambassadors with prior_pass=NONE."""
    RegistrationFactory.create(
        role=Registration.Role.AMBASSADOR,
        prior_pass=Registration.PriorPass.NONE,
    )
    eligible = RegistrationFactory.create(
        role=Registration.Role.AMBASSADOR,
        prior_pass=Registration.PriorPass.SEASONAL,
    )
    assert list(Registration.objects.eligible_ambassadors()) == [eligible]


def test_eligible_ambassadors_includes_all_valid_prior_passes() -> None:
    """eligible_ambassadors includes SEASONAL, ANNUAL and MONT4."""
    seasonal = RegistrationFactory.create(
        role=Registration.Role.AMBASSADOR,
        prior_pass=Registration.PriorPass.SEASONAL,
    )
    annual = RegistrationFactory.create(
        role=Registration.Role.AMBASSADOR,
        prior_pass=Registration.PriorPass.ANNUAL,
    )
    mont4 = RegistrationFactory.create(
        role=Registration.Role.AMBASSADOR,
        prior_pass=Registration.PriorPass.MONT4,
    )
    result = set(Registration.objects.eligible_ambassadors())
    assert {seasonal, annual, mont4} == result


def test_eligible_referees_excludes_non_none_prior_pass() -> None:
    """eligible_referees excludes referees who hold a prior pass."""
    RegistrationFactory.create(
        role=Registration.Role.REFEREE,
        prior_pass=Registration.PriorPass.SEASONAL,
    )
    eligible = RegistrationFactory.create(referee=True)
    assert list(Registration.objects.eligible_referees()) == [eligible]


def test_eligible_ambassadors_excludes_non_verified() -> None:
    """eligible_ambassadors excludes ambassadors not in VERIFIED pool standing."""
    RegistrationFactory.create(
        role=Registration.Role.AMBASSADOR,
        prior_pass=Registration.PriorPass.SEASONAL,
        status=Registration.Status.UNVERIFIED,
    )
    assert not Registration.objects.eligible_ambassadors().exists()


def test_paused_is_a_valid_status() -> None:
    """PAUSED is a valid choice for Registration.Status."""
    reg = RegistrationFactory.create(paused=True)
    assert reg.status == Registration.Status.PAUSED


def test_suspended_is_a_valid_status() -> None:
    """SUSPENDED is a valid choice for Registration.Status."""
    reg = RegistrationFactory.create(suspended=True)
    assert reg.status == Registration.Status.SUSPENDED


def test_eligible_ambassadors_excludes_suspended() -> None:
    """eligible_ambassadors excludes SUSPENDED registrations."""
    RegistrationFactory.create(
        role=Registration.Role.AMBASSADOR,
        prior_pass=Registration.PriorPass.SEASONAL,
        suspended=True,
    )
    assert not Registration.objects.eligible_ambassadors().exists()


def test_eligible_referees_excludes_suspended() -> None:
    """eligible_referees excludes SUSPENDED registrations."""
    RegistrationFactory.create(referee=True, suspended=True)
    assert not Registration.objects.eligible_referees().exists()


# ---------------------------------------------------------------------------
# Registration — nationality field
# ---------------------------------------------------------------------------


def test_nationality_stores_valid_country_code() -> None:
    """nationality stores an ISO 3166-1 alpha-2 code and round-trips cleanly."""
    registration = RegistrationFactory.create(nationality="CH")
    registration.refresh_from_db()
    # CountryField stores the alpha-2 code; comparing as str covers both the
    # Country wrapper object and a plain str.
    assert str(registration.nationality) == "CH"


def test_nationality_accepts_blank() -> None:
    """nationality is optional; a blank value is stored and retrieved as empty."""
    registration = RegistrationFactory.create(nationality="")
    registration.refresh_from_db()
    assert str(registration.nationality) == ""


# ---------------------------------------------------------------------------
# Match
# ---------------------------------------------------------------------------


def test_match_to_string_references_both_parties() -> None:
    """Match.to_string references both the ambassador and referee."""
    match = MatchFactory.create()
    s = str(match)
    assert match.ambassador_registration.user.username in s
    assert match.referee_registration.user.username in s


def test_match_proposed_queryset() -> None:
    """MatchQuerySet.proposed returns only PROPOSED matches."""
    proposed = MatchFactory.create(status=Match.Status.PROPOSED)
    MatchFactory.create(status=Match.Status.EXPIRED)
    assert list(Match.objects.proposed()) == [proposed]


def test_match_active_excludes_declined_and_expired() -> None:
    """MatchQuerySet.active excludes DECLINED, EXPIRED, and CANCELLED."""
    proposed = MatchFactory.create(status=Match.Status.PROPOSED)
    pending = MatchFactory.create(status=Match.Status.PENDING)
    accepted = MatchFactory.create(status=Match.Status.ACCEPTED)
    MatchFactory.create(status=Match.Status.DECLINED)
    MatchFactory.create(status=Match.Status.EXPIRED)
    MatchFactory.create(status=Match.Status.CANCELLED)
    assert set(Match.objects.active()) == {proposed, pending, accepted}


def test_multiple_matches_per_registration_allowed() -> None:
    """There is no unique constraint — a registration can appear in multiple matches."""
    ambassador = RegistrationFactory.create(role=Registration.Role.AMBASSADOR)
    ref1 = RegistrationFactory.create(referee=True)
    ref2 = RegistrationFactory.create(referee=True)
    expires = datetime(2099, 1, 1, tzinfo=UTC)
    Match.objects.create(
        ambassador_registration=ambassador,
        referee_registration=ref1,
        expires_at=expires,
    )
    Match.objects.create(
        ambassador_registration=ambassador,
        referee_registration=ref2,
        expires_at=expires,
    )
    assert Match.objects.count() == 2


def test_match_side_of_raises_for_unrelated_registration() -> None:
    """Match.side_of raises ValueError when the registration is not a party."""
    match = MatchFactory.create()
    unrelated = RegistrationFactory.create(referee=True)
    with pytest.raises(ValueError, match=r"not a party on Match"):
        match.side_of(unrelated)


def test_match_to_string_includes_status() -> None:
    """Match.to_string includes the status label."""
    match = MatchFactory.create(declined=True)
    s = str(match)
    assert "Declined" in s


# ---------------------------------------------------------------------------
# Match.accept_side() — pure, in-memory model method (VERB-101 / ADR 0017)
# ---------------------------------------------------------------------------

# A tz-aware instant suitable for acceptance timestamps.
_WHEN = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)


def test_accept_side_first_accept_by_ambassador_goes_pending() -> None:
    """First accept by the ambassador sets the timestamp and PROPOSED → PENDING."""
    match = MatchFactory.create()

    result = match.accept_side(Match.Side.AMBASSADOR, _WHEN)

    assert result is match
    assert match.ambassador_accepted_at == _WHEN
    assert match.referee_accepted_at is None
    assert match.status == Match.Status.PENDING


def test_accept_side_first_accept_by_ambassador_does_not_persist() -> None:
    """accept_side mutates only the in-memory instance; it never saves."""
    match = MatchFactory.create()

    match.accept_side(Match.Side.AMBASSADOR, _WHEN)

    # Not saved: the DB row is unchanged.
    fresh = Match.objects.get(pk=match.pk)
    assert fresh.status == Match.Status.PROPOSED
    assert fresh.ambassador_accepted_at is None


def test_accept_side_first_accept_by_referee_goes_pending() -> None:
    """First accept by the referee sets the timestamp and PROPOSED → PENDING."""
    match = MatchFactory.create()

    result = match.accept_side(Match.Side.REFEREE, _WHEN)

    assert result is match
    assert match.referee_accepted_at == _WHEN
    assert match.ambassador_accepted_at is None
    assert match.status == Match.Status.PENDING


def test_accept_side_second_accept_transitions_to_accepted() -> None:
    """Second accept (match PENDING) sets the timestamp and status → ACCEPTED."""
    match = MatchFactory.create(pending=True)
    assert match.referee_accepted_at is None

    result = match.accept_side(Match.Side.REFEREE, _WHEN)

    assert result is match
    assert match.referee_accepted_at == _WHEN
    assert match.ambassador_accepted_at is not None
    assert match.status == Match.Status.ACCEPTED


def test_accept_side_idempotent_re_accept_keeps_timestamp_and_status() -> None:
    """Re-accepting an already-accepted side is a no-op for that side's timestamp."""
    match = MatchFactory.create()
    match.accept_side(Match.Side.AMBASSADOR, _WHEN)
    assert match.status == Match.Status.PENDING

    later = datetime(2026, 1, 2, 12, 0, 0, tzinfo=UTC)
    result = match.accept_side(Match.Side.AMBASSADOR, later)

    assert result is match
    # Timestamp is unchanged — still the first-accept value, not `later`.
    assert match.ambassador_accepted_at == _WHEN
    # Status is unchanged — still PENDING (referee has not accepted).
    assert match.status == Match.Status.PENDING


@pytest.mark.parametrize(
    "trait",
    ["accepted", "declined", "cancelled"],
)
def test_accept_side_raises_for_terminal_statuses(trait: str) -> None:
    """accept_side raises StateTransitionError for ACCEPTED, DECLINED, CANCELLED."""
    match = MatchFactory.create(**{trait: True})
    status_before = match.status

    with pytest.raises(StateTransitionError) as exc_info:
        match.accept_side(Match.Side.AMBASSADOR, _WHEN)

    assert exc_info.value.current == status_before
    assert exc_info.value.proposed == Match.Status.ACCEPTED
    assert exc_info.value.obj is match


def test_accept_side_raises_for_expired() -> None:
    """accept_side raises StateTransitionError when the match is already EXPIRED."""
    match = MatchFactory.create()
    match.status = Match.Status.EXPIRED
    match.save(update_fields=["status"])

    with pytest.raises(StateTransitionError) as exc_info:
        match.accept_side(Match.Side.AMBASSADOR, _WHEN)

    assert exc_info.value.current == Match.Status.EXPIRED
    assert exc_info.value.proposed == Match.Status.ACCEPTED
    assert exc_info.value.obj is match
