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
# acceptance moves PENDING → ACCEPTED. Both guards in record_acceptance accept
# "PROPOSED or PENDING" rather than "PROPOSED only".
#
# withdraw_acceptance (VERB-43 / ADR 0010) is the inverse of the first accept:
# while a match is PENDING and the other side has not accepted, the viewing
# party clears their own *_accepted_at timestamp — a clean, no-penalty un-accept
# (PENDING → PROPOSED) — and a transition log row is written.
#
# expire_lapsed_matches is the periodic sweep entry point: it transitions all
# PROPOSED or PENDING matches past their contact window to EXPIRED. Non-
# responders are paused (pause_registration); the faithful party re-queues to
# the front (requeue_to_front). A window-expired notification email is queued
# via transaction.on_commit for each paused non-responder.
#
# pause_registration (VERB-74 / ADR 0013) replaces requeue_to_back and
# record_flake_and_requeue. Decline or non-response → PAUSED; the two-strike
# flake model is retired. rejoin_queue is the self-service re-entry from the
# account page (PAUSED → VERIFIED, priority -= 1, propose_match).
#
# report_no_show (VERB-21) implements the post-accept no-show path (ADR 0007):
# the reporter's registration is re-queued to the front; the accused is
# suspended and emailed (no reporter PII in the email — Invariant 1).
# The match is transitioned ACCEPTED → CANCELLED (renamed from ABANDONED).
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

from __future__ import annotations

import functools
import logging
from datetime import timedelta

from django.conf import settings
from django.contrib.auth.models import User
from django.core.mail import send_mail
from django.db import transaction
from django.db.models import Case, IntegerField, Q, Value, When
from django.urls import reverse
from django.utils import timezone, translation
from django.utils.dateparse import parse_date
from django.utils.translation import gettext as _

from accounts.tokens import make_match_access_token
from billing.models import Payment
from billing.services.payments import capture, forfeit, refund
from core.emails import normalise_email
from core.services import record_transition

from .models import Match, Registration
from .pricing_config import fee_chf_for, matching_opens_at

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

    # Rank: shared location first (1), then priority desc, then created_at asc.
    # We achieve the shared-location preference by annotating with a 0/1 flag.
    ranked = candidates.annotate(
        location_match=Case(
            When(
                preferred_location=registration.preferred_location,
                then=Value(1),
            ),
            default=Value(0),
            output_field=IntegerField(),
        )
    ).order_by("-location_match", "-priority", "created_at")

    counterpart = ranked.first()
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

    # Registrations are NOT flipped to MATCHED (VERB-44). Pool availability is
    # enforced by RegistrationQuerySet._without_active_match instead.
    logger.info(
        "Proposed match pk=%s: ambassador reg pk=%s, referee reg pk=%s",
        match.pk,
        ambassador_reg.pk,
        referee_reg.pk,
    )

    transaction.on_commit(lambda: send_match_notification(match))
    return match


