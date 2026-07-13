# Tests for the matching presentation/context selectors.

from datetime import UTC, datetime

import pytest
from django.test import override_settings

from matching.models import Match, Registration
from matching.selectors import (
    _QUEUE_MAX_ICONS,
    _QUEUE_MAX_PAIRS,
    _capped,
    build_queue_context,
    instant_match_role,
    match_status_context,
    queue_snapshot_context,
    status_pill_for,
)
from tests.accounts.factories import UserFactory
from tests.matching.factories import MatchFactory, RegistrationFactory

pytestmark = pytest.mark.django_db

# A fixed "now" well after the default test MATCHING_OPENS_AT (2020-01-01), so
# matching reads as open unless a test overrides the setting.
_NOW = datetime(2026, 7, 10, 12, 0, tzinfo=UTC)


# ---------------------------------------------------------------------------
# status_pill_for (VERB-116)
# ---------------------------------------------------------------------------


def test_status_pill_for_no_registration() -> None:
    """No registration renders a neutral 'Queued' (muted) pill."""
    assert status_pill_for(None, "none") == {"label": "Queued", "tone": "muted"}


@pytest.mark.parametrize(
    ("status", "label", "tone"),
    [
        (Registration.Status.UNVERIFIED, "Unverified", "muted"),
        (Registration.Status.VERIFIED, "Queued", "muted"),
        (Registration.Status.PAUSED, "Paused", "muted"),
        (Registration.Status.WITHDRAWN, "Withdrawn", "muted"),
        (Registration.Status.SUSPENDED, "Suspended", "muted"),
    ],
)
def test_status_pill_for_each_registration_status(
    status: str, label: str, tone: str
) -> None:
    """Each Registration.Status (no active match) maps to its own pill."""
    registration = RegistrationFactory.create(status=status)
    assert status_pill_for(registration, "none") == {"label": label, "tone": tone}


@pytest.mark.parametrize(
    ("match_state", "label", "tone"),
    [
        ("proposed", "Pending", "wait"),
        ("pending", "Pending", "wait"),
        ("accepted", "Accepted", "done"),
    ],
)
def test_status_pill_for_active_match_state_overrides_registration_status(
    match_state: str, label: str, tone: str
) -> None:
    """An active match_state overrides the pill regardless of Registration.Status.

    Exercised against every Registration.Status to confirm the override always
    wins, not just for VERIFIED.
    """
    for status in (
        Registration.Status.UNVERIFIED,
        Registration.Status.VERIFIED,
        Registration.Status.PAUSED,
        Registration.Status.WITHDRAWN,
        Registration.Status.SUSPENDED,
    ):
        registration = RegistrationFactory.create(status=status)
        assert status_pill_for(registration, match_state) == {
            "label": label,
            "tone": tone,
        }


# ---------------------------------------------------------------------------
# match_status_context (VERB-116)
# ---------------------------------------------------------------------------


def test_match_status_context_no_registration() -> None:
    """A user with no Registration gets a neutral, no-registration context."""
    user = UserFactory.create()
    context = match_status_context(user)
    assert context["registration"] is None
    assert context["status_pill"] == {"label": "Queued", "tone": "muted"}
    assert context["match_state"] == "none"
    assert context["queue_position"] is None
    assert context["can_rejoin"] is False
    assert context["can_cancel"] is False


def test_match_status_context_verified_no_match() -> None:
    """A VERIFIED registration with no active match reports its queue position."""
    reg = RegistrationFactory.create(status=Registration.Status.VERIFIED)
    context = match_status_context(reg.user)
    assert context["registration"] == reg
    assert context["match_state"] == "none"
    assert context["queue_position"] == 1
    assert context["can_rejoin"] is False
    assert context["can_cancel"] is False


def test_match_status_context_paused_no_match() -> None:
    """A PAUSED registration with no active match allows rejoin and cancel."""
    reg = RegistrationFactory.create(status=Registration.Status.PAUSED)
    context = match_status_context(reg.user)
    assert context["match_state"] == "none"
    assert context["queue_position"] is None
    assert context["can_rejoin"] is True
    assert context["can_cancel"] is True


def test_match_status_context_proposed_match() -> None:
    """An active PROPOSED match surfaces match_state and partner details."""
    reg = RegistrationFactory.create()
    partner_user = UserFactory.create(first_name="Bernard")
    partner = RegistrationFactory.create(referee=True, user=partner_user)
    MatchFactory.create(
        ambassador_registration=reg,
        referee_registration=partner,
        status=Match.Status.PROPOSED,
    )
    context = match_status_context(reg.user)
    assert context["match_state"] == "proposed"
    assert context["partner_first_name"] == "Bernard"
    assert context["partner_accepted"] is False


