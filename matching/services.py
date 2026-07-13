# Matching-domain service functions.
#
# Side effects (User / Registration / Match creation) are orchestrated here
# and called inline from views — never via Django signals (CLAUDE.md "Models").
#
# The matching engine runs synchronously inside register_participant: after
# creating the registration (inside an atomic transaction), propose_match is
# called to attempt an immediate pairing with a waiting counterpart.
#
# The open-date gate (VERB-83): propose_match is a no-op before
# matching_opens_at() (from matching.pricing_config, VERB-82). The gate lives
# inside propose_match — the single chokepoint every proposing caller
# (register_participant, confirm_registration, rejoin_queue, and any future
# one) passes through — so a pre-open email-confirmation or rejoin can never
# leak a match. Registrations still verify and enqueue before the open date;
# they are simply not paired until it. run_matching drains the built-up queue
# at/after the open date (it reuses propose_match, so the gate is satisfied by
# the time it runs) and can then run on a schedule to complement the rolling
# synchronous behaviour for late entrants.
#
# record_acceptance and record_decline implement the per-party response step of
# the post-match confirmation workflow (ADR 0007 / VERB-18). Both are atomic
# and call core.services.record_transition inline for the audit log.
#
# The PENDING Match state (VERB-44 / ADR 0011): the first acceptance moves
# the match from PROPOSED → PENDING (a real transition, logged). The second
# acceptance moves PENDING → ACCEPTED. record_acceptance (VERB-101) delegates
# the source-state guard and the timestamp/status mutation to the
# Match.accept model method (which derives the accepting side from the
# registration's role) — see the model/service split note below.
#
# withdraw_acceptance (VERB-43 / ADR 0010) is the inverse of the first accept:
# while a match is PENDING and the other side has not accepted, the viewing
# party clears their own *_accepted_at timestamp — a clean, no-penalty un-accept
# (PENDING → PROPOSED) — and a transition log row is written.
#
# expire_lapsed_matches is the periodic sweep entry point (VERB-100): given a
# cutoff, it fetches the lapsed candidate pks and, per match, delegates to
# expire_match for the actual transition. expire_match transitions
# the match to EXPIRED (via the Match.expire model method) and records the
# transition, then calls handle_lapsed_participants, which fans out to
# handle_lapsed_participant for each side. A kept-faith party (already
# accepted) re-queues to the front (requeue_to_front, delegating to the
# Registration.requeue model method); a non-responder is paused
# (pause_registration, delegating to Registration.pause). This is the
# model/service boundary established for the expiry transition (docs/decisions/0017):
# model methods (Match.expire,
# Registration.pause, Registration.requeue, Match.accept) validate their
# own source state and raise core.exceptions.StateTransitionError on an
# illegal transition (fail hard, low in the stack) rather than saving; they
# never save, touch another object, or fire a side effect. Service functions
# own the save, record_transition, cross-object coordination, and email
# dispatch, and do not re-check the state conditions the model methods
# already guard (catch high, not double-check). The optimistic row lock
# (select_for_update) these helpers once took has been dropped (VERB-106):
# concurrent writers on the same registration/match are rare enough that an
# occasional lost update is cheaper to reconcile by hand than to serialise
# against, and the model methods' StateTransitionError guard still rejects an
# illegal transition. The helpers now mutate the caller's instance directly (no
# lock, no re-fetch, no sync-back). The matching engine (propose_match) keeps
# its candidate-pool lock — that guards the 1:1 invariant, not a state
# transition. See docs/decisions/0018. All five match transitions now
# follow this shape: expire (Match.expire, VERB-100), accept (Match.accept,
# VERB-101), decline (Match.decline, VERB-102), withdraw-acceptance
# (Match.withdraw_acceptance, VERB-103), and no-show/cancel (Match.cancel plus
# Registration.suspend, VERB-104).
#
# pause_registration (VERB-74 / ADR 0013) replaces requeue_to_back and
# record_flake_and_requeue. Decline or non-response → PAUSED; the two-strike
# flake model is retired. rejoin_queue is the self-service re-entry from the
# account page (PAUSED → VERIFIED, priority -= 1, propose_match).
#
# report_no_show (VERB-21) implements the post-accept no-show path (ADR 0007):
# the reporter's registration is re-queued to the front; the accused is
# suspended (no reporter PII in the notification — Invariant 1). The match is
# transitioned ACCEPTED → CANCELLED (renamed from ABANDONED).
#
# Notification dispatch (VERB-107 / ADR 0018): the five real transition
# functions (propose_match, record_acceptance, record_decline, expire_match,
# report_no_show) are each decorated with @has_side_effects(LABEL) — see
# matching/side_effects.py for the label constants, the per-recipient
# @is_side_effect_of handlers (one recipient each, deriving who to notify by
# walking the mutated Match rather than a loose registration argument), and
# the DRY email-render helpers. Dispatch is deferred to transaction.on_commit
# by the library itself, so a rolled-back transition never emails anyone —
# this replaces the previous hand-written
# transaction.on_commit(functools.partial(send_x, ...)) call sites.
# MatchingConfig.ready() imports both modules so the decorators register at
# startup (the library does not autodiscover).
#
# queue_position and total_accepted_matches (VERB-40) are read-only query helpers
# that return a participant's ordinal position in the eligible same-role pool and
# the season-wide count of mutually-accepted matches respectively.
#
# Deposit transitions (VERB-87) are driven inline from the match lifecycle (no
# signals — CLAUDE.md). On mutual accept, record_acceptance captures both
# parties' held deposits (HELD → CAPTURED). On a post-accept no-show,
# report_no_show forfeits only the accused's held deposit (HELD → FORFEITED) —
# the reporter's stays HELD. close_season is the season-end sweep that refunds
# every still-HELD deposit whose registration never reached an ACCEPTED match
# and is not suspended (HELD → REFUNDED). Expiry/decline-induced PAUSE leaves the
# deposit HELD and refundable (lenient model, ADR 0013). All three reuse the
# billing.services.payments transitions; free-tier registrations (fee_chf=0) have
# no Payment row and are skipped gracefully.
#
# Product analytics (VERB-124): register_participant and confirm_registration
# each fire a best-effort core.observability.capture_event call, deferred to
# transaction.on_commit so a rolled-back transition never sends an event. The
# match-acceptance events (match_accepted / match_confirmed) live alongside the
# notification handlers in matching/side_effects.py, bound to the same
# MATCH_ACCEPTED label.
#
# queue_snapshot (VERB-145) is a read-only aggregate for the standalone queue
# visualisation. Per role, "matched" is derived as total − unmatched rather
# than counted directly, so the two rows are always exact complements of the
# VERIFIED total for that role — it reuses RegistrationQuerySet.verified() /
# .ambassadors() / .referees() / ._without_active_match() rather than
# re-deriving the "active match" definition.

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import NamedTuple, cast

