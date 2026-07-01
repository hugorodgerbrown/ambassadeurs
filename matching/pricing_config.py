# Season pricing/timing configuration readers (VERB-81/82).
#
# Deferred matching and the tiered prepaid registration fee are both
# configured via environment variables (MATCHING_OPENS_AT and
# REGISTRATION_FEE_TIERS respectively) rather than database rows, mirroring
# the REGISTRATION_OPENS_AT / CONTACT_WINDOW_HOURS pattern (ADR 0005).
#
# Settings are read inside each function body (not at import time) so that
# @override_settings works correctly in tests — this mirrors
# matching.services.is_registration_open.
#
# This module ships config parsing only; nothing in the codebase consumes it
# yet (the matching gate lands in VERB-83, the fee stamped at signup in
# VERB-84). See docs/decisions/0014-deferred-matching-prepaid-fee.md.

from __future__ import annotations

import logging
from datetime import date, datetime

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.utils import timezone
from django.utils.dateparse import parse_datetime

logger = logging.getLogger(__name__)


def matching_opens_at() -> datetime:
    """Return the moment matching begins, as a timezone-aware datetime.

    Parses ``settings.MATCHING_OPENS_AT`` (a full ISO 8601 datetime string)
    via ``django.utils.dateparse.parse_datetime``. A naive result is made
    aware in the current timezone. If the string cannot be parsed, logs an
    error and returns a far-future aware datetime — a fail-safe default that
    reads as "matching is not yet open", so a misconfiguration can never
    cause matches to be proposed prematurely.

    Returns:
        A timezone-aware ``datetime``.
    """
    parsed = parse_datetime(settings.MATCHING_OPENS_AT)
    if parsed is None:
        logger.error(
            "MATCHING_OPENS_AT=%r is not a valid ISO 8601 datetime; "
            "treating matching as not yet open (far-future fallback).",
            settings.MATCHING_OPENS_AT,
        )
        # 9999-01-01 is a deliberate far-future sentinel (not datetime.max,
        # which can overflow on some make_aware/DST-fold paths).
        return timezone.make_aware(datetime(9999, 1, 1))
    if timezone.is_naive(parsed):
        parsed = timezone.make_aware(parsed)
    return parsed


def fee_chf_for(on_date: date) -> int:
    """Return the prepaid registration fee, in CHF, for ``on_date``.

    Parses ``settings.REGISTRATION_FEE_TIERS`` via ``_parse_fee_tiers`` and
    resolves ``on_date`` to the amount of the last threshold whose date is
    on or before it. A date before the first threshold — or an empty
    schedule — resolves to ``0`` (free).

    Args:
        on_date: The date to resolve a fee for (typically the registration date).

    Returns:
        The fee in whole CHF (0 if free).

    Raises:
        ImproperlyConfigured: propagated from ``_parse_fee_tiers`` if
            ``REGISTRATION_FEE_TIERS`` is malformed.
    """
    tiers = _parse_fee_tiers(settings.REGISTRATION_FEE_TIERS)
    fee = 0
    # tiers is sorted ascending by date, so we can break on the first
    # threshold that falls after on_date.
    for threshold_date, chf in tiers:
        if threshold_date <= on_date:
            fee = chf
        else:
            break
    return fee


def _parse_fee_tiers(raw: str) -> list[tuple[date, int]]:
    """Parse a ``REGISTRATION_FEE_TIERS``-style schedule string.

    The expected format is a comma-separated list of ``YYYY-MM-DD:CHF``
    entries, e.g. ``"2026-10-01:5,2026-11-01:10,2026-12-01:20"``. Each
    entry means "from this date onward the fee is N CHF".

    Args:
        raw: The raw schedule string (typically ``settings.REGISTRATION_FEE_TIERS``).

    Returns:
        A list of ``(threshold_date, chf)`` tuples sorted by date.
        Empty or whitespace-only input returns ``[]``.

    Raises:
        ImproperlyConfigured: if any entry is malformed — missing the
            ``:`` separator, an invalid date, or a non-integer or negative
            CHF amount. Pricing misconfiguration must fail loud rather
            than silently charge the wrong amount.
    """
    raw = raw.strip()
    if not raw:
        return []

    tiers: list[tuple[date, int]] = []
    for entry in raw.split(","):
        entry = entry.strip()
        if ":" not in entry:
            raise ImproperlyConfigured(
                f"Malformed REGISTRATION_FEE_TIERS entry {entry!r}: "
                "expected 'YYYY-MM-DD:CHF'."
            )
        date_part, _sep, chf_part = entry.partition(":")
        date_part = date_part.strip()
        chf_part = chf_part.strip()

        try:
            threshold_date = date.fromisoformat(date_part)
        except ValueError as exc:
            raise ImproperlyConfigured(
                f"Malformed REGISTRATION_FEE_TIERS entry {entry!r}: "
                f"{date_part!r} is not a valid YYYY-MM-DD date."
            ) from exc

        try:
            chf = int(chf_part)
        except ValueError as exc:
            raise ImproperlyConfigured(
                f"Malformed REGISTRATION_FEE_TIERS entry {entry!r}: "
                f"{chf_part!r} is not a valid integer CHF amount."
            ) from exc
        if chf < 0:
            raise ImproperlyConfigured(
                f"Malformed REGISTRATION_FEE_TIERS entry {entry!r}: "
                "CHF amount must not be negative."
            )

        tiers.append((threshold_date, chf))

    tiers.sort(key=lambda tier: tier[0])
    return tiers