def test_match_status_context_referee_side() -> None:
    """The context resolves the partner correctly from the referee's side too."""
    reg = RegistrationFactory.create(referee=True)
    partner_user = UserFactory.create(first_name="Léa")
    partner = RegistrationFactory.create(user=partner_user)
    MatchFactory.create(
        ambassador_registration=partner,
        referee_registration=reg,
        status=Match.Status.PROPOSED,
    )
    context = match_status_context(reg.user)
    assert context["match_state"] == "proposed"
    assert context["partner_first_name"] == "Léa"


def test_match_status_context_lapsed_proposed_match_returns_none() -> None:
    """A lapsed, unswept PROPOSED match is treated as inactive (VERB-113 parity)."""
    reg = RegistrationFactory.create(status=Registration.Status.VERIFIED)
    MatchFactory.create(
        ambassador_registration=reg,
        status=Match.Status.PROPOSED,
        expires_at=datetime(2020, 1, 1, tzinfo=UTC),
    )
    context = match_status_context(reg.user)
    assert context["match_state"] == "none"
    # queue_position() is pool-eligibility-based (_without_active_match), which
    # still excludes a lapsed-but-unswept match (Invariant 3) — unlike
    # match_state, which uses active_at() and treats it as inactive.
    assert context["queue_position"] is None


# ---------------------------------------------------------------------------
# queue_snapshot_context (VERB-145)
# ---------------------------------------------------------------------------


def test_queue_snapshot_context_has_three_columns() -> None:
    """queue_snapshot_context returns the three columns plus the open-date keys."""
    context = queue_snapshot_context(_NOW)

    assert {"ambassadors", "matches", "referees", "instant_match_role"} <= set(context)
    assert set(context["ambassadors"]) == {"count", "glyphs", "truncated", "you_glyph"}
    assert set(context["matches"]) == {
        "count",
        "people",
        "glyphs",
        "truncated",
        "you_glyph",
    }


@pytest.mark.parametrize(
    ("is_open", "ambassadors", "referees", "expected"),
    [
        (True, 5, 0, "referee"),  # referees empty, ambassadors queue → referee instant
        (True, 0, 5, "ambassador"),  # ambassadors empty, referees queue → ambassador
        (True, 5, 5, ""),  # both queued → nobody instant
        (True, 0, 0, ""),  # empty pool → nobody instant
        (False, 5, 0, ""),  # matching not open → nobody instant
    ],
)
def test_instant_match_role(
    is_open: bool, ambassadors: int, referees: int, expected: str
) -> None:
    """instant_match_role names the empty side only when open with a queue opposite."""
    assert instant_match_role(is_open, ambassadors, referees) == expected


def test_queue_snapshot_context_reflects_pool_counts() -> None:
    """queue_snapshot_context splits the pool into waiting sides + matched pairs.

    Two matched pairs (each an ambassador + referee) plus one extra waiting
    ambassador and two extra waiting referees. The matched column reports
    ``people`` (2 x the two matches = 4), not the number of matches.
    """
    RegistrationFactory.create(status=Registration.Status.VERIFIED)  # waiting amb.
    RegistrationFactory.create(referee=True, status=Registration.Status.VERIFIED)
    RegistrationFactory.create(referee=True, status=Registration.Status.VERIFIED)
    MatchFactory.create()  # PROPOSED — one matched ambassador + one matched referee
    MatchFactory.create(pending=True)  # PENDING — another matched pair

    context = queue_snapshot_context(_NOW)

    assert context["ambassadors"] == {
        "count": 1,
        "glyphs": [0],
        "truncated": False,
        "you_glyph": None,
    }
    assert context["referees"] == {
        "count": 2,
        "glyphs": [0, 1],
        "truncated": False,
        "you_glyph": None,
    }
    assert context["matches"] == {
        "count": 2,
        "people": 4,
        "glyphs": [0, 1],
        "truncated": False,
        "you_glyph": None,
    }


def test_queue_snapshot_context_empty_pool() -> None:
    """An empty pool yields zero counts, no glyphs, and no truncation anywhere."""
    context = queue_snapshot_context(_NOW)

    for column in (context["ambassadors"], context["referees"], context["matches"]):
        assert column["count"] == 0
        assert column["glyphs"] == []
        assert column["truncated"] is False
    assert context["matches"]["people"] == 0