from django.conf import settings
from django.contrib.auth.models import User
from django.db import transaction
from django.db.models import Case, IntegerField, Q, QuerySet, Value, When
from django.utils import timezone
from django.utils.dateparse import parse_date
from side_effects.decorators import has_side_effects

from billing.models import Payment
from billing.services.payments import (
    InvalidPaymentTransition,
    capture,
    forfeit,
    refund,
)
from core.emails import normalise_email
from core.exceptions import StateTransitionError
from core.observability import capture_event
from core.services import record_transition

from .models import Match, Registration
from .pricing_config import fee_chf_for, matching_opens_at
from .side_effects import (
    MATCH_ACCEPTED,
    MATCH_DECLINED,
    MATCH_EXPIRED,
    MATCH_NO_SHOW,
    MATCH_PROPOSED,
)

logger = logging.getLogger(__name__)


def is_registration_open() -> bool:
    """Return True if today falls within the configured registration window.

    Reads REGISTRATION_OPENS_AT and REGISTRATION_CLOSES_AT from settings as
    dates (``YYYY-MM-DD``); any time/timezone component is ignored. Both bounds
    are inclusive, compared against today in the project timezone
    (``timezone.localdate()``). If either string is not a valid date the window
    is treated as closed (fail-safe).
    """
    today = timezone.localdate()
    opens_on = parse_date(settings.REGISTRATION_OPENS_AT)
    closes_on = parse_date(settings.REGISTRATION_CLOSES_AT)
    if opens_on is None or closes_on is None:
        logger.error(
            "REGISTRATION_OPENS_AT / REGISTRATION_CLOSES_AT is not a valid date; "
            "treating window as closed."
        )
        return False
    return opens_on <= today <= closes_on


def is_eligible_pair(ambassador: Registration, referee: Registration) -> bool:
    """Return True if ``ambassador`` and ``referee`` form an eligible match.

    Eligibility rules (checked here):
    - Opposite roles (ambassador vs. referee).
    - Both have VERIFIED pool standing.
    - Ambassador holds SEASONAL, ANNUAL, or MONT4 prior pass.
    - Referee holds NONE prior pass (genuinely new).

    The active-match exclusion (neither holds a PROPOSED, PENDING, or ACCEPTED
    match) is enforced upstream by ``eligible_ambassadors()`` /
    ``eligible_referees()`` querysets and ``propose_match``; it is not
    re-checked here to avoid an extra DB query per candidate pair.
    """
    if ambassador.role != Registration.Role.AMBASSADOR:
        return False
    if referee.role != Registration.Role.REFEREE:
        return False
    if ambassador.status != Registration.Status.VERIFIED:
        return False
    if referee.status != Registration.Status.VERIFIED:
        return False
    if ambassador.prior_pass not in (
        Registration.PriorPass.SEASONAL,
        Registration.PriorPass.ANNUAL,
        Registration.PriorPass.MONT4,
    ):
        return False
    if referee.prior_pass != Registration.PriorPass.NONE:
        return False
    return True


def queue_position(registration: Registration) -> int | None:
    """Return the 1-based position of ``registration`` in the eligible pool.

    Returns ``None`` if the registration is not in ``VERIFIED`` status, or if
    it is not a member of the eligible pool (e.g. an ambassador with an
    ineligible ``prior_pass`` value, or already holding an active match). The
    position is only meaningful for participants actively queuing in an eligible
    state.

    Picks the same-role eligible pool (``eligible_ambassadors`` or
    ``eligible_referees``) and counts the rows ranked strictly ahead using the
    same ``-priority, created_at`` ordering used by the matching engine. The
    result is that count plus 1.

    Args:
        registration: The registration whose position to determine.

    Returns:
        1-based queue ordinal, or ``None`` if not VERIFIED or not in the
        eligible pool.
    """
    if registration.status != Registration.Status.VERIFIED:
        return None

    if registration.role == Registration.Role.AMBASSADOR:
        pool = Registration.objects.eligible_ambassadors()
    else:
        pool = Registration.objects.eligible_referees()

    if not pool.filter(pk=registration.pk).exists():
        return None

    ahead = pool.filter(
        Q(priority__gt=registration.priority)
        | Q(priority=registration.priority, created_at__lt=registration.created_at)
    ).count()
    return ahead + 1


def total_accepted_matches() -> int:
    """Return the total count of mutually-accepted matches this season.

    Counts all ``Match`` rows in ``ACCEPTED`` status. Used to show participants
    how many pairs have already been successfully matched.
    """
    return Match.objects.filter(status=Match.Status.ACCEPTED).count()


class QueueSnapshot(NamedTuple):
    """Per-role pool counts for the standalone queue visualisation (VERB-145).

    ``*_unmatched`` and ``*_matched`` are exact complements of the VERIFIED
    total for that role — see ``queue_snapshot`` for the derivation.
    """

    ambassadors_unmatched: int
    ambassadors_matched: int
    referees_unmatched: int
    referees_matched: int


def queue_snapshot() -> QueueSnapshot:
    """Return the current pool state, per role, for the queue visualisation.

    Read-only. For each role, "unmatched" is the count of VERIFIED
    registrations holding no active (PROPOSED/PENDING/ACCEPTED) match — the
    same eligible-pool definition used elsewhere via
    ``RegistrationQuerySet._without_active_match()``. "Matched" is derived as
    the VERIFIED total minus "unmatched" rather than counted directly, so the
    two rows are always exact complements of that total (a terminal-match
    registration, e.g. DECLINED/EXPIRED/CANCELLED, counts as unmatched, since
    it holds no active match).
    """
    ambassadors_unmatched = (
        Registration.objects.verified().ambassadors()._without_active_match().count()
    )
    ambassadors_total = Registration.objects.verified().ambassadors().count()
    referees_unmatched = (
        Registration.objects.verified().referees()._without_active_match().count()
    )
    referees_total = Registration.objects.verified().referees().count()

    return QueueSnapshot(
        ambassadors_unmatched=ambassadors_unmatched,
        ambassadors_matched=ambassadors_total - ambassadors_unmatched,
        referees_unmatched=referees_unmatched,
        referees_matched=referees_total - referees_unmatched,
    )


def _rank_candidates(
    candidates: QuerySet[Registration], location: str
) -> QuerySet[Registration]:
    """Order counterpart ``candidates`` by the engine's ranking rule.

    The single source of truth for counterpart ordering, shared by the live
    ``propose_match`` path and the ``run_matching`` dry-run simulation so the two
    can never drift: a shared ``preferred_location`` first, then ``priority``
    descending (higher priority = closer to the front), then ``created_at``
    ascending (FIFO within an equal priority). ``location`` is the proposing
    party's ``preferred_location``; a candidate matching it is ranked ahead of
    one that does not. The preference is applied as a 0/1 annotation so it works
    as the leading ``ORDER BY`` key.
    """
    return candidates.annotate(
        location_match=Case(
            When(preferred_location=location, then=Value(1)),
            default=Value(0),
            output_field=IntegerField(),
        )
    ).order_by("-location_match", "-priority", "created_at")


