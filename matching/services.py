# Matching-domain service functions.
#
# Side effects (User / Registration / Match creation) are orchestrated here
# and called inline from views — never via Django signals (CLAUDE.md "Models").
#
# The matching engine runs synchronously inside register_participant: after
# creating the registration (inside an atomic transaction), propose_match is
# called to attempt an immediate pairing with a waiting counterpart.
#
# record_acceptance and record_decline implement the per-party response step of
# the post-match confirmation workflow (ADR 0007 / VERB-18). Both are atomic
# and call core.services.record_transition inline for the audit log.

from __future__ import annotations

import logging
from datetime import timedelta

from django.conf import settings
from django.contrib.auth.models import User
from django.core.mail import send_mail
from django.db import transaction
from django.db.models import Case, IntegerField, Value, When
from django.urls import reverse
from django.utils import timezone, translation
from django.utils.dateparse import parse_date
from django.utils.translation import gettext as _

from accounts.tokens import make_match_access_token
from core.services import record_transition

from .models import Match, Registration

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

    Eligibility rules:
    - Opposite roles.
    - Both WAITING.
    - Ambassador holds SEASONAL, ANNUAL, or MONT4 prior pass.
    - Referee holds NONE prior pass (genuinely new).
    """
    if ambassador.role != Registration.Role.AMBASSADOR:
        return False
    if referee.role != Registration.Role.REFEREE:
        return False
    if ambassador.status != Registration.Status.WAITING:
        return False
    if referee.status != Registration.Status.WAITING:
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


def propose_match(registration: Registration) -> Match | None:
    """Attempt to pair ``registration`` with a waiting eligible counterpart.

    Must be called inside an existing ``transaction.atomic()`` block — uses
    ``select_for_update()`` to prevent duplicate matches under concurrency.

    Ranking: shared ``preferred_location`` first, then ``priority`` descending
    (higher priority = closer to the front), then ``created_at`` ascending
    (FIFO within the same priority).

    Returns the created Match, or None if no eligible counterpart is waiting.
    No-ops (returns None) if ``registration`` is not itself eligible.
    """
    if registration.role == Registration.Role.AMBASSADOR:
        # Guard: the calling ambassador must hold a valid prior pass.
        if registration.prior_pass not in (
            Registration.PriorPass.SEASONAL,
            Registration.PriorPass.ANNUAL,
            Registration.PriorPass.MONT4,
        ):
            return None
        if registration.status != Registration.Status.WAITING:
            return None
        # Look for an eligible waiting referee.
        candidates = (
            Registration.objects.eligible_referees()
            .exclude(pk=registration.pk)
            .select_for_update()
        )
    else:
        # Guard: the calling referee must have no prior pass.
        if registration.prior_pass != Registration.PriorPass.NONE:
            return None
        if registration.status != Registration.Status.WAITING:
            return None
        # Look for an eligible waiting ambassador.
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

    # Flip both registrations to MATCHED.
    Registration.objects.filter(pk__in=[ambassador_reg.pk, referee_reg.pk]).update(
        status=Registration.Status.MATCHED
    )
    ambassador_reg.status = Registration.Status.MATCHED
    referee_reg.status = Registration.Status.MATCHED

    logger.info(
        "Proposed match pk=%s: ambassador reg pk=%s, referee reg pk=%s",
        match.pk,
        ambassador_reg.pk,
        referee_reg.pk,
    )

    transaction.on_commit(lambda: send_match_notification(match))
    return match


def requeue_to_front(registration: Registration) -> None:
    """Re-queue a kept-faith / wronged party: status=WAITING, priority += 1.

    Used after a counterpart declines or a match expires where this party had
    already accepted (or the window lapsed without action from the other side).
    Not a flake — does not touch flake_count.

    Runs inside a transaction with a SELECT FOR UPDATE to prevent lost updates.
    Syncs the passed-in instance's in-memory fields after the DB write.
    """
    with transaction.atomic():
        locked = Registration.objects.select_for_update().get(pk=registration.pk)
        locked.status = Registration.Status.WAITING
        locked.priority += 1
        locked.save(update_fields=["status", "priority"])
        registration.status = locked.status
        registration.priority = locked.priority
    logger.info(
        "Re-queued registration pk=%s to front (priority=%s)",
        registration.pk,
        registration.priority,
    )


def requeue_to_back(registration: Registration) -> None:
    """Re-queue a decliner: status=WAITING, priority -= 1.

    Used after this party explicitly declines a match. Not a flake — does not
    touch flake_count.

    Runs inside a transaction with a SELECT FOR UPDATE to prevent lost updates.
    Syncs the passed-in instance's in-memory fields after the DB write.
    """
    with transaction.atomic():
        locked = Registration.objects.select_for_update().get(pk=registration.pk)
        locked.status = Registration.Status.WAITING
        locked.priority -= 1
        locked.save(update_fields=["status", "priority"])
        registration.status = locked.status
        registration.priority = locked.priority
    logger.info(
        "Re-queued registration pk=%s to back (priority=%s)",
        registration.pk,
        registration.priority,
    )


def record_flake_and_requeue(registration: Registration) -> None:
    """Record a non-response flake and re-queue or suspend the registration.

    Increments flake_count from the current DB value (lost-update guard via
    SELECT FOR UPDATE). When the new count reaches 2 the registration is
    SUSPENDED and priority is left untouched; below 2 it is re-queued to the
    back (WAITING, priority -= 1).

    Syncs the passed-in instance's in-memory fields (status, priority,
    flake_count) after the DB write.
    """
    with transaction.atomic():
        locked = Registration.objects.select_for_update().get(pk=registration.pk)
        locked.flake_count += 1
        if locked.flake_count >= 2:
            locked.status = Registration.Status.SUSPENDED
            locked.save(update_fields=["status", "flake_count"])
        else:
            locked.status = Registration.Status.WAITING
            locked.priority -= 1
            locked.save(update_fields=["status", "priority", "flake_count"])
        registration.status = locked.status
        registration.priority = locked.priority
        registration.flake_count = locked.flake_count
    logger.info(
        "Recorded flake for registration pk=%s: flake_count=%s, status=%s",
        registration.pk,
        registration.flake_count,
        registration.status,
    )


def suspend_for_no_show(registration: Registration) -> None:
    """Suspend a registration following a post-accept no-show report.

    Sets status=SUSPENDED and increments flake_count unconditionally (even if
    flake_count was already ≥ 2, the suspension still records the incident).

    Runs inside a transaction with a SELECT FOR UPDATE to prevent lost updates.
    Syncs the passed-in instance's in-memory fields after the DB write.
    """
    with transaction.atomic():
        locked = Registration.objects.select_for_update().get(pk=registration.pk)
        locked.status = Registration.Status.SUSPENDED
        locked.flake_count += 1
        locked.save(update_fields=["status", "flake_count"])
        registration.status = locked.status
        registration.flake_count = locked.flake_count
    logger.info(
        "Suspended registration pk=%s for no-show (flake_count=%s)",
        registration.pk,
        registration.flake_count,
    )


def send_match_notification(match: Match) -> None:
    """Send a "you've been matched" notification to both parties.

    Each recipient's email is rendered under their own preferred_language via
    ``translation.override``. The body contains a per-recipient, signed match-
    access link so they can view, accept, or decline the match. The link carries
    no contact PII (Invariant 1) — contact details are only revealed after
    mutual accept.
    """
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
        ValueError: propagated from ``record_acceptance`` if match is not PROPOSED.
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
    """Record a decline by ``registration`` and re-queue both parties asymmetrically.

    Calls ``record_decline`` then applies the VERB-17 re-queue services:
    the decliner goes to the back of the queue (priority -= 1) and the other
    party (who had not yet declined) goes to the front (priority += 1).

    Args:
        match: The match being declined.
        registration: The registration (ambassador or referee) declining.

    Returns:
        The updated ``Match`` instance.

    Raises:
        ValueError: propagated from ``record_decline`` if match is not PROPOSED.
    """
    side = match.side_of(registration)
    match = record_decline(match, registration)

    # Determine the other party from the match before re-queuing.
    if side == Match.Side.AMBASSADOR:
        other = match.referee_registration
    else:
        other = match.ambassador_registration

    requeue_to_back(registration)
    requeue_to_front(other)

    logger.info(
        "decline_match: match pk=%s DECLINED by registration pk=%s; "
        "decliner queued to back, other party (pk=%s) queued to front.",
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
    phone: str = "",
    accepted_terms: list[str] | None = None,
) -> Registration:
    """Enrol a participant in the pool and return the Registration.

    With no ``user`` (the email-only flow) a passwordless ``User`` is created or
    reused, keyed on the lowercased email as username. With a ``user`` (e.g. one
    that just signed in with Facebook) that user is reused and their name kept
    current.

    ``accepted_terms`` is the ordered list of consent statement texts accepted by
    the participant (eligibility declaration first, then T&C); it is persisted on
    ``Registration.accepted_terms`` alongside ``terms_accepted_at``.

    After creating the registration, calls ``propose_match`` to attempt an
    immediate pairing. The whole function runs inside a single transaction;
    ``propose_match`` uses ``select_for_update`` for concurrency safety.
    """
    with transaction.atomic():
        if user is None:
            email = email.lower()
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
            accepted_terms=accepted_terms or [],
            terms_accepted_at=timezone.now() if accepted_terms else None,
        )

        propose_match(registration)

    logger.info("Registered user pk=%s as %s", user.pk, role)
    return registration


def record_acceptance(match: Match, registration: Registration) -> Match:
    """Record that ``registration`` has accepted ``match``.

    On the first accept, only the accepting side's ``*_accepted_at`` timestamp
    is set and the status stays ``PROPOSED``; **no** ``StateTransitionLog`` row
    is written at this point.

    On the second accept (both sides now have a timestamp), the match
    transitions ``PROPOSED → ACCEPTED``, both registrations transition
    ``MATCHED → CONFIRMED``, and **three** ``StateTransitionLog`` rows are
    written — one for ``Match.status`` and one for each ``Registration.status``
    — all inside the same atomic transaction.

    Re-accepting an already-accepted side is a no-op for that timestamp (the
    existing value is kept) so callers can safely retry without double-counting.

    Args:
        match: The match to accept.
        registration: The registration (ambassador or referee) accepting.

    Returns:
        The updated ``Match`` instance.

    Raises:
        ValueError: if ``match.status`` is not ``PROPOSED``.
    """
    with transaction.atomic():
        match = (
            Match.objects.select_for_update()
            .select_related("ambassador_registration", "referee_registration")
            .get(pk=match.pk)
        )

        if match.status != Match.Status.PROPOSED:
            raise ValueError(
                f"Cannot accept match pk={match.pk}: status is {match.status!r}, "
                f"expected {Match.Status.PROPOSED!r}."
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

        # Check if both sides have now accepted; the outer PROPOSED guard above
        # guarantees status is still PROPOSED here.
        if (
            match.ambassador_accepted_at is not None
            and match.referee_accepted_at is not None
        ):
            # Transition match status.
            status_before = match.status
            match.status = Match.Status.ACCEPTED
            match.save(update_fields=["status", "updated_at"])
            record_transition(
                match,
                "status",
                before=status_before,
                after=match.status,
            )

            # Transition both registrations to CONFIRMED.
            ambassador_reg = match.ambassador_registration
            referee_reg = match.referee_registration

            amb_status_before = ambassador_reg.status
            ref_status_before = referee_reg.status

            Registration.objects.filter(
                pk__in=[ambassador_reg.pk, referee_reg.pk]
            ).update(status=Registration.Status.CONFIRMED)

            ambassador_reg.status = Registration.Status.CONFIRMED
            referee_reg.status = Registration.Status.CONFIRMED

            record_transition(
                ambassador_reg,
                "status",
                before=amb_status_before,
                after=ambassador_reg.status,
            )
            record_transition(
                referee_reg,
                "status",
                before=ref_status_before,
                after=referee_reg.status,
            )

            logger.info(
                "Match pk=%s ACCEPTED: both parties accepted "
                "(ambassador reg pk=%s, referee reg pk=%s)",
                match.pk,
                ambassador_reg.pk,
                referee_reg.pk,
            )

    return match


def record_decline(match: Match, registration: Registration) -> Match:
    """Record that ``registration`` has declined ``match``.

    Sets ``declined_by``, ``declined_at``, and transitions the match from
    ``PROPOSED → DECLINED``. One ``StateTransitionLog`` row is written for the
    status change.

    NOTE: re-queuing registrations and adjusting ``priority`` are **not** done
    here; that belongs to VERB-17. This function deliberately leaves both
    ``Registration.status`` values untouched so it is not mistaken for a bug.

    Args:
        match: The match to decline.
        registration: The registration (ambassador or referee) declining.

    Returns:
        The updated ``Match`` instance.

    Raises:
        ValueError: if ``match.status`` is not ``PROPOSED``.
    """
    with transaction.atomic():
        match = Match.objects.select_for_update().get(pk=match.pk)

        if match.status != Match.Status.PROPOSED:
            raise ValueError(
                f"Cannot decline match pk={match.pk}: status is {match.status!r}, "
                f"expected {Match.Status.PROPOSED!r}."
            )

        side = match.side_of(registration)
        status_before = match.status

        match.declined_by = side
        match.declined_at = timezone.now()
        match.status = Match.Status.DECLINED
        match.save(update_fields=["declined_by", "declined_at", "status", "updated_at"])

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
