# Notification dispatch for the match state machine, via django-side-effects
# (ADR 0018, VERB-107).
#
# This module is the single declarative registry of "which notification fires
# on which transition": the label constants below are imported by
# matching/services.py to decorate the five real transition functions with
# @has_side_effects(LABEL); the @is_side_effect_of(LABEL) handlers here are
# bound to those same labels at import time (registered via
# MatchingConfig.ready(), since the library does not autodiscover).
#
# Rule: one handler notifies one recipient (CLAUDE.md conventions call for
# small, single-purpose functions). A transition that emails two people has
# two handlers bound to its label; the small `_email_*` helpers below are the
# DRY render bodies each handler calls.
#
# Rule: handlers derive their recipient by walking the Match root object
# (ambassador_registration / referee_registration, declined_by,
# no_show_reported_by, *_accepted_at) — never a loose registration argument
# that cannot be reached from the root. The one exception is `propose_match`,
# whose product *is* its return value (it creates the match), so its two
# handlers read the match from `return_value` — see the label table below.
#
# Every handler takes **kwargs: the registry passes the origin function's
# *args/**kwargs plus `return_value`, and a handler whose signature cannot
# bind is a hard SignatureMismatch (django_side_effects.registry._run_func),
# not a silent skip — so **kwargs is mandatory on every handler, mirroring
# the origin's positional parameters.
#
# This module imports models, accounts.tokens, and mail helpers only — never
# matching.services — to avoid an import cycle (services imports the label
# constants from here).

from __future__ import annotations

import logging

from django.conf import settings
from django.core.mail import send_mail
from django.urls import reverse
from django.utils import translation
from django.utils.translation import gettext as _
from side_effects.decorators import is_side_effect_of

from accounts.tokens import make_match_access_token

from .models import Match, Registration

logger = logging.getLogger(__name__)

# --- Labels -----------------------------------------------------------------
# One label per transition function in matching/services.py. Referenced by
# @has_side_effects(LABEL) on the origin and @is_side_effect_of(LABEL) on each
# handler below.

MATCH_PROPOSED = "match_proposed"
MATCH_ACCEPTED = "match_accepted"
MATCH_DECLINED = "match_declined"
MATCH_EXPIRED = "match_expired"
MATCH_NO_SHOW = "match_no_show"


# --- Render helpers -----------------------------------------------------------
# Small, DRY, one-recipient email bodies carved from the pre-VERB-107 send_*
# functions. Each is rendered under the recipient's own preferred_language.


def _email_proposal(registration: Registration, match: Match) -> None:
    """Send the "you've been matched" notification to one recipient.

    Carries a per-recipient, signed match-access link so they can view,
    accept, or decline the match. The link carries no contact PII (Invariant
    1) — contact details are only revealed after mutual accept.
    """
    lang = registration.preferred_language or settings.LANGUAGE_CODE
    # Mint a per-recipient token that scopes the link to this registration
    # only. The token carries no PII — only the match and registration PKs.
    token = make_match_access_token(match.pk, registration.pk)
    match_url = settings.BASE_URL + reverse("public:match", args=[token])
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
    send_mail(subject, body, settings.DEFAULT_FROM_EMAIL, [registration.user.email])
    logger.info(
        "Sent match notification for match pk=%s to registration pk=%s",
        match.pk,
        registration.pk,
    )


def _email_partner_accepted(waiting_registration: Registration, match: Match) -> None:
    """Notify the waiting party that their counterpart has accepted the match.

    Sent on the first accept (PROPOSED → PENDING) to the party who has not yet
    responded, nudging them to accept or decline before the contact window
    closes. Carries a per-recipient signed match-access link but no contact
    PII (Invariant 1) — details are only revealed on mutual accept.
    """
    lang = waiting_registration.preferred_language or settings.LANGUAGE_CODE
    token = make_match_access_token(match.pk, waiting_registration.pk)
    match_url = settings.BASE_URL + reverse("public:match", args=[token])
    with translation.override(lang):
        subject = _("Your partner has accepted — it's your turn")
        body = _(
            "Good news — your match partner for the 4 Vallées Ambassadors "
            "Program has accepted.\n\n"
            "Open the link below to accept or decline before the contact window "
            "closes:\n\n"
            "%(url)s\n\n"
            "If you did not register for this programme, please ignore this email."
        ) % {"url": match_url}
    send_mail(
        subject, body, settings.DEFAULT_FROM_EMAIL, [waiting_registration.user.email]
    )
    logger.info(
        "Sent partner-accepted notification for match pk=%s to registration pk=%s",
        match.pk,
        waiting_registration.pk,
    )