@override_settings(MATCHING_OPENS_AT="2020-01-01T00:00:00+00:00")
def test_queue_snapshot_context_open_has_no_countdown() -> None:
    """Past the open date, matching reads as open and the countdown is zero."""
    context = queue_snapshot_context(_NOW)

    assert context["is_open"] is True
    assert context["days_until_open"] == 0


@override_settings(MATCHING_OPENS_AT="2026-10-01T00:00:00+00:00")
def test_queue_snapshot_context_not_open_counts_down() -> None:
    """Before the open date, matching is closed and the day countdown is exposed."""
    context = queue_snapshot_context(_NOW)

    assert context["is_open"] is False
    # 2026-07-10 → 2026-10-01 is 83 calendar days.
    assert context["days_until_open"] == 83


@pytest.mark.parametrize(
    ("count", "cap", "expected_glyphs", "expected_truncated"),
    [
        (0, 5, 0, False),
        (3, 5, 3, False),
        (5, 5, 5, False),  # exactly at the cap — every item drawn, no ellipsis
        (6, 5, 4, True),  # over by one — (cap-1) glyphs + an ellipsis
        (200, 5, 4, True),
    ],
)
def test_capped(
    count: int, cap: int, expected_glyphs: int, expected_truncated: bool
) -> None:
    """_capped draws every item up to the cap, then (cap-1) glyphs + an ellipsis."""
    glyphs, truncated = _capped(count, cap)

    assert glyphs == list(range(expected_glyphs))
    assert truncated is expected_truncated


def test_build_queue_context_truncates_large_columns() -> None:
    """A pool past the caps draws (cap-1) glyphs plus an ellipsis; count stays exact."""
    context = build_queue_context(
        ambassadors_waiting=200,
        referees_waiting=0,
        matches=50,
        is_open=True,
        opens_at=_NOW,
        days_until_open=0,
    )

    ambassadors = context["ambassadors"]
    assert ambassadors["count"] == 200
    assert len(ambassadors["glyphs"]) == _QUEUE_MAX_ICONS - 1
    assert ambassadors["truncated"] is True

    matches = context["matches"]
    assert matches["count"] == 50
    assert matches["people"] == 100
    assert len(matches["glyphs"]) == _QUEUE_MAX_PAIRS - 1
    assert matches["truncated"] is True


def test_queue_snapshot_context_caps_are_wired() -> None:
    """The waiting and match columns use their respective glyph caps.

    Drives ``_capped`` at the two module caps directly (creating hundreds of rows
    would be slow and add nothing) to confirm the wiring: waiting columns cap at
    ``_QUEUE_MAX_ICONS``, the matches column at ``_QUEUE_MAX_PAIRS``.
    """
    # count = cap + 5 → (cap - 1) glyphs + a trailing ellipsis.
    assert _capped(_QUEUE_MAX_ICONS + 5, _QUEUE_MAX_ICONS) == (
        list(range(_QUEUE_MAX_ICONS - 1)),
        True,
    )
    assert _capped(_QUEUE_MAX_PAIRS + 5, _QUEUE_MAX_PAIRS) == (
        list(range(_QUEUE_MAX_PAIRS - 1)),
        True,
    )


# ---------------------------------------------------------------------------
# you_glyph highlighting (VERB-145 follow-up)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("you_index", "expected_you_glyph"),
    [
        (0, 0),  # first slot
        (5, 5),  # mid-grid
        (_QUEUE_MAX_ICONS - 2, _QUEUE_MAX_ICONS - 2),  # last drawn slot when truncated
        (_QUEUE_MAX_ICONS - 1, None),  # the ellipsis slot itself — dropped
        (_QUEUE_MAX_ICONS, None),  # beyond the grid entirely
        (-1, None),  # negative index
        (None, None),  # no current user in this column
    ],
)
def test_build_queue_context_waiting_column_you_glyph(
    you_index: int | None, expected_you_glyph: int | None
) -> None:
    """The ambassadors column's ``you_glyph`` reflects ``you_index`` when routed.

    Uses a pool large enough (``_QUEUE_MAX_ICONS + 5``, so truncated) to exercise
    both the truncated grid's last drawn slot and its ellipsis slot.
    """
    context = build_queue_context(
        ambassadors_waiting=_QUEUE_MAX_ICONS + 5,
        referees_waiting=0,
        matches=0,
        is_open=True,
        opens_at=_NOW,
        days_until_open=0,
        you_role="ambassadors",
        you_index=you_index,
    )
    assert context["ambassadors"]["you_glyph"] == expected_you_glyph
    # Routing is exclusive — the other columns never see this you_index.
    assert context["referees"]["you_glyph"] is None
    assert context["matches"]["you_glyph"] is None


