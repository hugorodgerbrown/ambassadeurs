# Matching-domain service functions.
#
# Side effects (User / Registration / Match creation) are orchestrated here
# and called inline from views — never via Django signals (CLAUDE.md "Models").
#
# The matching engine runs synchronously inside register_participant: after
# creating the registration (inside an atomic transaction), propose_match is
# called to attempt an immediate pairing with a waiting counterpart.

from __future__ import annotations

import logging
from datetime import timedelta

from django.conf import settings
from django.contrib.auth.models import User
from django.core.mail import send_mail
from django.db import transaction
from django.db.models import Case, IntegerField, Value, When
from django.utils import timezone, translation
from django.utils.dateparse import parse_date
from django.utils.translation import gettext as _

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


def send_match_notification(match: Match) -> None:
    """Send a "you've been matched" notification to both parties.

    Each recipient's email is rendered under their own preferred_language via
    ``translation.override``. IMPORTANT: the body must contain NO contact PII
    (name, email, phone) and NO action link — contact details are revealed only
    after mutual accept (Invariant 1). The accept endpoint does not yet exist.
    """
    for registration in (
        match.ambassador_registration,
        match.referee_registration,
    ):
        lang = registration.preferred_language or settings.LANGUAGE_CODE
        with translation.override(lang):
            subject = _("You have been matched — 4 Vallées Ambassadors Program")
            body = _(
                "Good news — the matching system has found you a partner for the "
                "4 Vallées Ambassadors Program.\n\n"
                "Log in to your account to view the match and take action within "
                "the contact window.\n\n"
                "If you did not register for this programme, please ignore this email."
            )
        recipient_email = registration.user.email
        send_mail(subject, body, settings.DEFAULT_FROM_EMAIL, [recipient_email])
        logger.info(
            "Sent match notification for match pk=%s to registration pk=%s",
            match.pk,
            registration.pk,
        )


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
) -> Registration:
    """Enrol a participant in the pool and return the Registration.

    With no ``user`` (the email-only flow) a passwordless ``User`` is created or
    reused, keyed on the lowercased email as username. With a ``user`` (e.g. one
    that just signed in with Facebook) that user is reused and their name kept
    current.

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
        )

        propose_match(registration)

    logger.info("Registered user pk=%s as %s", user.pk, role)
    return registration