def _email_confirmation(registration: Registration, counterpart: Registration) -> None:
    """Send the mutual-accept confirmation email to one recipient.

    This is the first and only point at which contact PII (the counterpart's
    name, email, and phone) is revealed (Invariant 1).
    """
    lang = registration.preferred_language or settings.LANGUAGE_CODE
    full_name = f"{counterpart.user.first_name} {counterpart.user.last_name}".strip()
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
    send_mail(subject, body, settings.DEFAULT_FROM_EMAIL, [registration.user.email])
    logger.info(
        "Sent match confirmed email to registration pk=%s",
        registration.pk,
    )


def _email_requeued(registration: Registration) -> None:
    """Notify a kept-faith party that their match ended and they are re-queued.

    Sent to the faithful party whenever a match does not proceed through no
    fault of theirs — the counterpart declined, the contact window lapsed
    after they had accepted, or they reported a post-accept no-show. The copy
    is deliberately neutral: it reveals neither the counterpart's contact PII
    (Invariant 1) nor the reason the match ended, and asks nothing of the
    recipient (re-queuing is automatic).
    """
    lang = registration.preferred_language or settings.LANGUAGE_CODE
    with translation.override(lang):
        subject = _(
            "Your match didn't go ahead — you're back at the front of the queue"
        )
        body = _(
            "Your recent match in the 4 Vallées Ambassadors Program did not go "
            "ahead.\n\n"
            "This is not a reflection on you — you have been returned to the "
            "front of the queue, and the matching system will pair you with a "
            "new partner as soon as one is available. There is nothing you need "
            "to do.\n\n"
            "If you did not register for this programme, please ignore this email."
        )
    send_mail(subject, body, settings.DEFAULT_FROM_EMAIL, [registration.user.email])
    logger.info("Sent requeued notification to registration pk=%s", registration.pk)


def _email_window_expired(registration: Registration) -> None:
    """Send a contact-window expiry notification to a non-responding party.

    Informs the participant that the match window closed because they did not
    respond, that their registration is now paused, and that they may rejoin
    the queue from their account page — or, if they would rather stop
    waiting, cancel from the same page and get any deposit refunded (VERB-88).
    No reporter or partner PII is included (Invariant 1).
    """
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
            "If you'd rather not wait, you can cancel from the same page and "
            "get any deposit you paid refunded.\n\n"
            "If you did not register for this programme, please ignore this email."
        ) % {"url": account_url}
    send_mail(subject, body, settings.DEFAULT_FROM_EMAIL, [registration.user.email])
    logger.info(
        "Sent window-expired notification to registration pk=%s",
        registration.pk,
    )


def _email_no_show(accused_registration: Registration) -> None:
    """Send a no-show suspension notification to the accused party.

    Informs the accused that a partner reported them as a no-show and that
    their registration has been removed from the pool. No reporter PII is
    included (Invariant 1).
    """
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
    send_mail(
        subject, body, settings.DEFAULT_FROM_EMAIL, [accused_registration.user.email]
    )
    logger.info(
        "Sent no-show notification to registration pk=%s",
        accused_registration.pk,
    )


# --- match_proposed -----------------------------------------------------------
# Origin: propose_match(registration) -> Match | None, decorated with
# run_on_exit=lambda m: m is not None so handlers never fire on a no-match.
# The created match is only reachable via return_value (the one sanctioned
# use of it — see the module docstring and ADR 0018 amendment).


@is_side_effect_of(MATCH_PROPOSED)
def notify_ambassador_of_proposal(registration: Registration, **kwargs: object) -> None:
    """Email the ambassador side that a match has been proposed.

    Args:
        registration: The registration passed to ``propose_match`` (may be
            either side; the match itself is read from ``return_value``).
        kwargs: Must include ``return_value`` (the created ``Match``).
    """
    match = kwargs["return_value"]
    assert isinstance(match, Match)
    assert match.ambassador_registration is not None
    _email_proposal(match.ambassador_registration, match)


@is_side_effect_of(MATCH_PROPOSED)
def notify_referee_of_proposal(registration: Registration, **kwargs: object) -> None:
    """Email the referee side that a match has been proposed.

    Args:
        registration: The registration passed to ``propose_match`` (may be
            either side; the match itself is read from ``return_value``).
        kwargs: Must include ``return_value`` (the created ``Match``).
    """
    match = kwargs["return_value"]
    assert isinstance(match, Match)
    assert match.referee_registration is not None
    _email_proposal(match.referee_registration, match)


