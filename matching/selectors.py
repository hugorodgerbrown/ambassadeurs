# Read-only presentation/context selectors derived from match and
# registration state.
#
# These functions never mutate state — they only read Match/Registration rows
# and shape them into the dicts the templates consume. Kept separate from
# matching/services.py (the transition-services module, which owns state
# mutation and side-effect dispatch) so that a purely presentational change
# here can never be confused with a domain transition.
#
# status_pill_for and match_status_context (VERB-116) are shared by
# accounts.views.account_detail and public.views.register_done so both
# surfaces render the identical Match status component. match_status_context
# calls matching.services.queue_position, which stays in services.py as a
# query helper alongside the rest of the matching-engine logic.
#
# queue_snapshot_context (VERB-145) shapes matching.services.queue_snapshot
# into the two-column context templates/includes/_queue_snapshot.html reads;
# the aggregate counting logic itself stays in services.py (this module never
# runs its own queries).

from __future__ import annotations

from datetime import datetime
from typing import TypedDict

from django.contrib.auth.models import User
from django.db.models import Q
from django.utils import timezone
from django.utils.translation import gettext as _

from .models import Match, Registration
from .pricing_config import matching_opens_at
from .services import queue_position, queue_snapshot


class StatusPill(TypedDict):
    """The Match status heading pill: a translated label and a tone suffix."""

    label: str
    tone: str


class MatchStatusContext(TypedDict):
    """The full render context for the Match status card (VERB-116).

    Every key that ``templates/accounts/partials/match_status.html`` reads.
    """

    registration: Registration | None
    status_pill: StatusPill
    match_state: str
    partner_first_name: str
    partner_accepted: bool
    queue_position: int | None
    can_rejoin: bool
    can_cancel: bool


def status_pill_for(
    registration: Registration | None,
    match_state: str,
) -> StatusPill:
    """Return the ``{label, tone}`` for the Match status heading pill.

    ``tone`` is the ``.tag-status--<tone>`` suffix; ``label`` is translated.
    ``match_state`` is one of ``none``, ``proposed``, ``pending``, ``accepted``.

    Covers every Registration.Status plus the no-registration and active-match
    cases. VERIFIED with no active match → "Queued" (muted); UNVERIFIED,
    WITHDRAWN, SUSPENDED → muted. An active match overrides the pill regardless
    of Registration.Status.

    Shared by the account page (``accounts.views.account_detail``) and the
    registration-confirmation page (``public.views.register_done``) so both
    surfaces render the same ``.tag-status`` pill from the same mapping
    (VERB-116).
    """
    if registration is None:
        return {"label": _("Queued"), "tone": "muted"}

    # Active-match states override the registration-status pill.
    if match_state == "proposed":
        return {"label": _("Pending"), "tone": "wait"}
    if match_state == "pending":
        return {"label": _("Pending"), "tone": "wait"}
    if match_state == "accepted":
        return {"label": _("Accepted"), "tone": "done"}

    # No active match — derive from pool standing.
    pills: dict[str, tuple[str, str]] = {
        Registration.Status.UNVERIFIED: (_("Unverified"), "muted"),
        Registration.Status.VERIFIED: (_("Queued"), "muted"),
        Registration.Status.PAUSED: (_("Paused"), "muted"),
        Registration.Status.WITHDRAWN: (_("Withdrawn"), "muted"),
        Registration.Status.SUSPENDED: (_("Suspended"), "muted"),
    }
    label, tone = pills.get(
        Registration.Status(registration.status),
        (registration.get_status_display(), "muted"),
    )
    return {"label": str(label), "tone": tone}