def requeue_to_front(registration: Registration) -> None:
    """Re-queue a kept-faith / wronged party: status=VERIFIED, priority += 1.

    Used after a counterpart declines or a match expires where this party had
    already accepted (or the window lapsed without action from the other side).
    Not a penalty — priority is only adjusted here, never on pause.

    Runs inside a transaction with a SELECT FOR UPDATE to prevent lost updates.
    Syncs the passed-in instance's in-memory fields after the DB write.
    """
    with transaction.atomic():
        locked = Registration.objects.select_for_update().get(pk=registration.pk)
        locked.status = Registration.Status.VERIFIED
        locked.priority += 1
        locked.save(update_fields=["status", "priority"])
        registration.status = locked.status
        registration.priority = locked.priority
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

    Runs inside a transaction with a SELECT FOR UPDATE to prevent lost updates.
    Syncs the passed-in instance's in-memory fields after the DB write.
    """
    with transaction.atomic():
        locked = Registration.objects.select_for_update().get(pk=registration.pk)
        locked.status = Registration.Status.PAUSED
        locked.save(update_fields=["status"])
        registration.status = locked.status
    logger.info(
        "Paused registration pk=%s (out of pool; may self-rejoin)",
        registration.pk,
    )


def rejoin_queue(registration: Registration) -> None:
    """Transition a PAUSED registration back to VERIFIED and attempt matching.

    Mirrors ``confirm_registration``: uses ``select_for_update`` inside an
    atomic block for concurrency safety. If the registration is not PAUSED the
    function is a no-op (idempotent guard). On success:
      - status → VERIFIED
      - priority -= 1 (one step toward the back each time they re-enter)
      - ``propose_match`` is called to attempt an immediate pairing

    This is the self-service re-entry point exposed via ``accounts:rejoin_queue``
    (VERB-74 / ADR 0013).

    Args:
        registration: The registration to re-activate.
    """
    with transaction.atomic():
        locked = Registration.objects.select_for_update().get(pk=registration.pk)
        if locked.status != Registration.Status.PAUSED:
            logger.info(
                "rejoin_queue called on non-PAUSED registration pk=%s "
                "(status=%s); no-op.",
                registration.pk,
                locked.status,
            )
            registration.status = locked.status
            return

        locked.status = Registration.Status.VERIFIED
        locked.priority -= 1
        locked.save(update_fields=["status", "priority"])
        registration.status = locked.status
        registration.priority = locked.priority

        propose_match(registration)

    logger.info(
        "rejoin_queue: registration pk=%s PAUSED → VERIFIED (priority=%s)",
        registration.pk,
        registration.priority,
    )


def send_window_expired_notification(registration: Registration) -> None:
    """Send a contact-window expiry notification to a non-responding party.

    Informs the participant that the match window closed because they did not
    respond, that their registration is now paused, and that they may rejoin
    the queue from their account page.

    Each email is rendered under the participant's ``preferred_language``.

    No reporter or partner PII is included (Invariant 1).

    Args:
        registration: The non-responding party's registration.
    """
    # Reload to ensure we have the latest related user.
    registration = Registration.objects.select_related("user").get(pk=registration.pk)
    lang = registration.preferred_language or settings.LANGUAGE_CODE
    account_url = settings.BASE_URL + reverse("accounts:detail")
    with translation.override(lang):
        subject = _("Your match has expired — rejoin the queue when you're ready")
        body = _(
            "The contact window for your recent match in the 4 Vallées "
            "Ambassadors Program has closed because the match was not confirmed "
            "in time.\n\n"
            "Your registration is now paused. When you are ready to be matched "
            'again, visit your account page and click "Rejoin the queue":\n\n'
            "%(url)s\n\n"
            "If you did not register for this programme, please ignore this email."
        ) % {"url": account_url}
    recipient_email = registration.user.email
    send_mail(subject, body, settings.DEFAULT_FROM_EMAIL, [recipient_email])
    logger.info(
        "Sent window-expired notification to registration pk=%s",
        registration.pk,
    )


def suspend_for_no_show(registration: Registration) -> None:
    """Suspend a registration following a post-accept no-show report.

    Sets status=SUSPENDED. The two-strike flake model is retired (VERB-74); a
    no-show report suspends unconditionally with a single step.

    Runs inside a transaction with a SELECT FOR UPDATE to prevent lost updates.
    Syncs the passed-in instance's in-memory fields after the DB write.
    """
    with transaction.atomic():
        locked = Registration.objects.select_for_update().get(pk=registration.pk)
        locked.status = Registration.Status.SUSPENDED
        locked.save(update_fields=["status"])
        registration.status = locked.status
    logger.info(
        "Suspended registration pk=%s for post-accept no-show",
        registration.pk,
    )


def send_match_notification(match: Match) -> None:
    """Send a "you've been matched" notification to both parties.

    Each recipient's email is rendered under their own preferred_language via
    ``translation.override``. The body contains a per-recipient, signed match-
    access link so they can view, accept, or decline the match. The link carries
    no contact PII (Invariant 1) — contact details are only revealed after
    mutual accept.
    """
    # Both FKs are non-null on PROPOSED matches (the only state this is called
    # from); assertions make the nullability explicit for the type checker.
    assert match.ambassador_registration is not None
    assert match.referee_registration is not None
    for registration in (
        match.ambassador_registration,
        match.referee_registration,
    ):
        lang = registration.preferred_language or settings.LANGUAGE_CODE
        # Mint a per-recipient token that scopes the link to this registration
        # only. The token carries no PII — only the match and registration PKs.
        token = make_match_access_token(match.pk, registration.pk)
        match_path = reverse("public:match", args=[token])
        match_url = settings.BASE_URL + match_path
        with translation.override(lang):
            subject = _("You have been matched — 4 Vallées Ambassadors Program")
            body = _(
                "Good news — the matching system has found you a partner for the "
                "4 Vallées Ambassadors Program.\n\n"
                "Open the link below to view your match and accept or decline "
                "within the contact window:\n\n"
                "%(url)s\n\n"
                "If you did not register for this programme, please ignore this email."
            ) % {"url": match_url}
        recipient_email = registration.user.email
        send_mail(subject, body, settings.DEFAULT_FROM_EMAIL, [recipient_email])
        logger.info(
            "Sent match notification for match pk=%s to registration pk=%s",
            match.pk,
            registration.pk,
        )


def send_match_confirmed_email(match: Match) -> None:
    """Send a confirmation email to both parties when a match is mutually accepted.

    Each recipient's email is rendered under their own preferred_language. This
    is the first and only point at which contact PII (the counterpart's name,
    email, and phone) is revealed (Invariant 1). Must only be called after the
    match has reached ``ACCEPTED`` status.

    Args:
        match: A Match whose ``status`` is ``ACCEPTED``.
    """
    # Reload to ensure we have the latest related objects.
    match = Match.objects.select_related(
        "ambassador_registration__user",
        "referee_registration__user",
    ).get(pk=match.pk)

    # Both FKs are non-null on ACCEPTED matches (the only state this is called
    # from); assertions make the nullability explicit for the type checker.
    assert match.ambassador_registration is not None
    assert match.referee_registration is not None

    registrations = (
        match.ambassador_registration,
        match.referee_registration,
    )
    counterparts = {
        match.ambassador_registration.pk: match.referee_registration,
        match.referee_registration.pk: match.ambassador_registration,
    }

    for registration in registrations:
        counterpart = counterparts[registration.pk]
        lang = registration.preferred_language or settings.LANGUAGE_CODE
        full_name = (
            f"{counterpart.user.first_name} {counterpart.user.last_name}".strip()
        )
        with translation.override(lang):
            subject = _("Match confirmed — contact your partner")
            body = _(
                "Great news — your match has been confirmed!\n\n"
                "Here are your partner's contact details:\n\n"
                "Name: %(name)s\n"
                "Email: %(email)s\n"
                "Phone: %(phone)s\n\n"
                "Please get in touch to arrange buying your passes together at "
                "the ticket office.\n\n"
                "Good luck!"
            ) % {
                "name": full_name,
                "email": counterpart.user.email,
                "phone": counterpart.phone,
            }
        recipient_email = registration.user.email
        send_mail(subject, body, settings.DEFAULT_FROM_EMAIL, [recipient_email])
        logger.info(
            "Sent match confirmed email for match pk=%s to registration pk=%s",
            match.pk,
            registration.pk,
        )


def accept_match(match: Match, registration: Registration) -> Match:
    """Record an acceptance and send a confirmed email on mutual accept.

    Calls ``record_acceptance``; if the returned match has reached
    ``ACCEPTED`` status (both parties have now accepted), queues
    ``send_match_confirmed_email`` via ``transaction.on_commit`` so the PII
    reveal email is only sent after the DB commit succeeds.

    Args:
        match: The match being accepted.
        registration: The registration (ambassador or referee) accepting.

    Returns:
        The updated ``Match`` instance.

    Raises:
        ValueError: propagated from ``record_acceptance`` if match is not
            PROPOSED or PENDING.
    """
    match = record_acceptance(match, registration)
    if match.status == Match.Status.ACCEPTED:
        transaction.on_commit(lambda: send_match_confirmed_email(match))
        logger.info(
            "Match pk=%s accepted by both parties; confirmed email queued.",
            match.pk,
        )
    return match


def decline_match(match: Match, registration: Registration) -> Match:
    """Record a decline, pause the decliner, and re-queue the other party.

    Calls ``record_decline``, then:
    - Pauses the decliner's registration (``pause_registration``). The User and
      Registration rows are retained; the participant can rejoin from their
      account page (VERB-74 / ADR 0013).
    - Re-queues the other party to the front of the pool (``requeue_to_front``).

    All three steps run inside a single outer ``transaction.atomic()`` block so
    that a crash between steps cannot leave a partial state (e.g. match DECLINED
    but decliner still VERIFIED). The inner atomics in ``record_decline``,
    ``pause_registration``, and ``requeue_to_front`` nest via savepoints —
    mirroring the pattern used in ``expire_lapsed_matches``.

    Args:
        match: The match being declined.
        registration: The registration (ambassador or referee) declining.

    Returns:
        The updated ``Match`` instance.

    Raises:
        ValueError: propagated from ``record_decline`` if match is not PROPOSED
            or PENDING.
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

    logger.info("Registered user pk=%s as %s (status=%s)", user.pk, role, status)
    return registration