@has_side_effects(MATCH_PROPOSED, run_on_exit=lambda match: match is not None)
def propose_match(registration: Registration) -> Match | None:
    """Attempt to pair ``registration`` with an eligible counterpart.

    Must be called inside an existing ``transaction.atomic()`` block — uses
    ``select_for_update()`` to prevent duplicate matches under concurrency.

    Ranking: shared ``preferred_location`` first, then ``priority`` descending
    (higher priority = closer to the front), then ``created_at`` ascending
    (FIFO within the same priority).

    Returns the created Match, or None if no eligible counterpart is waiting.
    No-ops (returns None) if ``registration`` is not itself eligible.

    Registrations no longer flip to MATCHED when a match is proposed (VERB-44).
    Pool availability is managed by RegistrationQuerySet._without_active_match,
    which excludes registrations already holding a non-terminal match.

    The open-date gate (VERB-83): returns None without proposing when
    ``timezone.now()`` is before ``matching_opens_at()``. This is the single
    chokepoint that defers matching for every caller, so a pre-open
    registration, email confirmation, or queue rejoin enqueues without leaking
    a match. The built-up queue is drained by ``run_matching`` at the open date.
    """
    if timezone.now() < matching_opens_at():
        logger.debug(
            "propose_match: matching not yet open; skipping proposal for "
            "registration pk=%s.",
            registration.pk,
        )
        return None

    if registration.role == Registration.Role.AMBASSADOR:
        # Guard: the calling ambassador must hold a valid prior pass.
        if registration.prior_pass not in (
            Registration.PriorPass.SEASONAL,
            Registration.PriorPass.ANNUAL,
            Registration.PriorPass.MONT4,
        ):
            return None
        if registration.status != Registration.Status.VERIFIED:
            return None
        # Look for an eligible VERIFIED referee without an active match.
        candidates = (
            Registration.objects.eligible_referees()
            .exclude(pk=registration.pk)
            .select_for_update()
        )
    else:
        # Guard: the calling referee must have no prior pass.
        if registration.prior_pass != Registration.PriorPass.NONE:
            return None
        if registration.status != Registration.Status.VERIFIED:
            return None
        # Look for an eligible VERIFIED ambassador without an active match.
        candidates = (
            Registration.objects.eligible_ambassadors()
            .exclude(pk=registration.pk)
            .select_for_update()
        )

    if not candidates.exists():
        return None

    # Rank via the engine's shared ordering (see _rank_candidates) so the live
    # path and the run_matching dry-run can never disagree.
    counterpart = _rank_candidates(candidates, registration.preferred_location).first()
    if counterpart is None:
        return None

    # Determine ambassador / referee FK assignments.
    if registration.role == Registration.Role.AMBASSADOR:
        ambassador_reg = registration
        referee_reg = counterpart
    else:
        ambassador_reg = counterpart
        referee_reg = registration

    expires_at = timezone.now() + timedelta(hours=settings.CONTACT_WINDOW_HOURS)
    match = Match.objects.create(
        ambassador_registration=ambassador_reg,
        referee_registration=referee_reg,
        expires_at=expires_at,
    )

    # Reload with both sides' users prefetched: the match_proposed handlers
    # (matching.side_effects) read match.ambassador_registration.user.email /
    # match.referee_registration.user.email straight off the returned match
    # (accessed via return_value), which is a fresh FK descriptor lookup
    # regardless of what `registration`/`counterpart` happened to have
    # cached — without this each proposal fires an N+1 lazy-load per side.
    # propose_match is decorated with @has_side_effects, an untyped decorator
    # (django-side-effects ships no py.typed marker), so mypy widens the
    # inferred type of every name reassigned inside this function to Any —
    # hence the explicit re-typed local rather than a bare reassignment.
    reloaded: Match = Match.objects.select_related(
        "ambassador_registration__user",
        "referee_registration__user",
    ).get(pk=match.pk)
    match = reloaded

    # Registrations are NOT flipped to MATCHED (VERB-44). Pool availability is
    # enforced by RegistrationQuerySet._without_active_match instead.
    logger.info(
        "Proposed match pk=%s: ambassador reg pk=%s, referee reg pk=%s",
        match.pk,
        ambassador_reg.pk,
        referee_reg.pk,
    )

    return match


def requeue_to_front(registration: Registration) -> None:
    """Re-queue a kept-faith / wronged party: status=VERIFIED, priority += 1.

    Used after a counterpart declines or a match expires where this party had
    already accepted (or the window lapsed without action from the other side).
    Not a penalty — priority is only adjusted here, never on pause.

    The pure mutation is delegated to ``Registration.requeue`` (model logic,
    VERB-100); this function persists it. No row lock is taken (VERB-106): the
    mutation is applied directly to the passed-in instance, so ``priority += 1``
    is computed from its in-memory value. ``priority=1`` is the front-of-queue
    amount.
    """
    registration.requeue(priority=1).save(update_fields=["status", "priority"])
    logger.info(
        "Re-queued registration pk=%s to front (priority=%s)",
        registration.pk,
        registration.priority,
    )


def pause_registration(registration: Registration) -> None:
    """Set a registration to PAUSED — removed from the pool, no priority change.

    Used after a participant declines a match or fails to respond within the
    contact window (VERB-74 / ADR 0013). The registration row is retained; the
    participant can rejoin the queue themselves from their account page via
    ``rejoin_queue``. No priority is changed here; priority adjustment happens
    on rejoin (priority -= 1 each time they re-enter).

    Replaces the former ``requeue_to_back`` (decline path) and
    ``record_flake_and_requeue`` (non-response path). The two-strike flake
    model is retired.

    The pure mutation is delegated to ``Registration.pause`` (model logic,
    VERB-100), which guards its own source state (VERIFIED only); this function
    persists it. No row lock is taken (VERB-106): the mutation is applied
    directly to the passed-in instance.
    """
    registration.pause().save(update_fields=["status"])
    logger.info(
        "Paused registration pk=%s (out of pool; may self-rejoin)",
        registration.pk,
    )


def rejoin_queue(registration: Registration) -> None:
    """Transition a PAUSED registration back to VERIFIED and attempt matching.

    Mirrors ``confirm_registration``: runs inside ``transaction.atomic()``
    because ``propose_match`` must (it holds the candidate-pool lock). No lock
    is taken on the registration row itself (VERB-106): the status guard and
    mutation act on the passed-in instance directly. If the registration is not
    PAUSED the function is a no-op (idempotent guard). On success:
      - status → VERIFIED
      - priority -= 1 (one step toward the back each time they re-enter)
      - ``propose_match`` is called to attempt an immediate pairing

    This is the self-service re-entry point exposed via ``accounts:rejoin_queue``
    (VERB-74 / ADR 0013).

    Args:
        registration: The registration to re-activate.
    """
    with transaction.atomic():
        if registration.status != Registration.Status.PAUSED:
            logger.info(
                "rejoin_queue called on non-PAUSED registration pk=%s "
                "(status=%s); no-op.",
                registration.pk,
                registration.status,
            )
            return

        registration.status = Registration.Status.VERIFIED
        registration.priority -= 1
        registration.save(update_fields=["status", "priority"])

        propose_match(registration)

    logger.info(
        "rejoin_queue: registration pk=%s PAUSED → VERIFIED (priority=%s)",
        registration.pk,
        registration.priority,
    )