# --- match_accepted -----------------------------------------------------------
# Origin: record_acceptance(match, registration) -> Match. All three handlers
# fire on every call and guard on match.status (read from the mutated `match`
# argument, not return_value) so the right notification fires for the first
# accept (PENDING) vs the mutual accept (ACCEPTED).


@is_side_effect_of(MATCH_ACCEPTED)
def notify_waiting_partner_of_accept(
    match: Match, registration: Registration, **kwargs: object
) -> None:
    """Nudge the party who has not yet responded, on the first accept.

    No-ops unless ``match.status`` is PENDING (one-sided accept) — the mutual-
    accept case is handled by the confirmation handlers below.
    """
    if match.status != Match.Status.PENDING:
        return
    assert match.ambassador_registration is not None
    assert match.referee_registration is not None
    waiting = (
        match.referee_registration
        if match.side_of(registration) == Match.Side.AMBASSADOR
        else match.ambassador_registration
    )
    _email_partner_accepted(waiting, match)


@is_side_effect_of(MATCH_ACCEPTED)
def notify_ambassador_of_confirmation(
    match: Match, registration: Registration, **kwargs: object
) -> None:
    """Reveal the referee's contact details to the ambassador on mutual accept.

    No-ops unless ``match.status`` is ACCEPTED.
    """
    if match.status != Match.Status.ACCEPTED:
        return
    assert match.ambassador_registration is not None
    assert match.referee_registration is not None
    _email_confirmation(match.ambassador_registration, match.referee_registration)


@is_side_effect_of(MATCH_ACCEPTED)
def notify_referee_of_confirmation(
    match: Match, registration: Registration, **kwargs: object
) -> None:
    """Reveal the ambassador's contact details to the referee on mutual accept.

    No-ops unless ``match.status`` is ACCEPTED.
    """
    if match.status != Match.Status.ACCEPTED:
        return
    assert match.ambassador_registration is not None
    assert match.referee_registration is not None
    _email_confirmation(match.referee_registration, match.ambassador_registration)


# --- match_declined ------------------------------------------------------------
# Origin: record_decline(match, registration) -> Match. Only the kept-faith
# (non-declining) side is notified; the decliner receives nothing.


@is_side_effect_of(MATCH_DECLINED)
def notify_kept_faith_of_decline(
    match: Match, registration: Registration, **kwargs: object
) -> None:
    """Notify the non-declining party that the match ended and they're re-queued.

    The recipient is derived from ``match.declined_by`` — the side opposite
    the one that declined.
    """
    assert match.ambassador_registration is not None
    assert match.referee_registration is not None
    kept_faith = (
        match.referee_registration
        if match.declined_by == Match.Side.AMBASSADOR
        else match.ambassador_registration
    )
    _email_requeued(kept_faith)


# --- match_expired --------------------------------------------------------------
# Origin: expire_match(match) -> None. Each per-recipient handler picks
# requeued-vs-window-expired copy from that side's own *_accepted_at.


@is_side_effect_of(MATCH_EXPIRED)
def notify_ambassador_of_expiry(match: Match, **kwargs: object) -> None:
    """Notify the ambassador side of a lapsed match's outcome for them."""
    assert match.ambassador_registration is not None
    if match.ambassador_accepted_at is not None:
        _email_requeued(match.ambassador_registration)
    else:
        _email_window_expired(match.ambassador_registration)


@is_side_effect_of(MATCH_EXPIRED)
def notify_referee_of_expiry(match: Match, **kwargs: object) -> None:
    """Notify the referee side of a lapsed match's outcome for them."""
    assert match.referee_registration is not None
    if match.referee_accepted_at is not None:
        _email_requeued(match.referee_registration)
    else:
        _email_window_expired(match.referee_registration)


# --- match_no_show ---------------------------------------------------------
# Origin: report_no_show(match, registration) -> Match. `registration` is the
# reporter; the accused is the other side.


@is_side_effect_of(MATCH_NO_SHOW)
def notify_accused_of_no_show(
    match: Match, registration: Registration, **kwargs: object
) -> None:
    """Notify the accused party (the side opposite the reporter)."""
    assert match.ambassador_registration is not None
    assert match.referee_registration is not None
    side = match.side_of(registration)
    accused = (
        match.referee_registration
        if side == Match.Side.AMBASSADOR
        else match.ambassador_registration
    )
    _email_no_show(accused)


@is_side_effect_of(MATCH_NO_SHOW)
def notify_reporter_of_requeue(
    match: Match, registration: Registration, **kwargs: object
) -> None:
    """Notify the reporter (kept-faith party, re-queued to the front)."""
    _email_requeued(registration)