def test_build_queue_context_you_role_ambassadors_routes_only_there() -> None:
    """``you_role="ambassadors"`` sets only the ambassadors column's you_glyph."""
    context = build_queue_context(
        ambassadors_waiting=3,
        referees_waiting=3,
        matches=3,
        is_open=True,
        opens_at=_NOW,
        days_until_open=0,
        you_role="ambassadors",
        you_index=1,
    )
    assert context["ambassadors"]["you_glyph"] == 1
    assert context["referees"]["you_glyph"] is None
    assert context["matches"]["you_glyph"] is None


def test_build_queue_context_you_role_referees_routes_only_there() -> None:
    """``you_role="referees"`` sets only the referees column's you_glyph."""
    context = build_queue_context(
        ambassadors_waiting=3,
        referees_waiting=3,
        matches=3,
        is_open=True,
        opens_at=_NOW,
        days_until_open=0,
        you_role="referees",
        you_index=2,
    )
    assert context["ambassadors"]["you_glyph"] is None
    assert context["referees"]["you_glyph"] == 2
    assert context["matches"]["you_glyph"] is None


def test_build_queue_context_you_role_matches_routes_only_there() -> None:
    """``you_role="matches"`` sets only the matches column's you_glyph."""
    context = build_queue_context(
        ambassadors_waiting=3,
        referees_waiting=3,
        matches=3,
        is_open=True,
        opens_at=_NOW,
        days_until_open=0,
        you_role="matches",
        you_index=0,
    )
    assert context["ambassadors"]["you_glyph"] is None
    assert context["referees"]["you_glyph"] is None
    assert context["matches"]["you_glyph"] == 0


def test_build_queue_context_no_you_role_leaves_all_none() -> None:
    """``you_role=""`` (the default) leaves every column's you_glyph None."""
    context = build_queue_context(
        ambassadors_waiting=3,
        referees_waiting=3,
        matches=3,
        is_open=True,
        opens_at=_NOW,
        days_until_open=0,
    )
    assert context["ambassadors"]["you_glyph"] is None
    assert context["referees"]["you_glyph"] is None
    assert context["matches"]["you_glyph"] is None


def test_build_queue_context_out_of_range_index_leaves_all_none() -> None:
    """An out-of-range you_index with no you_role still leaves every column None."""
    context = build_queue_context(
        ambassadors_waiting=3,
        referees_waiting=3,
        matches=3,
        is_open=True,
        opens_at=_NOW,
        days_until_open=0,
        you_role="",
        you_index=100,
    )
    assert context["ambassadors"]["you_glyph"] is None
    assert context["referees"]["you_glyph"] is None
    assert context["matches"]["you_glyph"] is None


def test_build_queue_context_matches_you_glyph_uses_pairs_cap_not_icons_cap() -> None:
    """``you_role="matches"`` bounds against ``_QUEUE_MAX_PAIRS`` (16), not 20.

    With exactly ``_QUEUE_MAX_PAIRS`` pairs (no truncation), every slot is drawn,
    so index ``_QUEUE_MAX_PAIRS - 1`` (15) — the last slot — is kept. With more
    pairs than the cap (truncated), index ``_QUEUE_MAX_PAIRS`` (16) is the
    ellipsis slot and is dropped, even though it would still fall within
    ``_QUEUE_MAX_ICONS`` (20) — proving the bound uses the pairs cap, not the
    icons cap.
    """
    context_kept = build_queue_context(
        ambassadors_waiting=0,
        referees_waiting=0,
        matches=_QUEUE_MAX_PAIRS,
        is_open=True,
        opens_at=_NOW,
        days_until_open=0,
        you_role="matches",
        you_index=_QUEUE_MAX_PAIRS - 1,
    )
    assert context_kept["matches"]["you_glyph"] == _QUEUE_MAX_PAIRS - 1

    context_dropped = build_queue_context(
        ambassadors_waiting=0,
        referees_waiting=0,
        matches=_QUEUE_MAX_PAIRS + 5,
        is_open=True,
        opens_at=_NOW,
        days_until_open=0,
        you_role="matches",
        you_index=_QUEUE_MAX_PAIRS,
    )
    assert context_dropped["matches"]["you_glyph"] is None