def suspend_for_no_show(registration: Registration) -> None:
    """Suspend a registration following a post-accept no-show report.

    Sets status=SUSPENDED. The two-strike flake model is retired (VERB-74); a
    no-show report suspends unconditionally with a single step.

    The pure mutation is delegated to ``Registration.suspend`` (model logic,
    VERB-104 / ADR 0017), which guards its own source state (VERIFIED only);
    this function persists it. No row lock is taken (VERB-106): the mutation is
    applied directly to the passed-in instance.
    """
    registration.suspend().save(update_fields=["status"])
    logger.info(
        "Suspended registration pk=%s for post-accept no-show",
        registration.pk,
    )


def accept_match(match: Match, registration: Registration) -> Match:
    """Record an acceptance (VERB-92) — the accept-side service entrypoint.

    The symmetric counterpart of ``decline_match``, but with no cross-object
    orchestration of its own: an acceptance neither pauses nor re-queues a
    registration, and its notifications ride the transition itself.
    ``record_acceptance`` is decorated with ``@has_side_effects(MATCH_ACCEPTED)``
    (VERB-107): on mutual accept (``ACCEPTED``) the PII-reveal confirmation email
    is sent to both parties; on the first accept (``PENDING``) the party yet to
    respond is nudged. Both are handled by the ``matching.side_effects`` handlers
    bound to that label, deferred to ``transaction.on_commit`` so a rolled-back
    accept never emails anyone. This function stays as the view-facing entrypoint
    (symmetric with ``decline_match``, so callers never reach past the service
    layer into the ``record_*`` transition) and to re-narrow the return type:
    ``@has_side_effects`` is untyped (django-side-effects ships no ``py.typed``),
    so mypy widens ``record_acceptance``'s inferred return to ``Any``.

    Args:
        match: The match being accepted.
        registration: The registration (ambassador or referee) accepting.

    Returns:
        The updated ``Match`` instance.

    Raises:
        StateTransitionError: propagated from ``record_acceptance`` if match
            is not PROPOSED or PENDING.
    """
    return cast(Match, record_acceptance(match, registration))


def decline_match(match: Match, registration: Registration) -> Match:
    """Record a decline, pause the decliner, and re-queue the other party.

    Calls ``record_decline`` — decorated with
    ``@has_side_effects(MATCH_DECLINED)`` (VERB-107), so the kept-faith party
    is notified via the ``matching.side_effects`` handler bound to that label,
    deferred to ``transaction.on_commit`` — then:
    - Pauses the decliner's registration (``pause_registration``). The User and
      Registration rows are retained; the participant can rejoin from their
      account page (VERB-74 / ADR 0013).
    - Re-queues the other party to the front of the pool (``requeue_to_front``).
      No PII and no reason are disclosed in the notification (Invariant 1).

    All three steps run inside a single outer ``transaction.atomic()`` block so
    that a crash between steps cannot leave a partial state (e.g. match DECLINED
    but decliner still VERIFIED). ``record_decline`` opens its own nested atomic
    (a savepoint); ``pause_registration`` and ``requeue_to_front`` apply their
    single-row mutation directly within the outer block (no inner lock —
    VERB-106).

    Args:
        match: The match being declined.
        registration: The registration (ambassador or referee) declining.

    Returns:
        The updated ``Match`` instance.

    Raises:
        StateTransitionError: propagated from ``record_decline`` if match is
            not PROPOSED or PENDING.
    """
    side = match.side_of(registration)

    with transaction.atomic():
        match = record_decline(match, registration)

        # Determine the other party.
        # Both FKs are non-null on PROPOSED/PENDING matches.
        if side == Match.Side.AMBASSADOR:
            other = match.referee_registration
        else:
            other = match.ambassador_registration

        pause_registration(registration)
        requeue_to_front(other)

    logger.info(
        "decline_match: match pk=%s DECLINED by registration pk=%s "
        "(paused); other party (pk=%s) queued to front.",
        match.pk,
        registration.pk,
        other.pk,
    )
    return match


def register_participant(
    *,
    role: str,
    first_name: str,
    last_name: str,
    prior_pass: str,
    email: str = "",
    user: User | None = None,
    preferred_location: str = "",
    preferred_language: str = "",
    nationality: str = "",
    phone: str = "",
    accepted_terms: list[str] | None = None,
    status: str = Registration.Status.VERIFIED,
    registration_country: str = "",
    registration_region: str = "",
    marketing_properties: dict[str, object] | None = None,
) -> Registration:
    """Enrol a participant in the pool and return the Registration.

    With no ``user`` (the combined-form flow) a passwordless ``User`` is
    created or reused, keyed on the lowercased email as username. With a
    ``user`` (authenticated path) that user is reused and their name kept
    current.

    ``accepted_terms`` is the ordered list of consent statement texts accepted
    by the participant (eligibility declaration first, then T&C); it is
    persisted on ``Registration.accepted_terms`` alongside ``terms_accepted_at``.

    ``status`` defaults to VERIFIED (immediate pool entry). Pass
    ``status=Registration.Status.UNVERIFIED`` for the combined-form path where
    the registration must be email-confirmed before it enters the pool — an
    UNVERIFIED registration is *never* matched (Invariant 2).

    ``registration_country`` and ``registration_region`` are geolocation fields
    derived from the client IP at registration time (admin-only, never shown to
    participants). Both default to empty strings when geolocation is unavailable
    (e.g. private/local IP or missing GeoLite2 database). The raw IP must never
    be passed here — resolve it in the view layer and discard it after lookup.

    ``marketing_properties`` (VERB-147, ADR 0023) is an optional dict of derived
    marketing-attribution properties — typically the output of
    ``core.marketing.marketing_event_properties`` — merged into the
    ``registration`` PostHog event. This function has no request access, so the
    caller (``public.services.register_or_resend_participant``) is responsible
    for deriving it from the session. Must never carry raw click IDs (fbclid,
    gclid, ...); only a derived ``source`` and ``utm_*`` values.

    After creating a VERIFIED registration, calls ``propose_match`` to attempt
    an immediate pairing. The whole function runs inside a single transaction;
    ``propose_match`` uses ``select_for_update`` for concurrency safety.
    """
    with transaction.atomic():
        if user is None:
            email = normalise_email(email)
            user, created = User.objects.get_or_create(
                username=email,
                defaults={
                    "email": email,
                    "first_name": first_name,
                    "last_name": last_name,
                },
            )
            if created:
                user.set_unusable_password()
                user.save(update_fields=["password"])
        elif user.first_name != first_name or user.last_name != last_name:
            user.first_name = first_name
            user.last_name = last_name
            user.save(update_fields=["first_name", "last_name"])

        registration = Registration.objects.create(
            user=user,
            role=role,
            prior_pass=prior_pass,
            phone=phone,
            preferred_location=preferred_location,
            preferred_language=preferred_language,
            nationality=nationality,
            accepted_terms=accepted_terms or [],
            terms_accepted_at=timezone.now() if accepted_terms else None,
            status=status,
            registration_country=registration_country,
            registration_region=registration_region,
            # Prepaid fee locked at signup from today's tier (VERB-84); frozen
            # thereafter — never recomputed against a later tier.
            fee_chf=fee_chf_for(timezone.localdate()),
        )

        # Only propose a match for VERIFIED registrations; UNVERIFIED rows must
        # never enter the matching engine (Invariant 2).
        if status == Registration.Status.VERIFIED:
            propose_match(registration)

        # Product analytics (VERB-124): fire only on a successful commit, so a
        # rolled-back registration attempt never sends an event. Marketing
        # attribution (VERB-147) is merged in when the caller supplied it —
        # derived source/utm_* only, never raw click IDs.
        registered_user = user
        transaction.on_commit(
            lambda: capture_event(
                str(registered_user.pk),
                "registration",
                {
                    "role": role,
                    "prior_pass": prior_pass,
                    "status": status,
                    **(marketing_properties or {}),
                },
            )
        )

    logger.info("Registered user pk=%s as %s (status=%s)", user.pk, role, status)
    return registration