def match_status_context(user: User) -> MatchStatusContext:
    """Build the full render context for the Match status card, for ``user``.

    Returns every key ``templates/accounts/partials/match_status.html`` reads:
    ``registration``, ``status_pill``, ``match_state``, ``partner_first_name``,
    ``partner_accepted``, ``queue_position``, ``can_rejoin``, ``can_cancel``.

    Shared by ``accounts.views.account_detail`` and ``public.views.register_done``
    (VERB-116) so both surfaces render the identical Match status component —
    the registration engine runs synchronously inside ``register_participant``,
    so a user can already hold a PROPOSED (or later) match by the time they
    reach ``register_done``.

    Looks up the user's own ``Registration`` (``None`` if they have none, e.g.
    an admin user). Match progress (``match_state``) is derived from the active
    ``Match`` row (PROPOSED, PENDING, or ACCEPTED via ``Match.objects.active_at``),
    never from ``Registration.status`` (VERB-44 / ADR 0011). ``active_at``
    excludes a PROPOSED/PENDING match whose contact window has lapsed but which
    the hourly ``expire_matches`` sweep has not yet processed, so a lapsed,
    unswept match reads as inactive (VERB-113 parity).

    ``queue_position`` is only computed for a VERIFIED registration with no
    active match. ``can_rejoin``/``can_cancel`` are both True only when the
    registration is PAUSED with no active match.

    Args:
        user: The user whose match status to derive.

    Returns:
        The full Match status card context, keyed as above.
    """
    try:
        registration: Registration | None = Registration.objects.get(user=user)
    except Registration.DoesNotExist:
        registration = None

    active_match: Match | None = (
        Match.objects.active_at(timezone.now())
        .filter(
            Q(ambassador_registration__user=user) | Q(referee_registration__user=user)
        )
        .select_related(
            "ambassador_registration__user",
            "referee_registration__user",
        )
        .first()
    )

    match_state = "none"
    partner_first_name = ""
    partner_accepted = False

    if active_match is not None:
        if active_match.status == Match.Status.ACCEPTED:
            match_state = "accepted"
        elif active_match.status == Match.Status.PENDING:
            match_state = "pending"
        else:
            match_state = "proposed"

        # Identify which side this user is on to find the partner.
        if active_match.ambassador_registration is not None and (
            active_match.ambassador_registration.user_id == user.pk
        ):
            partner = active_match.referee_registration
            partner_accepted = active_match.referee_accepted_at is not None
        else:
            partner = active_match.ambassador_registration
            partner_accepted = active_match.ambassador_accepted_at is not None

        if partner is not None:
            partner_first_name = partner.user.first_name

    # Fall back to a generic noun when the partner has no first name on file.
    if not partner_first_name:
        partner_first_name = _("your partner")

    # Queue position — only computed for VERIFIED registrations without an active
    # match (pool members awaiting a pairing).
    position: int | None = None
    if (
        registration is not None
        and registration.status == Registration.Status.VERIFIED
        and active_match is None
    ):
        position = queue_position(registration)

    # A PAUSED registration with no active match is the recoverable state after
    # a decline or expiry. It drives both the "Rejoin the queue" and the
    # "Cancel & refund" (VERB-88) links on the account page — the same
    # condition, so it is computed once and read into both flags.
    paused_recoverable = (
        registration is not None
        and registration.status == Registration.Status.PAUSED
        and active_match is None
    )
    can_rejoin = paused_recoverable
    can_cancel = paused_recoverable

    return {
        "registration": registration,
        "status_pill": status_pill_for(registration, match_state),
        "match_state": match_state,
        "partner_first_name": partner_first_name,
        "partner_accepted": partner_accepted,
        "queue_position": position,
        "can_rejoin": can_rejoin,
        "can_cancel": can_cancel,
    }


# The pictograph renders one person glyph per waiting registrant (side columns)
# or one pair-of-people glyph per match (centre column). Above these caps the
# row would wrap into an unreadable block (and bloat the DOM), so the glyphs are
# capped and a ``scaled`` flag tells the template to show its "represents several
# people" caption; the header count stays exact. Early-season pools sit well
# under the caps, so the common case is one-glyph-per-person / per-pair.
_QUEUE_MAX_ICONS = 40
_QUEUE_MAX_PAIRS = 18


class QueueColumn(TypedDict):
    """One waiting role column in the queue visualisation (VERB-145).

    A side column shows only the *waiting* (unmatched) registrations of one role
    — matched people move to the central pairs column. ``glyphs`` is a list the
    template iterates to draw one person icon each (length capped at
    ``_QUEUE_MAX_ICONS``); ``count`` is the exact waiting total and stays the
    source of truth. ``scaled`` is True when ``count`` exceeds the cap and the
    glyphs are a sample.
    """

    count: int
    glyphs: list[int]
    scaled: bool


