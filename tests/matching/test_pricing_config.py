# Tests for matching.pricing_config: deferred-matching and fee-tier helpers.

from datetime import UTC, date, datetime, timedelta

import pytest
from django.core.exceptions import ImproperlyConfigured
from django.test import override_settings
from django.utils import timezone

from matching.pricing_config import fee_chf_for, matching_opens_at

pytestmark = pytest.mark.django_db


# ---------------------------------------------------------------------------
# fee_chf_for
# ---------------------------------------------------------------------------

PRODUCTION_TIERS = "2026-10-01:5,2026-11-01:10,2026-12-01:20"


@override_settings(REGISTRATION_FEE_TIERS=PRODUCTION_TIERS)
@pytest.mark.parametrize(
    ("on_date", "expected_chf"),
    [
        (date(2026, 9, 30), 0),
        (date(2026, 10, 1), 5),
        (date(2026, 10, 31), 5),
        (date(2026, 11, 1), 10),
        (date(2026, 12, 1), 20),
    ],
)
def test_fee_chf_for_boundary_table(on_date: date, expected_chf: int) -> None:
    """fee_chf_for resolves the last threshold whose date <= on_date."""
    assert fee_chf_for(on_date) == expected_chf


@override_settings(REGISTRATION_FEE_TIERS="")
def test_fee_chf_for_empty_schedule_is_free() -> None:
    """An empty REGISTRATION_FEE_TIERS resolves every date to 0 (free)."""
    assert fee_chf_for(date(2026, 11, 1)) == 0


@override_settings(REGISTRATION_FEE_TIERS="2026-10-01:5")
def test_fee_chf_for_single_tier_schedule() -> None:
    """A single-tier schedule is free before the threshold, priced after."""
    assert fee_chf_for(date(2026, 9, 1)) == 0
    assert fee_chf_for(date(2026, 10, 1)) == 5
    assert fee_chf_for(date(2027, 1, 1)) == 5


@override_settings(REGISTRATION_FEE_TIERS="2026-10-01:5,not-a-date:10")
def test_fee_chf_for_bad_date_raises() -> None:
    """A malformed date in a schedule entry raises ImproperlyConfigured."""
    with pytest.raises(ImproperlyConfigured):
        fee_chf_for(date(2026, 11, 1))


@override_settings(REGISTRATION_FEE_TIERS="2026-10-01:five")
def test_fee_chf_for_non_integer_chf_raises() -> None:
    """A non-integer CHF amount raises ImproperlyConfigured."""
    with pytest.raises(ImproperlyConfigured):
        fee_chf_for(date(2026, 11, 1))


@override_settings(REGISTRATION_FEE_TIERS="2026-10-01:-5")
def test_fee_chf_for_negative_chf_raises() -> None:
    """A negative CHF amount raises ImproperlyConfigured."""
    with pytest.raises(ImproperlyConfigured):
        fee_chf_for(date(2026, 11, 1))


@override_settings(REGISTRATION_FEE_TIERS="2026-10-015")
def test_fee_chf_for_missing_colon_raises() -> None:
    """An entry missing the ':' separator raises ImproperlyConfigured."""
    with pytest.raises(ImproperlyConfigured):
        fee_chf_for(date(2026, 11, 1))


# ---------------------------------------------------------------------------
# matching_opens_at
# ---------------------------------------------------------------------------


@override_settings(MATCHING_OPENS_AT="2026-11-01T00:00:00+00:00")
def test_matching_opens_at_valid_iso_datetime() -> None:
    """A valid ISO datetime with an offset parses to the expected aware value."""
    result = matching_opens_at()
    assert result == datetime(2026, 11, 1, tzinfo=UTC)
    assert timezone.is_aware(result)


@override_settings(MATCHING_OPENS_AT="2026-11-01T00:00:00")
def test_matching_opens_at_naive_input_is_made_aware() -> None:
    """A naive ISO datetime (no offset) is made aware, not rejected."""
    result = matching_opens_at()
    assert timezone.is_aware(result)
    assert result.replace(tzinfo=None) == datetime(2026, 11, 1)


@override_settings(MATCHING_OPENS_AT="not-a-datetime")
def test_matching_opens_at_unparseable_returns_far_future_sentinel() -> None:
    """An unparseable value fails safe: far-future, tz-aware, reads as not-yet-open."""
    result = matching_opens_at()
    assert timezone.is_aware(result)
    assert result > timezone.now() + timedelta(days=365 * 100)