def confirm_registration(registration: Registration) -> Registration:
    """Transition an UNVERIFIED registration to VERIFIED and trigger matching.

    Runs inside ``transaction.atomic()`` because ``propose_match`` must (it
    holds the candidate-pool lock). No lock is taken on the registration row
    itself (VERB-106): the status guard and mutation act on the passed-in
    instance directly. If the registration is not UNVERIFIED (already confirmed,
    or an invalid state), the function is a no-op and returns the unchanged row
    — the caller is responsible for treating a non-UNVERIFIED result as an
    invalid/used token.

    After the status flip, ``propose_match`` is called to attempt an immediate
    pairing. The instance is returned.
    """
    with transaction.atomic():
        if registration.status != Registration.Status.UNVERIFIED:
            # Already confirmed or in an unexpected state; no-op.
            logger.info(
                "confirm_registration called on non-UNVERIFIED registration pk=%s "
                "(status=%s); no-op.",
                registration.pk,
                registration.status,
            )
            return registration

        registration.status = Registration.Status.VERIFIED
        registration.save(update_fields=["status", "updated_at"])

        propose_match(registration)

        # Product analytics (VERB-124): fire only on a successful commit.
        confirmed_registration = registration
        transaction.on_commit(
            lambda: capture_event(
                str(confirmed_registration.user.pk),
                "email_verified",
                {"role": confirmed_registration.role},
            )
        )

    logger.info("Confirmed registration pk=%s: UNVERIFIED → VERIFIED", registration.pk)
    return registration


def handle_lapsed_participant(registration: Registration, kept_faith: bool) -> None:
    """Apply the per-side outcome of a lapsed match to one participant.

    Per-side outcome logic (VERB-74 / ADR 0013):
    - ``kept_faith=True`` (the side had already accepted, i.e. ``*_accepted_at``
      is not None) → ``requeue_to_front``.
    - ``kept_faith=False`` (the side had not responded by expiry) →
      ``pause_registration`` (removed from pool; may self-rejoin).

    The re-queued / window-expired notification email is no longer sent from
    here (VERB-107): it is dispatched by the ``matching.side_effects``
    handlers bound to ``expire_match``'s ``@has_side_effects`` label, which
    derive the per-side copy from the match's own ``*_accepted_at`` fields
    rather than from this function's ``kept_faith`` argument.

    Role-agnostic: works identically for an ambassador or a referee
    registration.

    Args:
        registration: The participant's registration.
        kept_faith: Whether this side had already accepted the match.
    """
    if kept_faith:
        requeue_to_front(registration)
    else:
        pause_registration(registration)


def handle_lapsed_participants(match: Match) -> None:
    """Apply the lapsed-match outcome to both parties on ``match``.

    Delegates to ``handle_lapsed_participant`` once per side, passing whether
    that side had already accepted (kept faith) at the point of expiry.

    Args:
        match: The match that has just been transitioned to EXPIRED.
    """
    # Both FKs are non-null; assertions satisfy mypy.
    assert match.ambassador_registration is not None
    assert match.referee_registration is not None

    handle_lapsed_participant(
        match.ambassador_registration,
        kept_faith=match.ambassador_accepted_at is not None,
    )
    handle_lapsed_participant(
        match.referee_registration,
        kept_faith=match.referee_accepted_at is not None,
    )


@has_side_effects(MATCH_EXPIRED)
def expire_match(match: Match) -> None:
    """Transition one already-locked, lapsed match to EXPIRED and re-queue.

    Orchestration for a single match: must be called with ``match`` already
    fetched inside an outer ``transaction.atomic()`` block (see
    ``expire_lapsed_matches``, which owns the per-match exception isolation). No
    row lock is taken (VERB-106).

    Transitions the match to EXPIRED via the ``Match.expire`` model method,
    persists it, records the transition, and calls
    ``handle_lapsed_participants`` to apply the per-side re-queue/pause outcome.
    Decorated with ``@has_side_effects(MATCH_EXPIRED)`` (VERB-107): the
    ``matching.side_effects`` handlers bound to that label notify each side,
    picking requeued-vs-window-expired copy from that side's own
    ``*_accepted_at`` on the mutated ``match``.

    ``Match.expire()`` is the single guard on the source state — it validates
    ``match.status`` itself and raises ``StateTransitionError`` (fail hard,
    low in the stack) if the match is not PROPOSED or PENDING. This function
    does not re-check the condition; the caller (``expire_lapsed_matches``)
    catches ``StateTransitionError`` to treat an already-transitioned match
    (a benign concurrency race) as a skip.

    Args:
        match: The locked, candidate match to expire.

    Raises:
        StateTransitionError: propagated from ``Match.expire`` if the match is
            not PROPOSED or PENDING.
    """
    status_before = match.status
    match.expire().save(update_fields=["status", "updated_at"])
    record_transition(
        match,
        "status",
        before=status_before,
        after=match.status,
    )

    handle_lapsed_participants(match)

    # Both FKs are non-null; assertions satisfy mypy.
    assert match.ambassador_registration is not None
    assert match.referee_registration is not None
    logger.info(
        "Expired match pk=%s (ambassador reg pk=%s accepted=%s, "
        "referee reg pk=%s accepted=%s)",
        match.pk,
        match.ambassador_registration.pk,
        match.ambassador_accepted_at is not None,
        match.referee_registration.pk,
        match.referee_accepted_at is not None,
    )


