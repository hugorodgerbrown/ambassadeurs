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

from __future__ import annotations

from typing import TypedDict

from django.contrib.auth.models import User
from django.db.models import Q
from django.utils import timezone
from django.utils.translation import gettext as _

from .models import Match, Registration
from .services import queue_position


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
