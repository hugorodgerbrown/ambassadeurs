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

from typing import TypedDict

from django.contrib.auth.models import User
from django.db.models import Q
from django.utils import timezone
from django.utils.translation import gettext as _

from .models import Match, Registration
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


# The unit pictograph renders one person icon per registrant. Above this cap
# the row would wrap into an unreadable block (and bloat the DOM), so the icons
# are scaled down to a proportional sample of this many while the legend keeps
# the exact counts. Early-season pools sit well under the cap, so the common
# case is one-icon-per-person and exact.
_QUEUE_MAX_ICONS = 40


class QueueColumn(TypedDict):
    """One role's column in the queue visualisation (VERB-145).

    ``icons`` is the unit-pictograph payload: a list of ``"matched"`` /
    ``"waiting"`` state strings the template renders as person glyphs (matched
    first). ``scaled`` is True when the column's ``total`` exceeds
    ``_QUEUE_MAX_ICONS`` and the icons are a proportional sample rather than
    one-per-person; ``matched`` / ``unmatched`` / ``total`` stay exact
    regardless.
    """

    role_label: str
    is_referee: bool
    matched: int
    unmatched: int
    total: int
    icons: list[str]
    scaled: bool


class QueueSnapshotContext(TypedDict):
    """The full render context for ``templates/includes/_queue_snapshot.html``.

    ``columns`` holds exactly two entries, ambassador first then referee.
    """

    columns: list[QueueColumn]


def _pictograph(matched: int, total: int) -> tuple[list[str], bool]:
    """Return ``(icon_states, scaled)`` for a column's unit pictograph.

    Each entry is ``"matched"`` or ``"waiting"`` (matched first). When
    ``total`` is at or below ``_QUEUE_MAX_ICONS`` the pictograph is exact — one
    icon per person. Above the cap it is scaled to ``_QUEUE_MAX_ICONS`` icons
    that preserve the matched proportion (rounded), and ``scaled`` is True; the
    caller's exact counts remain the source of truth.

    Args:
        matched: Count of matched registrations in this column.
        total: Total (matched + waiting) registrations in this column.

    Returns:
        The list of per-icon state strings and whether it was scaled down.
    """
    if total <= 0:
        return [], False
    if total <= _QUEUE_MAX_ICONS:
        return ["matched"] * matched + ["waiting"] * (total - matched), False
    shown_matched = round(matched / total * _QUEUE_MAX_ICONS)
    return (
        ["matched"] * shown_matched + ["waiting"] * (_QUEUE_MAX_ICONS - shown_matched),
        True,
    )


def _queue_column(
    role_label: str,
    is_referee: bool,
    matched: int,
    unmatched: int,
) -> QueueColumn:
    """Shape one role's counts into a ``QueueColumn`` with its pictograph.

    Args:
        role_label: The translated role name (``Registration.Role.<X>.label``).
        is_referee: True for the referee column (drives the template's blue
            ``role-card--referee`` theming); False for the ambassador column.
        matched: Count of that role's matched registrations.
        unmatched: Count of that role's waiting (unmatched) registrations.

    Returns:
        The fully-shaped column, including the unit-pictograph icon list.
    """
    total = matched + unmatched
    icons, scaled = _pictograph(matched, total)
    return {
        "role_label": role_label,
        "is_referee": is_referee,
        "matched": matched,
        "unmatched": unmatched,
        "total": total,
        "icons": icons,
        "scaled": scaled,
    }


def queue_snapshot_context() -> QueueSnapshotContext:
    """Build the render context for the standalone queue visualisation.

    Calls ``matching.services.queue_snapshot`` for the counts and shapes them
    into two columns (ambassador, referee, in that order), each carrying its
    role label (``Registration.Role.<X>.label``, already ``gettext_lazy``), its
    matched/waiting counts and total, and a unit-pictograph icon list. The
    person glyphs and legend labels live in the template.
    """
    snapshot = queue_snapshot()
    return {
        "columns": [
            _queue_column(
                str(Registration.Role.AMBASSADOR.label),
                is_referee=False,
                matched=snapshot.ambassadors_matched,
                unmatched=snapshot.ambassadors_unmatched,
            ),
            _queue_column(
                str(Registration.Role.REFEREE.label),
                is_referee=True,
                matched=snapshot.referees_matched,
                unmatched=snapshot.referees_unmatched,
            ),
        ]
    }