def expire_lapsed_matches(cutoff: datetime) -> int:
    """Expire all PROPOSED or PENDING matches past their contact window.

    Selects candidate PKs up front (``Match.objects.lapsed(cutoff=cutoff)``),
    then processes each match in its own ``transaction.atomic()`` block so that
    one bad match does not abort the whole sweep. No row lock is taken
    (VERB-106): a concurrent accept/decline racing the sweep is handled by the
    ``StateTransitionError`` catch below, not by serialising on the row. The
    per-match orchestration (the EXPIRED transition and the per-side
    re-queue/pause outcome) is delegated to ``expire_match``.

    ``cutoff`` is the tz-aware "now" the caller has read (inversion of
    control, VERB-100) — see ``matching.management.commands.expire_matches``,
    which passes ``timezone.now()``.

    Two exception paths, fail-hard-low / catch-high (ADR 0017):
    - ``StateTransitionError`` is the benign, expected race — another worker
      or an accept/decline changed the match's status between the candidate
      PK query and this loop's fetch. Logged at debug and skipped without
      counting as a failure.
    - Any other exception is a real failure: logged at error level (with
      traceback) and skipped, so one bad match does not abort the sweep.

    Args:
        cutoff: The tz-aware instant to treat as "now" for the lapsed-match query.

    Returns:
        The number of matches that were transitioned to EXPIRED in this run.
    """
    candidate_pks = list(
        Match.objects.lapsed(cutoff=cutoff).values_list("pk", flat=True)
    )
    expired_count = 0

    for pk in candidate_pks:
        try:
            with transaction.atomic():
                # select_related the registrations' users too: the
                # match_expired handlers (matching.side_effects) read
                # registration.user.email directly, so without this each
                # expiry fires an N+1 lazy-load per match in the sweep.
                match = Match.objects.select_related(
                    "ambassador_registration__user",
                    "referee_registration__user",
                ).get(pk=pk)
                expire_match(match)
                expired_count += 1
        except StateTransitionError as exc:
            logger.debug("Skipping match pk=%s: no longer expirable (%s)", pk, exc)
        except Exception:
            # Deliberate catch-all: isolate one match's failure so a single bad
            # row cannot abort the sweep. logger.exception records it for triage.
            logger.exception("Error expiring match pk=%s; skipping", pk)

    return expired_count


def _simulate_run_matching() -> int:
    """Return how many matches ``run_matching`` would propose, writing nothing.

    Greedily pairs the eligible ambassador pool (ordered ``-priority,
    created_at`` — the same order the commit path proposes in) against the
    eligible referee pool, consuming each referee as it is taken so no
    registration is paired twice. Each ambassador's counterpart is chosen with
    ``_rank_candidates`` — the identical ordering the live ``propose_match``
    uses — so the dry-run count is exactly what a real run would create.

    Returns:
        The number of matches that would be proposed.
    """
    ambassadors = list(
        Registration.objects.eligible_ambassadors().order_by("-priority", "created_at")
    )
    consumed: set[int] = set()
    would_propose = 0

    for ambassador in ambassadors:
        counterpart = _rank_candidates(
            Registration.objects.eligible_referees().exclude(pk__in=consumed),
            ambassador.preferred_location,
        ).first()
        if counterpart is None:
            break
        consumed.add(counterpart.pk)
        would_propose += 1

    return would_propose


def run_matching(*, commit: bool) -> tuple[int, int]:
    """Drain the waiting pool, proposing eligible matches until none remain.

    Walks the eligible ambassador pool in the engine's ``-priority,
    created_at`` order and proposes a match for each via ``propose_match``,
    which selects the best eligible counterpart (shared location, then
    priority, then FIFO) and excludes registrations already holding an active
    match. Because ``propose_match`` re-queries the pool each call, a single
    linear pass drains every pairable ambassador — each successful proposal
    removes both parties from the eligible querysets.

    This is the batch entry point used to clear the queue that builds up before
    the open date (when the ``propose_match`` gate defers all pairing) and to
    run on a schedule thereafter. It reuses ``propose_match`` unchanged, so the
    open-date gate is satisfied only when this runs at/after the open date.

    Read-only unless ``commit`` is True (management-command rules): with
    ``commit=False`` it reports how many matches it *would* propose without
    writing anything; with ``commit=True`` it proposes for real. Each real
    proposal is isolated in its own ``transaction.atomic()`` block so one
    failure cannot abort the whole drain; failures are counted and surfaced so
    the caller can exit non-zero on a partial failure.

    Args:
        commit: When False (default for callers), simulate and count only.
            When True, create the matches.

    Returns:
        A ``(proposed, failed)`` tuple. In dry-run mode ``failed`` is always 0
        and ``proposed`` is the would-propose count.
    """
    if not commit:
        would_propose = _simulate_run_matching()
        logger.info(
            "run_matching (dry-run): would propose %s match(es).", would_propose
        )
        return would_propose, 0

    ambassador_pks = list(
        Registration.objects.eligible_ambassadors()
        .order_by("-priority", "created_at")
        .values_list("pk", flat=True)
    )
    proposed = 0
    failed = 0

    for pk in ambassador_pks:
        try:
            with transaction.atomic():
                # Re-fetch inside the transaction: an earlier proposal in this
                # run may have consumed this ambassador as a counterpart, or
                # left the pool otherwise changed.
                ambassador = Registration.objects.get(pk=pk)
                match = propose_match(ambassador)
                if match is not None:
                    proposed += 1
        except Exception:
            # Deliberate catch-all: isolate one ambassador's failure so a single
            # bad row cannot abort the drain. logger.exception records it for triage.
            failed += 1
            logger.exception(
                "run_matching: error proposing a match for ambassador pk=%s; skipping",
                pk,
            )

    logger.info("run_matching: proposed %s match(es), %s failure(s).", proposed, failed)
    return proposed, failed


def withdraw_acceptance(match: Match, registration: Registration) -> Match:
    """Clear ``registration``'s acceptance on a PENDING ``match``.

    A clean, no-penalty un-accept: while the match is ``PENDING`` (one side
    has accepted) and the *other* side has not yet accepted, the viewing party
    may retract their own acceptance. This transitions the match ``PENDING →
    PROPOSED`` (a real status change logged to ``StateTransitionLog``) and
    returns them to the actionable ``proposed`` view (VERB-43 / ADR 0010).

    Only the accepting side's ``*_accepted_at`` timestamp is cleared. Nothing
    is re-queued and no penalty is applied — withdrawing differs from a decline
    or a non-response, which both pause the registration (VERB-74 / ADR 0013).

    The guard that the other side has not accepted is what keeps the operation
    safe: if both sides had accepted the match would already be ``ACCEPTED`` (a
    terminal, contact-revealed state), so there is no window in which a
    withdrawal could un-reveal PII.

    The source-state guards (match is PENDING; this side has accepted) and the
    field mutations are delegated to the ``Match.withdraw_acceptance`` model
    method (model logic, VERB-103 / ADR 0017), which raises
    ``StateTransitionError`` on an illegal source state (fail hard, low in the
    stack). This function does not re-check those conditions; it owns the save
    and audit-log row. No row lock is taken (VERB-106); it acts on the passed-in
    match instance directly. Both ``*_accepted_at`` fields are listed in
    ``update_fields`` (only one is ever changed) to keep the persistence
    boundary independent of which side withdrew.

    Args:
        match: The match to withdraw acceptance from.
        registration: The registration (ambassador or referee) withdrawing.

    Returns:
        The updated ``Match`` instance.

    Raises:
        StateTransitionError: propagated from ``Match.withdraw_acceptance`` if
            ``match.status`` is not ``PENDING``, or if this side has not
            accepted (nothing to withdraw).
    """
    with transaction.atomic():
        status_before = match.status
        match.withdraw_acceptance(registration).save(
            update_fields=[
                "ambassador_accepted_at",
                "referee_accepted_at",
                "status",
                "updated_at",
            ]
        )
        record_transition(
            match,
            "status",
            before=status_before,
            after=match.status,
        )

        logger.info(
            "Match pk=%s acceptance withdrawn by %s (registration pk=%s); "
            "PENDING → PROPOSED",
            match.pk,
            match.side_of(registration),
            registration.pk,
        )

    return match