class QueueMatches(TypedDict):
    """The central matched-pairs column in the queue visualisation (VERB-145).

    Each match is one ambassador paired with one referee. ``count`` is the number
    of active matches (== matched ambassadors == matched referees) and drives the
    pair glyphs; ``people`` is ``2 * count`` — the headline figure, since the
    zone reports matched *people*, not matches. ``glyphs`` is a list the template
    iterates to draw one pair icon each (length capped at ``_QUEUE_MAX_PAIRS``);
    ``scaled`` is True when ``count`` exceeds the cap.
    """

    count: int
    people: int
    glyphs: list[int]
    scaled: bool


class QueueSnapshotContext(TypedDict):
    """The full render context for ``templates/includes/_queue_snapshot.html``.

    Three columns, left to right: ambassadors waiting, matched pairs, referees
    waiting. The ``is_open`` / ``opens_at`` / ``days_until_open`` keys describe
    whether matching has begun (VERB-83 open-date gate): before the open date the
    template shows a "matching begins on …" subheader and a countdown in place of
    the (necessarily empty) matched column.
    """

    ambassadors: QueueColumn
    matches: QueueMatches
    referees: QueueColumn
    is_open: bool
    opens_at: datetime
    days_until_open: int


def _capped(count: int, cap: int) -> tuple[list[int], bool]:
    """Return ``(glyphs, scaled)`` for a column of ``count`` items.

    ``glyphs`` is a list the template iterates once per icon — ``range(count)``
    when ``count`` is at or below ``cap``, otherwise ``range(cap)`` (a capped
    sample). ``scaled`` is True only in the capped case, so the template can flag
    that each icon then stands for more than one person/pair. The exact
    ``count`` is shown regardless.

    Args:
        count: The exact number of people (or pairs) in this column.
        cap: The maximum number of glyphs to draw.

    Returns:
        The list of glyph indices to render and whether it was capped.
    """
    return (list(range(min(count, cap))), count > cap)


def _waiting_column(count: int) -> QueueColumn:
    """Shape one role's waiting count into a ``QueueColumn``.

    Args:
        count: The exact number of waiting (unmatched) registrations.

    Returns:
        The fully-shaped waiting column.
    """
    glyphs, scaled = _capped(count, _QUEUE_MAX_ICONS)
    return {"count": count, "glyphs": glyphs, "scaled": scaled}


def queue_snapshot_context(now: datetime) -> QueueSnapshotContext:
    """Build the render context for the standalone queue visualisation.

    Calls ``matching.services.queue_snapshot`` and shapes it into three columns:
    ambassadors waiting, matched pairs, referees waiting. The two side columns
    carry only their waiting (unmatched) counts — matched registrations are
    represented once each as a pair in the centre column. The match count is the
    matched-ambassador count, which equals the matched-referee count and the
    active-match count (an active match always has exactly one VERIFIED
    ambassador and one VERIFIED referee); the matched column reports ``people``
    (``2 * matches``), not the number of matches.

    ``now`` is passed in (inversion of control, VERB-100) rather than read via
    ``timezone.now()`` so the open-date countdown is a pure function of its
    arguments and deterministic in tests. Matching is "open" once ``now`` reaches
    ``matching_opens_at()``; ``days_until_open`` is the whole-day countdown to
    that date (0 once open), computed in the active timezone.

    Args:
        now: The tz-aware instant to evaluate the open-date gate against.

    Returns:
        The full render context, including the open-date keys.
    """
    snapshot = queue_snapshot()
    match_count = snapshot.ambassadors_matched
    match_glyphs, match_scaled = _capped(match_count, _QUEUE_MAX_PAIRS)

    opens_at = matching_opens_at()
    is_open = now >= opens_at
    days_until_open = max(
        (timezone.localtime(opens_at).date() - timezone.localtime(now).date()).days,
        0,
    )

    return {
        "ambassadors": _waiting_column(snapshot.ambassadors_unmatched),
        "matches": {
            "count": match_count,
            "people": match_count * 2,
            "glyphs": match_glyphs,
            "scaled": match_scaled,
        },
        "referees": _waiting_column(snapshot.referees_unmatched),
        "is_open": is_open,
        "opens_at": opens_at,
        "days_until_open": days_until_open,
    }