def confirm_registration(registration: Registration) -> Registration:
    """Transition an UNVERIFIED registration to VERIFIED and trigger matching.

    Runs inside ``transaction.atomic()`` with a ``select_for_update()`` to
    prevent duplicate confirms under concurrency. If the registration is not
    UNVERIFIED (already confirmed, or an invalid state), the function is a
    no-op and returns the unchanged row — the caller is responsible for treating
    a non-UNVERIFIED result as an invalid/used token.

    After the status flip, ``propose_match`` is called to attempt an immediate
    pairing. The in-memory instance is synced and returned.
    """
    with transaction.atomic():
        locked = Registration.objects.select_for_update().get(pk=registration.pk)
        if locked.status != Registration.Status.UNVERIFIED:
            # Already confirmed or in an unexpected state; no-op.
            logger.info(
                "confirm_registration called on non-UNVERIFIED registration pk=%s "
                "(status=%s); no-op.",
                registration.pk,
                locked.status,
            )
            registration.status = locked.status
            return registration

        locked.status = Registration.Status.VERIFIED
        locked.save(update_fields=["status", "updated_at"])
        registration.status = locked.status

        propose_match(registration)

    logger.info("Confirmed registration pk=%s: UNVERIFIED → VERIFIED", registration.pk)
    return registration