@has_side_effects(MATCH_ACCEPTED)
def record_acceptance(match: Match, registration: Registration) -> Match:
    """Record that ``registration`` has accepted ``match``.

    On the first accept (match is PROPOSED), the accepting side's
    ``*_accepted_at`` timestamp is set and the match transitions
    ``PROPOSED → PENDING`` — a ``StateTransitionLog`` row is written.

    On the second accept (match is PENDING), both sides now have a timestamp.
    The match transitions ``PENDING → ACCEPTED`` and one ``StateTransitionLog``
    row is written for ``Match.status``. Registration statuses are no longer
    transitioned here (VERB-44: pool standing is independent of match progress).
    Both parties' held deposits are captured inline (HELD → CAPTURED, reason
    SUCCESSFUL_MATCH) via ``billing.services.payments.capture`` — VERB-87. A
    free-tier registration (``fee_chf=0``) has no Payment and is skipped.

    Re-accepting an already-accepted side is a no-op for that timestamp (the
    existing value is kept) so callers can safely retry without double-counting.

    ``Match.accept()`` is the single guard on the source state — it validates
    ``match.status`` itself and raises ``StateTransitionError`` (fail hard, low
    in the stack) if the match is not PROPOSED or PENDING. This function does
    not re-check the condition (ADR 0017).

    Decorated with ``@has_side_effects(MATCH_ACCEPTED)`` (VERB-107): all three
    ``matching.side_effects`` handlers bound to that label fire on every call
    and each guards on the mutated ``match.status`` — the waiting-partner nudge
    on PENDING, the PII-reveal confirmation (to both sides) on ACCEPTED.

    Args:
        match: The match to accept.
        registration: The registration (ambassador or referee) accepting.

    Returns:
        The updated ``Match`` instance.

    Raises:
        StateTransitionError: propagated from ``Match.accept`` if
            ``match.status`` is not ``PROPOSED`` or ``PENDING``.
    """
    with transaction.atomic():
        status_before = match.status
        match.accept(registration)
        match.save(
            update_fields=[
                "ambassador_accepted_at",
                "referee_accepted_at",
                "status",
                "updated_at",
            ]
        )

        if match.status != status_before:
            record_transition(
                match,
                "status",
                before=status_before,
                after=match.status,
            )
            logger.info(
                "Match pk=%s accepted by registration pk=%s: %s → %s",
                match.pk,
                registration.pk,
                status_before,
                match.status,
            )

        if match.status == Match.Status.ACCEPTED:
            logger.info(
                "Match pk=%s ACCEPTED: both parties accepted "
                "(ambassador reg pk=%s, referee reg pk=%s)",
                match.pk,
                match.ambassador_registration_id,
                match.referee_registration_id,
            )

            # Capture both parties' deposits inline (VERB-87). A free-tier
            # registration (fee_chf=0) has no HELD Payment — held().first()
            # returns None and it is skipped gracefully. capture() opens its own
            # atomic block, which nests here as a savepoint.
            for reg in (
                match.ambassador_registration,
                match.referee_registration,
            ):
                deposit = Payment.objects.for_registration(reg).held().first()
                if deposit is not None:
                    capture(deposit, reason=Payment.Reason.SUCCESSFUL_MATCH)

    return match


@has_side_effects(MATCH_NO_SHOW)
def report_no_show(match: Match, registration: Registration) -> Match:
    """Record a post-accept no-show report and apply the asymmetric re-queue.

    ``registration`` is the **reporter** (the party who showed up). The
    accused is the other party on the match.

    Atomically:
    1. Transitions ``match.status`` ACCEPTED → CANCELLED and sets
       ``no_show_reported_by`` / ``no_show_reported_at``.
    2. Writes one ``StateTransitionLog`` row for ``Match.status``.
    3. Suspends the accused (``SUSPENDED``) and logs the accused's
       ``Registration.status`` transition.
    4. Forfeits only the accused's held deposit (HELD → FORFEITED, reason
       POST_ACCEPT_NOSHOW) via ``billing.services.payments.forfeit`` — VERB-87.
       The reporter's deposit stays HELD; a free-tier accused (no Payment) is
       skipped.
    5. Re-queues the reporter to the front of the pool (``VERIFIED``,
       ``priority += 1``). The reporter's status transition is **not** logged,
       consistent with the decline path.

    Decorated with ``@has_side_effects(MATCH_NO_SHOW)`` (VERB-107): the
    ``matching.side_effects`` handlers bound to that label notify the accused
    (no-show notice) and the reporter (re-queued notice) after commit.

    Args:
        match: The match being reported. Must be in ``ACCEPTED`` status with
            no existing ``no_show_reported_by``.
        registration: The reporter's registration (the party who was let down).

    Returns:
        The updated ``Match`` instance.

    Raises:
        StateTransitionError: propagated from ``Match.cancel`` if
            ``match.status`` is not ``ACCEPTED`` or if a no-show has already
            been reported on this match (first-report-wins).
    """
    with transaction.atomic():
        side = match.side_of(registration)
        # Both FKs are non-null on ACCEPTED matches; assertions satisfy mypy.
        assert match.ambassador_registration is not None
        assert match.referee_registration is not None
        # The accused is the other party.
        if side == Match.Side.AMBASSADOR:
            accused = match.referee_registration
        else:
            accused = match.ambassador_registration

        # Transition the match to CANCELLED. Match.cancel is the single guard
        # on the source state (ACCEPTED, not already reported) and sets the
        # no_show_reported_* fields (model logic, VERB-104 / ADR 0017); this
        # function does not re-check those conditions.
        status_before = match.status
        match.cancel(registration).save(
            update_fields=[
                "no_show_reported_by",
                "no_show_reported_at",
                "status",
                "updated_at",
            ]
        )
        record_transition(
            match,
            "status",
            before=status_before,
            after=match.status,
        )

        # Suspend the accused and log the transition.
        accused_status_before = accused.status
        suspend_for_no_show(accused)
        record_transition(
            accused,
            "status",
            before=accused_status_before,
            after=accused.status,
        )

        # Forfeit only the accused's held deposit inline (HELD → FORFEITED) —
        # VERB-87. The reporter's deposit stays HELD (they kept faith). A
        # free-tier accused (no Payment) is skipped gracefully. forfeit() opens
        # its own atomic block, which nests here as a savepoint.
        accused_deposit = Payment.objects.for_registration(accused).held().first()
        if accused_deposit is not None:
            forfeit(accused_deposit, reason=Payment.Reason.POST_ACCEPT_NOSHOW)

        # Re-queue the reporter to the front (no transition log — consistent
        # with the decline path which does not log reporter re-queue either).
        requeue_to_front(registration)

        logger.info(
            "report_no_show: match pk=%s CANCELLED by %s (registration pk=%s); "
            "accused (pk=%s) SUSPENDED, reporter re-queued to front.",
            match.pk,
            side,
            registration.pk,
            accused.pk,
        )

    return match


@has_side_effects(MATCH_DECLINED)
def record_decline(match: Match, registration: Registration) -> Match:
    """Record that ``registration`` has declined ``match``.

    Sets ``declined_by`` and ``declined_at`` and transitions the match from
    ``PROPOSED or PENDING → DECLINED``. One ``StateTransitionLog`` row is
    written for the status change.

    The source-state guard and the field mutations are delegated to the
    ``Match.decline`` model method (model logic, VERB-102 / ADR 0017), which
    validates ``match.status`` itself and raises ``StateTransitionError`` on an
    illegal source state (fail hard, low in the stack). This function does not
    re-check that condition; it owns the save and audit-log row. No row lock is
    taken (VERB-106); it acts on the passed-in match instance directly.

    NOTE: pausing the decliner's registration and re-queuing the other party
    are **not** done here; they belong to ``decline_match``. This function
    deliberately leaves both ``Registration.status`` values untouched so it is
    not mistaken for a bug. The email-hash field was removed in VERB-74 (see
    ADR 0008 — superseded).

    Decorated with ``@has_side_effects(MATCH_DECLINED)`` (VERB-107): the
    ``matching.side_effects`` handler bound to that label notifies the
    kept-faith (non-declining) party after commit.

    Args:
        match: The match to decline.
        registration: The registration (ambassador or referee) declining.

    Returns:
        The updated ``Match`` instance.

    Raises:
        StateTransitionError: propagated from ``Match.decline`` if
            ``match.status`` is not ``PROPOSED`` or ``PENDING``.
    """
    with transaction.atomic():
        status_before = match.status
        match.decline(registration).save(
            update_fields=[
                "declined_by",
                "declined_at",
                "status",
                "updated_at",
            ]
        )

        record_transition(
            match,
            "status",
            before=status_before,
            after=match.status,
        )

        logger.info(
            "Match pk=%s DECLINED by %s (registration pk=%s)",
            match.pk,
            match.declined_by,
            registration.pk,
        )

    return match


def close_season(*, commit: bool) -> tuple[int, int]:
    """Refund every still-HELD deposit with no ACCEPTED match and not suspended.

    The season-end sweep (VERB-87). A deposit is refunded (HELD → REFUNDED,
    reason SEASON_END_NO_MATCH) when its registration:
      - still holds a HELD Payment (a captured / forfeited / already-refunded
        deposit is left untouched — those are terminal);
      - never reached an ``ACCEPTED`` match (a registration currently in an
        accepted match has already had its deposit captured, so this is belt-
        and-braces on top of the HELD filter);
      - is not ``SUSPENDED`` (a post-accept no-show already forfeited theirs).

    Expiry/decline-induced PAUSE deliberately leaves the deposit HELD and
    refundable (lenient model, ADR 0013) — a PAUSED registration is therefore
    swept here.

    Read-only unless ``commit`` is True (management-command rules): with
    ``commit=False`` it reports how many deposits it *would* refund without
    writing anything; with ``commit=True`` it refunds for real.

    Each refund is issued serially by a self-contained ``refund()`` call — the
    loop is deliberately **not** wrapped in one ``transaction.atomic()``. Each
    ``refund()`` opens and commits its own short transaction around a single
    Stripe round-trip, so the sweep never holds a DB connection across the whole
    batch (connection-pool exhaustion — see VERB-85 review note). A per-payment
    HELD re-check makes a retried / concurrent run idempotent, on top of the
    stable Stripe idempotency key. If a payment still races out of HELD between
    the re-check and ``refund()``'s own lock, the resulting
    ``InvalidPaymentTransition`` is caught and skipped — a benign race, not
    counted as a batch failure; only genuine errors bump ``failed``.

    Args:
        commit: When False, simulate and count only. When True, issue refunds.

    Returns:
        A ``(refunded, failed)`` tuple. In dry-run mode ``failed`` is always 0
        and ``refunded`` is the would-refund count.
    """
    candidates = (
        Payment.objects.held()
        .filter(registration__isnull=False)
        .exclude(registration__status=Registration.Status.SUSPENDED)
        .exclude(
            Q(registration__matches_as_ambassador__status=Match.Status.ACCEPTED)
            | Q(registration__matches_as_referee__status=Match.Status.ACCEPTED)
        )
        .distinct()
    )
    candidate_pks = list(candidates.values_list("pk", flat=True))

    if not commit:
        logger.info(
            "close_season (dry-run): would refund %s deposit(s).",
            len(candidate_pks),
        )
        return len(candidate_pks), 0

    refunded = 0
    failed = 0

    for pk in candidate_pks:
        try:
            payment = Payment.objects.get(pk=pk)
            # Concurrency / idempotency guard: another run may have already
            # transitioned this payment out of HELD since the PK list was built.
            if payment.status != Payment.Status.HELD:
                logger.debug(
                    "close_season: skipping payment pk=%s (status=%r, no longer HELD)",
                    pk,
                    payment.status,
                )
                continue
            refund(payment, reason=Payment.Reason.SEASON_END_NO_MATCH)
            refunded += 1
        except InvalidPaymentTransition:
            # A benign race: the payment left HELD between the re-check above and
            # refund()'s own select_for_update. Skip it — this is not a batch
            # failure, so do not increment the failure counter (a spurious
            # non-zero exit would false-alarm the cron).
            logger.warning(
                "close_season: payment pk=%s left HELD before refund; skipping", pk
            )
            continue
        except Exception:
            # Deliberate catch-all: isolate one payment's failure so a single bad
            # row cannot abort the refund sweep. logger.exception records it for triage.
            failed += 1
            logger.exception(
                "close_season: error refunding payment pk=%s; skipping", pk
            )

    logger.info(
        "close_season: refunded %s deposit(s), %s failure(s).", refunded, failed
    )
    return refunded, failed