def expire_lapsed_matches() -> int:
    """Expire all PROPOSED or PENDING matches past their contact window.

    Selects candidate PKs up front, then processes each match in its own
    ``transaction.atomic()`` block so that one bad match does not abort the
    whole sweep.

    Per-side outcome logic (VERB-74 / ADR 0013):
    - If a side had already accepted (``*_accepted_at`` is not None), they kept
      faith → ``requeue_to_front``.
    - If a side had not responded by expiry, they are the non-responder →
      ``pause_registration`` (removed from pool; may self-rejoin). A window-
      expired notification email is queued via ``transaction.on_commit`` for
      each paused non-responder.

    Returns:
        The number of matches that were transitioned to EXPIRED in this run.
    """
    candidate_pks = list(Match.objects.lapsed().values_list("pk", flat=True))
    expired_count = 0

    for pk in candidate_pks:
        try:
            with transaction.atomic():
                match = (
                    Match.objects.select_for_update()
                    .select_related("ambassador_registration", "referee_registration")
                    .get(pk=pk)
                )

                # Concurrency / idempotency guard: another worker or an
                # accept/decline may have already changed the status.
                if match.status not in (Match.Status.PROPOSED, Match.Status.PENDING):
                    logger.debug(
                        "Skipping match pk=%s: status=%r (expected PROPOSED/PENDING)",
                        pk,
                        match.status,
                    )
                    continue

                status_before = match.status
                match.status = Match.Status.EXPIRED
                match.save(update_fields=["status", "updated_at"])
                record_transition(
                    match,
                    "status",
                    before=status_before,
                    after=match.status,
                )

                # Both FKs are non-null; assertions satisfy mypy.
                assert match.ambassador_registration is not None
                assert match.referee_registration is not None
                ambassador_reg = match.ambassador_registration
                referee_reg = match.referee_registration

                if match.ambassador_accepted_at is not None:
                    requeue_to_front(ambassador_reg)
                else:
                    pause_registration(ambassador_reg)
                    transaction.on_commit(
                        functools.partial(
                            send_window_expired_notification, ambassador_reg
                        )
                    )

                if match.referee_accepted_at is not None:
                    requeue_to_front(referee_reg)
                else:
                    pause_registration(referee_reg)
                    transaction.on_commit(
                        functools.partial(send_window_expired_notification, referee_reg)
                    )

                expired_count += 1
                logger.info(
                    "Expired match pk=%s (ambassador reg pk=%s accepted=%s, "
                    "referee reg pk=%s accepted=%s)",
                    match.pk,
                    ambassador_reg.pk,
                    match.ambassador_accepted_at is not None,
                    referee_reg.pk,
                    match.referee_accepted_at is not None,
                )
        except Exception:
            logger.exception("Error expiring match pk=%s; skipping", pk)

    return expired_count


def _rank_referees_for(
    ambassador: Registration, referees: list[Registration]
) -> list[Registration]:
    """Rank ``referees`` for ``ambassador`` using the engine's ordering.

    Mirrors the ranking applied inside ``propose_match``: shared
    ``preferred_location`` first, then ``priority`` descending, then
    ``created_at`` ascending (FIFO within an equal priority). Used only by the
    read-only ``run_matching`` dry-run simulation, which cannot lean on the
    database ``ORDER BY`` because it pairs greedily in Python without writing.

    Args:
        ambassador: The ambassador whose location drives the shared-location
            preference.
        referees: The candidate referees to rank (already filtered to the
            eligible, unconsumed pool).

    Returns:
        A new list of the referees ordered best-first.
    """
    return sorted(
        referees,
        key=lambda referee: (
            # Negate so that a shared location (1) sorts before a non-shared
            # one (0), and higher priority sorts before lower priority.
            -(1 if referee.preferred_location == ambassador.preferred_location else 0),
            -referee.priority,
            referee.created_at,
        ),
    )


def _simulate_run_matching() -> int:
    """Return how many matches ``run_matching`` would propose, writing nothing.

    Greedily pairs the eligible ambassador pool (ordered ``-priority,
    created_at`` — the same order the commit path proposes in) against the
    eligible referee pool, consuming each referee as it is taken so no
    registration is paired twice. Reuses ``_rank_referees_for`` for the
    per-ambassador counterpart ranking, so the dry-run count matches what a
    real run would create.

    Returns:
        The number of matches that would be proposed.
    """
    ambassadors = list(
        Registration.objects.eligible_ambassadors().order_by("-priority", "created_at")
    )
    referees = list(Registration.objects.eligible_referees())
    consumed: set[int] = set()
    would_propose = 0

    for ambassador in ambassadors:
        available = [ref for ref in referees if ref.pk not in consumed]
        if not available:
            break
        counterpart = _rank_referees_for(ambassador, available)[0]
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
    is re-queued and no flake penalty is applied — withdrawing differs from a
    decline (which removes the registration) and from a non-response flake.

    The guard that the other side has not accepted is what keeps the operation
    safe: if both sides had accepted the match would already be ``ACCEPTED`` (a
    terminal, contact-revealed state), so there is no window in which a
    withdrawal could un-reveal PII.

    Args:
        match: The match to withdraw acceptance from.
        registration: The registration (ambassador or referee) withdrawing.

    Returns:
        The updated ``Match`` instance.

    Raises:
        ValueError: if ``match.status`` is not ``PENDING``, or if this side
            has not accepted (nothing to withdraw).
    """
    with transaction.atomic():
        match = (
            Match.objects.select_for_update()
            .select_related("ambassador_registration", "referee_registration")
            .get(pk=match.pk)
        )

        if match.status != Match.Status.PENDING:
            raise ValueError(
                f"Cannot withdraw acceptance on match pk={match.pk}: status is "
                f"{match.status!r}, expected {Match.Status.PENDING!r}."
            )

        side = match.side_of(registration)

        if side == Match.Side.AMBASSADOR:
            if match.ambassador_accepted_at is None:
                raise ValueError(
                    f"Cannot withdraw acceptance on match pk={match.pk}: the "
                    f"ambassador side has not accepted."
                )
            match.ambassador_accepted_at = None
            field = "ambassador_accepted_at"
        else:
            if match.referee_accepted_at is None:
                raise ValueError(
                    f"Cannot withdraw acceptance on match pk={match.pk}: the "
                    f"referee side has not accepted."
                )
            match.referee_accepted_at = None
            field = "referee_accepted_at"

        # Transition PENDING → PROPOSED (a real status change).
        status_before = match.status
        match.status = Match.Status.PROPOSED
        match.save(update_fields=[field, "status", "updated_at"])
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
            side,
            registration.pk,
        )

    return match


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

    Args:
        match: The match to accept.
        registration: The registration (ambassador or referee) accepting.

    Returns:
        The updated ``Match`` instance.

    Raises:
        ValueError: if ``match.status`` is not ``PROPOSED`` or ``PENDING``.
    """
    with transaction.atomic():
        match = (
            Match.objects.select_for_update()
            .select_related("ambassador_registration", "referee_registration")
            .get(pk=match.pk)
        )

        if match.status not in (Match.Status.PROPOSED, Match.Status.PENDING):
            raise ValueError(
                f"Cannot accept match pk={match.pk}: status is {match.status!r}, "
                f"expected PROPOSED or PENDING."
            )

        side = match.side_of(registration)
        now = timezone.now()

        update_fields: list[str] = []

        if side == Match.Side.AMBASSADOR and match.ambassador_accepted_at is None:
            match.ambassador_accepted_at = now
            update_fields.append("ambassador_accepted_at")
        elif side == Match.Side.REFEREE and match.referee_accepted_at is None:
            match.referee_accepted_at = now
            update_fields.append("referee_accepted_at")
        # If the side has already accepted (re-accept), no timestamp is changed.

        if update_fields:
            match.save(update_fields=update_fields + ["updated_at"])

        # After setting the timestamp, determine the next state.
        both_accepted = (
            match.ambassador_accepted_at is not None
            and match.referee_accepted_at is not None
        )

        if both_accepted:
            # Second accept: PENDING → ACCEPTED.
            status_before = match.status
            match.status = Match.Status.ACCEPTED
            match.save(update_fields=["status", "updated_at"])
            record_transition(
                match,
                "status",
                before=status_before,
                after=match.status,
            )
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
        elif update_fields:
            # First accept: PROPOSED → PENDING (only when a timestamp was actually set).
            if match.status == Match.Status.PROPOSED:
                status_before = match.status
                match.status = Match.Status.PENDING
                match.save(update_fields=["status", "updated_at"])
                record_transition(
                    match,
                    "status",
                    before=status_before,
                    after=match.status,
                )
                logger.info(
                    "Match pk=%s first accept by %s: PROPOSED → PENDING",
                    match.pk,
                    side,
                )

    return match


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
    6. Queues ``send_no_show_notification`` to fire after the transaction
       commits.

    Args:
        match: The match being reported. Must be in ``ACCEPTED`` status with
            no existing ``no_show_reported_by``.
        registration: The reporter's registration (the party who was let down).

    Returns:
        The updated ``Match`` instance.

    Raises:
        ValueError: if ``match.status != ACCEPTED`` or if
            ``no_show_reported_by`` is already set (first-report-wins).
    """
    with transaction.atomic():
        match = (
            Match.objects.select_for_update()
            .select_related(
                "ambassador_registration__user",
                "referee_registration__user",
            )
            .get(pk=match.pk)
        )

        if match.status != Match.Status.ACCEPTED:
            raise ValueError(
                f"Cannot report no-show on match pk={match.pk}: status is "
                f"{match.status!r}, expected {Match.Status.ACCEPTED!r}."
            )
        if match.no_show_reported_by:
            raise ValueError(
                f"Cannot report no-show on match pk={match.pk}: already "
                f"reported by {match.no_show_reported_by!r}."
            )

        side = match.side_of(registration)
        # Both FKs are non-null on ACCEPTED matches; assertions satisfy mypy.
        assert match.ambassador_registration is not None
        assert match.referee_registration is not None
        # The accused is the other party.
        if side == Match.Side.AMBASSADOR:
            accused = match.referee_registration
        else:
            accused = match.ambassador_registration

        # Transition the match to CANCELLED.
        status_before = match.status
        match.no_show_reported_by = side
        match.no_show_reported_at = timezone.now()
        match.status = Match.Status.CANCELLED
        match.save(
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

        transaction.on_commit(
            functools.partial(send_no_show_notification, match, accused)
        )

    return match


def send_no_show_notification(match: Match, accused_registration: Registration) -> None:
    """Send a no-show suspension notification to the accused party.

    Informs the accused that a partner reported them as a no-show and that
    their registration has been removed from the pool. No reporter PII is
    included (Invariant 1).

    Each email is rendered under the accused's ``preferred_language``.

    Args:
        match: The match that was cancelled.
        accused_registration: The registration of the party being notified.
    """
    # Reload to ensure we have the latest related user.
    accused_registration = Registration.objects.select_related("user").get(
        pk=accused_registration.pk
    )
    lang = accused_registration.preferred_language or settings.LANGUAGE_CODE
    with translation.override(lang):
        subject = _("Your match has been reported as a no-show")
        body = _(
            "We have received a no-show report from your match partner for the "
            "4 Vallées Ambassadors Program.\n\n"
            "Your registration has been removed from the pool. If you believe "
            "this report was made in error, please contact us for help.\n\n"
            "If you did not register for this programme, please ignore this email."
        )
    recipient_email = accused_registration.user.email
    send_mail(subject, body, settings.DEFAULT_FROM_EMAIL, [recipient_email])
    logger.info(
        "Sent no-show notification for match pk=%s to registration pk=%s",
        match.pk,
        accused_registration.pk,
    )


def record_decline(match: Match, registration: Registration) -> Match:
    """Record that ``registration`` has declined ``match``.

    Sets ``declined_by`` and ``declined_at`` and transitions the match from
    ``PROPOSED or PENDING → DECLINED``. One ``StateTransitionLog`` row is
    written for the status change.

    NOTE: pausing the decliner's registration and re-queuing the other party
    are **not** done here; they belong to ``decline_match``. This function
    deliberately leaves both ``Registration.status`` values untouched so it is
    not mistaken for a bug. The email-hash field was removed in VERB-74 (see
    ADR 0008 — superseded).

    Args:
        match: The match to decline.
        registration: The registration (ambassador or referee) declining.

    Returns:
        The updated ``Match`` instance.

    Raises:
        ValueError: if ``match.status`` is not ``PROPOSED`` or ``PENDING``.
    """
    with transaction.atomic():
        match = Match.objects.select_for_update().get(pk=match.pk)

        if match.status not in (Match.Status.PROPOSED, Match.Status.PENDING):
            raise ValueError(
                f"Cannot decline match pk={match.pk}: status is {match.status!r}, "
                f"expected PROPOSED or PENDING."
            )

        side = match.side_of(registration)
        status_before = match.status

        match.declined_by = side
        match.declined_at = timezone.now()
        match.status = Match.Status.DECLINED
        match.save(
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
            side,
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
    stable Stripe idempotency key.

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
        except Exception:
            failed += 1
            logger.exception(
                "close_season: error refunding payment pk=%s; skipping", pk
            )

    logger.info(
        "close_season: refunded %s deposit(s), %s failure(s).", refunded, failed
    )
    return refunded, failed
