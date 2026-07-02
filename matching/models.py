# Matching-domain models: Registration and Match.
#
# The data model is intentionally lean — one season at a time (configured via
# REGISTRATION_OPENS_AT / REGISTRATION_CLOSES_AT env vars), adults-only (no
# PriceCategory), one registration per user (OneToOneField). See
# docs/decisions/0005-single-season-matching-engine.md for the rationale.
#
# Fixed choice values are TextChoices with UPPER_CASE values (CLAUDE.md).
#
# Two independent state machines (VERB-44 / ADR 0011, updated VERB-74):
#   Registration.Status tracks pool standing:
#     UNVERIFIED → VERIFIED (on email confirm) → WITHDRAWN | SUSPENDED
#     VERIFIED → PAUSED (decline or non-response; self-rejoin via rejoin_queue)
#     PAUSED → VERIFIED (user rejoins from their account page)
#   Match.Status tracks the match lifecycle:
#     PROPOSED → PENDING (one side accepted) → ACCEPTED | DECLINED | EXPIRED
#     ACCEPTED → CANCELLED (post-accept no-show report)

from __future__ import annotations

from datetime import datetime

from django.conf import settings
from django.db import models
from django.db.models import Exists, OuterRef, Q
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from django_countries.fields import CountryField

from core.exceptions import StateTransitionError
from core.models import BaseModel, BaseQuerySet


class Resort(models.TextChoices):
    """4 Vallées ticket offices / resorts a participant may prefer.

    Location is a *soft* preference (CLAUDE.md "Match eligibility"): the engine
    prefers a shared resort but never hard-gates on it. Values are UPPER_CASE.
    """

    VERBIER = "VERBIER", _("Verbier")
    THYON = "THYON", _("Thyon")
    NENDAZ = "NENDAZ", _("Nendaz")
    VEYSONNAZ = "VEYSONNAZ", _("Veysonnaz")
    LA_TZOUMAZ = "LA_TZOUMAZ", _("La Tzoumaz")
    BRUSON = "BRUSON", _("Bruson")


class RegistrationQuerySet(BaseQuerySet):
    """Queryset for Registration."""

    def ambassadors(self) -> RegistrationQuerySet:
        """Return ambassador (referrer) registrations."""
        return self.filter(role=Registration.Role.AMBASSADOR)

    def referees(self) -> RegistrationQuerySet:
        """Return referee (referred) registrations."""
        return self.filter(role=Registration.Role.REFEREE)

    def verified(self) -> RegistrationQuerySet:
        """Return VERIFIED registrations (confirmed and eligible to be pooled)."""
        return self.filter(status=Registration.Status.VERIFIED)

    def _without_active_match(self) -> RegistrationQuerySet:
        """Exclude registrations already holding a non-terminal match.

        A registration with a PROPOSED, PENDING, or ACCEPTED match is already
        committed to that match and must not enter the pool as a counterpart.

        Exclusion is expressed as an ``Exists`` subquery rather than a reverse-FK
        join so the outer query stays one-row-per-registration. A join would
        multiply rows and force a ``.distinct()`` — but Postgres forbids
        ``SELECT ... FOR UPDATE`` on a ``DISTINCT`` query, and this queryset is
        chained with ``select_for_update()`` in ``propose_match``. The subquery
        keeps both constraints satisfied.
        """
        _active = [
            Match.Status.PROPOSED,
            Match.Status.PENDING,
            Match.Status.ACCEPTED,
        ]
        active_matches = Match.objects.filter(
            Q(ambassador_registration=OuterRef("pk"))
            | Q(referee_registration=OuterRef("pk")),
            status__in=_active,
        )
        return self.filter(~Exists(active_matches))

    def eligible_ambassadors(self) -> RegistrationQuerySet:
        """Return VERIFIED ambassadors in the pool with a valid prior pass."""
        return (
            self.ambassadors()
            .verified()
            ._without_active_match()
            .filter(
                prior_pass__in=[
                    Registration.PriorPass.SEASONAL,
                    Registration.PriorPass.ANNUAL,
                    Registration.PriorPass.MONT4,
                ]
            )
        )

    def eligible_referees(self) -> RegistrationQuerySet:
        """Return VERIFIED referees in the pool who are genuinely new."""
        return (
            self.referees()
            .verified()
            ._without_active_match()
            .filter(prior_pass=Registration.PriorPass.NONE)
        )


class Registration(BaseModel):
    """A participant's enrolment in the current season's pool.

    One registration per user (OneToOneField). Holds the role, prior-pass
    attestation that gates match eligibility, soft location preference, the pool
    status, and the queue priority.

    Pool standing is tracked by Registration.Status (independent of any active
    Match). Match progress is tracked by Match.Status. See ADR 0011.
    """

    class Role(models.TextChoices):
        """The two participant roles. Fixed once registered (CLAUDE.md)."""

        AMBASSADOR = "AMBASSADOR", _("Ambassador")
        REFEREE = "REFEREE", _("Referee")

    class Status(models.TextChoices):
        """Pool-standing lifecycle of a registration.

        UNVERIFIED: created from the combined form but not yet email-confirmed;
            never enters the matching engine.
        VERIFIED: email confirmed and active in the pool (or re-joined after
            a paused registration was resumed via rejoin_queue).
        PAUSED: the participant declined a match or failed to respond within the
            contact window. They are out of the pool but their registration
            is retained. They may rejoin from their account page (VERB-74 /
            ADR 0013).
        WITHDRAWN: the participant withdrew themselves.
        SUSPENDED: the participant was suspended by the system (e.g. a
            post-accept no-show accusation).

        Match progress is tracked on Match.Status, not here (ADR 0011).
        """

        UNVERIFIED = "UNVERIFIED", _("Unverified")
        VERIFIED = "VERIFIED", _("Verified")
        PAUSED = "PAUSED", _("Paused")
        WITHDRAWN = "WITHDRAWN", _("Withdrawn")
        SUSPENDED = "SUSPENDED", _("Suspended")

    class PriorPass(models.TextChoices):
        """Prior-season pass type, used to gate match eligibility.

        Ambassadors must hold SEASONAL, ANNUAL, or MONT4 (Mont 4 Card / special
        reduction). Referees must be genuinely new and resolve to NONE.
        UPPER_CASE values (CLAUDE.md "TextChoices").
        """

        NONE = "NONE", _("None — I did not hold a prior pass")
        SEASONAL = "SEASONAL", _("Seasonal pass (4 Vallées)")
        ANNUAL = "ANNUAL", _("Annual pass (4 Vallées)")
        MONT4 = "MONT4", _("Mont 4 Card / special reduction")

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="registration",
    )
    role = models.CharField(max_length=16, choices=Role.choices)
    # Phone is contact PII; revealed only after mutual match accept (Invariant 1).
    phone = models.CharField(max_length=32, blank=True)
    # Language codes are external identifiers (ISO 639) keyed to settings.LANGUAGES,
    # not a domain enum, so the UPPER_CASE TextChoices rule does not apply here.
    preferred_language = models.CharField(
        max_length=8,
        choices=settings.LANGUAGES,
        blank=True,
    )
    preferred_location = models.CharField(
        max_length=16,
        choices=Resort.choices,
        blank=True,
        help_text="Soft preference; used to rank matches, never to gate them.",
    )
    nationality = CountryField(
        blank=True,
        help_text="ISO 3166-1 alpha-2 country code. Optional; collected for analytics.",
    )
    prior_pass = models.CharField(
        max_length=16,
        choices=PriorPass.choices,
        default=PriorPass.NONE,
        help_text=(
            "Prior-season pass attestation. Ambassadors must hold SEASONAL, ANNUAL, or "
            "MONT4. Referees are genuinely new and hold NONE."
        ),
    )
    status = models.CharField(
        max_length=16,
        choices=Status.choices,
        default=Status.VERIFIED,
    )
    priority = models.IntegerField(
        default=0,
        help_text=(
            "Queue priority; higher is nearer the front. Adjusted on rejoin "
            "(priority -= 1 each time) and on requeue-to-front (priority += 1)."
        ),
    )
    fee_chf = models.PositiveIntegerField(
        default=0,
        help_text=(
            "Prepaid registration fee in CHF, locked at signup from the "
            "REGISTRATION_FEE_TIERS schedule (see "
            "matching.pricing_config.fee_chf_for). Frozen — never recomputed "
            "against a later tier."
        ),
    )
    accepted_terms = models.JSONField(
        default=list,
        blank=True,
        help_text=(
            "Ordered list of consent statement texts accepted at registration "
            "(eligibility declaration first, then T&C). Stored as displayed under "
            "the active language at the time of registration."
        ),
    )
    terms_accepted_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text=(
            "Tz-aware timestamp at which the participant accepted the statements."
        ),
    )

    # --- Geolocation fields (admin-only, never shown to participants) ----------
    # Derived from the request's client IP at registration time (in memory only;
    # the IP itself is never stored). Stored as free-text resolved from the
    # MaxMind GeoLite2-City database. Empty when the database is absent or the
    # IP is private/unroutable (e.g. local development).
    registration_country = models.CharField(
        max_length=64,
        blank=True,
        default="",
        help_text=(
            "Country name derived from the client IP at registration time "
            "(admin-only, never shown to participants). The raw IP is never stored."
        ),
    )
    registration_region = models.CharField(
        max_length=128,
        blank=True,
        default="",
        help_text=(
            "Region / subdivision name derived from the client IP at registration "
            "time (admin-only, never shown to participants). "
            "The raw IP is never stored."
        ),
    )

    objects = RegistrationQuerySet.as_manager()

    class Meta:
        ordering = ["-created_at"]

    @property
    def is_ambassador(self) -> bool:
        """True if role is AMBASSADOR (derived from own state — a property)."""
        return self.role == self.Role.AMBASSADOR

    @property
    def is_referee(self) -> bool:
        """True if role is REFEREE (derived from own state — a property)."""
        return self.role == self.Role.REFEREE

    def pause(self) -> Registration:
        """Mutate this registration's own state to PAUSED, in memory only.

        Model-logic layer (VERB-100): mutates only this instance's own field,
        never saves, never touches another object, and never fires a side
        effect. Callers are responsible for persisting the change (e.g.
        ``registration.pause().save(update_fields=["status"])``) and for any
        cross-object coordination (see ``matching.services.pause_registration``).

        Only VERIFIED is a legal source state — a registration is paused after
        declining a match or failing to respond within the contact window, and
        both of those paths act on a VERIFIED registration (VERB-74 / ADR
        0013). This is the fail-hard-low guard: an illegal source state raises
        immediately, in the model method, rather than being silently applied.

        Returns:
            self, so calls can be chained.

        Raises:
            StateTransitionError: if ``self.status`` is not ``VERIFIED``.
        """
        if self.status != Registration.Status.VERIFIED:
            raise StateTransitionError(
                current=self.status,
                proposed=Registration.Status.PAUSED,
                obj=self,
            )
        self.status = Registration.Status.PAUSED
        return self

    def requeue(self, priority: int = 1) -> Registration:
        """Mutate this registration's own state to re-join the pool, in memory only.

        Model-logic layer (VERB-100): mutates only this instance's own fields
        (status and priority), never saves, never touches another object, and
        never fires a side effect. Callers are responsible for persisting the
        change and for any cross-object coordination (see
        ``matching.services.requeue_to_front``).

        Args:
            priority: The amount to add to ``self.priority``. Defaults to
                ``1`` — a kept-faith/wronged party re-joins at the front of
                the queue (``priority += 1``).

        Returns:
            self, so calls can be chained.
        """
        self.status = Registration.Status.VERIFIED
        self.priority += priority
        return self

    def suspend(self) -> Registration:
        """Mutate this registration's own state to SUSPENDED, in memory only.

        Model-logic layer (VERB-104 / ADR 0017): mutates only this instance's
        own ``status`` field, never saves, never touches another object, and
        never fires a side effect. Callers are responsible for persisting the
        change (e.g. ``registration.suspend().save(update_fields=["status"])``)
        and for any cross-object coordination (see
        ``matching.services.suspend_for_no_show``).

        Only VERIFIED is a legal source state — a registration is suspended
        when the other party files a post-accept no-show report, and the
        accused is necessarily VERIFIED at that point: an ACCEPTED match keeps
        both parties out of the pool while leaving their standing VERIFIED
        (VERB-44 / ADR 0011). This is the fail-hard-low guard, mirroring
        ``pause()``: an illegal source state raises immediately.

        Returns:
            self, so calls can be chained.

        Raises:
            StateTransitionError: if ``self.status`` is not ``VERIFIED``.
        """
        if self.status != Registration.Status.VERIFIED:
            raise StateTransitionError(
                current=self.status,
                proposed=Registration.Status.SUSPENDED,
                obj=self,
            )
        self.status = Registration.Status.SUSPENDED
        return self

    def to_string(self) -> str:
        """Return a human-readable label for the registration."""
        return f"{self.user} · {self.get_role_display()}"

    def __str__(self) -> str:
        """Delegate to to_string."""
        return self.to_string()


class MatchQuerySet(BaseQuerySet):
    """Queryset for Match."""

    def proposed(self) -> MatchQuerySet:
        """Return matches currently in the PROPOSED state."""
        return self.filter(status=Match.Status.PROPOSED)

    def lapsed(self, cutoff: datetime) -> MatchQuerySet:
        """Return PROPOSED or PENDING matches whose contact window has expired.

        Shorthand for the expiry-sweep candidate set: non-terminal active matches
        filtered to those whose expires_at is at or before ``cutoff``. Both
        PROPOSED and PENDING are eligible for expiry.

        ``cutoff`` is a required, tz-aware "now" passed in by the caller
        (inversion of control, VERB-100) rather than read internally via
        ``timezone.now()`` — this keeps the queryset a pure function of its
        arguments and lets the caller control "now" for testing / sweep
        consistency.

        Args:
            cutoff: The tz-aware instant to compare ``expires_at`` against.
        """
        return self.filter(
            status__in=[Match.Status.PROPOSED, Match.Status.PENDING],
            expires_at__lte=cutoff,
        )

    def active(self) -> MatchQuerySet:
        """Return non-terminal matches (PROPOSED, PENDING, and ACCEPTED).

        Excludes DECLINED, EXPIRED, and CANCELLED — all three are terminal
        states in the match state machine.
        """
        return self.exclude(
            status__in=[
                Match.Status.DECLINED,
                Match.Status.EXPIRED,
                Match.Status.CANCELLED,
            ]
        )


class Match(BaseModel):
    """A system-proposed pairing of one ambassador and one referee.

    Created by the matching engine when an eligible pair is found. Accumulates
    rows (no unique constraint on the registration FKs) so that declined and
    expired matches are preserved as history.

    State machine (VERB-44 / ADR 0011):
        PROPOSED  — engine paired them; neither side has responded yet.
        PENDING   — one side has accepted; awaiting the other.
        ACCEPTED  — both sides accepted (terminal success).
        DECLINED  — one side declined (terminal).
        EXPIRED   — contact window lapsed without mutual accept (terminal).
        CANCELLED — previously ACCEPTED; one party filed a post-accept no-show
                    report (ADR 0007).

    Contact PII is never revealed until the match is ACCEPTED (Invariant 1).

    Per-party responses are tracked as typed nullable columns rather than
    generic JSON or separate FK rows — the parties are always exactly the two
    registrations on the match, so a FK is over-built (ADR 0007).
    """

    class Status(models.TextChoices):
        """Match lifecycle states. UPPER_CASE values (CLAUDE.md).

        PENDING is new in VERB-44: the intermediate state after one side
        accepts but before both have accepted. CANCELLED replaces the former
        ABANDONED (post-accept no-show path). ACCEPTED is kept as the terminal
        success state (not renamed).
        """

        PROPOSED = "PROPOSED", _("Proposed")
        PENDING = "PENDING", _("Pending")
        ACCEPTED = "ACCEPTED", _("Accepted")
        DECLINED = "DECLINED", _("Declined")
        EXPIRED = "EXPIRED", _("Expired")
        CANCELLED = "CANCELLED", _("Cancelled")

    class Side(models.TextChoices):
        """Which side of the match a party is on. UPPER_CASE values (CLAUDE.md).

        Used to record which party declined or reported a no-show without
        storing a full FK to Registration (the two parties are always known
        from the match itself).
        """

        AMBASSADOR = "AMBASSADOR", _("Ambassador")
        REFEREE = "REFEREE", _("Referee")

    # FKs are non-nullable: registrations are never deleted on decline/expiry
    # (VERB-74 / ADR 0013). CASCADE ensures referential integrity is maintained
    # if a registration is explicitly removed (e.g. account deletion).
    ambassador_registration = models.ForeignKey(
        Registration,
        on_delete=models.CASCADE,
        related_name="matches_as_ambassador",
    )
    referee_registration = models.ForeignKey(
        Registration,
        on_delete=models.CASCADE,
        related_name="matches_as_referee",
    )
    status = models.CharField(
        max_length=16,
        choices=Status.choices,
        default=Status.PROPOSED,
    )
    expires_at = models.DateTimeField(
        help_text=(
            "When the contact window closes; both re-queue if not "
            "mutually accepted by then."
        ),
    )

    # --- Per-party response timestamps (VERB-18 / ADR 0007) -----------------

    ambassador_accepted_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="Tz-aware instant the ambassador accepted; null until they do.",
    )
    referee_accepted_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="Tz-aware instant the referee accepted; null until they do.",
    )
    declined_by = models.CharField(
        max_length=16,
        choices=Side.choices,
        blank=True,
        help_text="Which side declined; empty until a decline occurs.",
    )
    declined_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="Tz-aware instant the decline was recorded; null until then.",
    )
    no_show_reported_by = models.CharField(
        max_length=16,
        choices=Side.choices,
        blank=True,
        help_text=(
            "Which side filed the post-accept no-show report; empty until then."
        ),
    )
    no_show_reported_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="Tz-aware instant the no-show report was filed; null until then.",
    )

    objects = MatchQuerySet.as_manager()

    class Meta:
        ordering = ["-created_at"]

    def to_string(self) -> str:
        """Return a human-readable label for the match.

        Registration FKs are non-nullable (CASCADE; registrations are never
        deleted on decline or expiry — VERB-74 / ADR 0013).
        """
        amb = str(self.ambassador_registration.user)
        ref = str(self.referee_registration.user)
        return f"Match {self.pk}: {amb} ↔ {ref} [{self.get_status_display()}]"

    def __str__(self) -> str:
        """Delegate to to_string."""
        return self.to_string()

    def expire(self) -> Match:
        """Mutate this match's own state to EXPIRED, in memory only.

        Model-logic layer (VERB-100): mutates only this instance's own
        ``status`` field, never saves, never touches another object (e.g. the
        related registrations), and never fires a side effect (e.g. an email).
        Callers are responsible for persisting the change (e.g.
        ``match.expire().save(update_fields=["status", "updated_at"])``) and
        for any cross-object coordination — see
        ``matching.services.expire_match``.

        Returns:
            self, so calls can be chained.

        Raises:
            StateTransitionError: if ``self.status`` is not ``PROPOSED`` or
                ``PENDING``. This is the fail-hard-low guard: expiry is only
                legal from those two states, and an illegal source state
                raises immediately rather than being silently applied.
        """
        if self.status not in (Match.Status.PROPOSED, Match.Status.PENDING):
            raise StateTransitionError(
                current=self.status,
                proposed=Match.Status.EXPIRED,
                obj=self,
            )
        self.status = Match.Status.EXPIRED
        return self

    def accept(self, accepted_by: Registration) -> Match:
        """Record that ``accepted_by`` has accepted this match, in memory only.

        Model-logic layer (VERB-101 / ADR 0017): mutates only this instance's
        own fields — the accepting side's ``*_accepted_at`` timestamp and, via
        ``set_status``, ``status``. Never saves, never touches another object
        (e.g. a Payment), and never fires a side effect (e.g. an email). The
        accepting side is derived from the registration's own role
        (``is_ambassador`` / ``is_referee``): the caller passes the participant,
        not a side. Persisting the change and any cross-object coordination
        belong to ``matching.services.record_acceptance``.

        Idempotent: re-accepting by a side that has already accepted leaves its
        timestamp — and therefore the status — unchanged.

        Args:
            accepted_by: The registration (ambassador or referee) accepting.

        Returns:
            self, so calls can be chained.

        Raises:
            StateTransitionError: if ``self.status`` is not ``PROPOSED`` or
                ``PENDING``. This is the fail-hard-low guard: accepting is
                only legal from those two states, and an illegal source state
                raises immediately rather than being silently applied.
        """
        if self.status not in (Match.Status.PROPOSED, Match.Status.PENDING):
            raise StateTransitionError(
                current=self.status,
                proposed=Match.Status.ACCEPTED,
                obj=self,
            )

        now = timezone.now()
        if accepted_by.is_ambassador and self.ambassador_accepted_at is None:
            self.ambassador_accepted_at = now
        if accepted_by.is_referee and self.referee_accepted_at is None:
            self.referee_accepted_at = now

        self.set_status()
        return self

    def set_status(self) -> None:
        """Derive ``status`` from the two acceptance timestamps, in memory.

        Both sides accepted → ``ACCEPTED``; exactly one → ``PENDING``; neither →
        ``PROPOSED``. A pure own-state computation that maps the accept state
        onto the status; it never saves. Callers must first establish that the
        match is in an accept-eligible state (see ``accept``).
        """
        if self.ambassador_accepted_at and self.referee_accepted_at:
            self.status = Match.Status.ACCEPTED
        elif self.ambassador_accepted_at or self.referee_accepted_at:
            self.status = Match.Status.PENDING
        else:
            self.status = Match.Status.PROPOSED

    def decline(self, declined_by: Registration) -> Match:
        """Record that ``declined_by`` has declined this match, in memory only.

        Model-logic layer (VERB-102 / ADR 0017): mutates only this instance's
        own fields — ``declined_by``, ``declined_at``, and ``status``. Never
        saves, never touches another object (e.g. the decliner's Registration),
        and never fires a side effect (e.g. an email). The declining side is
        derived from the registration's own role via ``side_of``: the caller
        passes the participant, not a side. Persisting the change and all
        cross-object coordination — pausing the decliner, re-queuing the other
        party, emailing — belong to ``matching.services.record_decline`` and
        ``decline_match``.

        Args:
            declined_by: The registration (ambassador or referee) declining.

        Returns:
            self, so calls can be chained.

        Raises:
            StateTransitionError: if ``self.status`` is not ``PROPOSED`` or
                ``PENDING``. This is the fail-hard-low guard: declining is only
                legal from those two states, and an illegal source state raises
                immediately rather than being silently applied.
        """
        if self.status not in (Match.Status.PROPOSED, Match.Status.PENDING):
            raise StateTransitionError(
                current=self.status,
                proposed=Match.Status.DECLINED,
                obj=self,
            )
        self.declined_by = self.side_of(declined_by)
        self.declined_at = timezone.now()
        self.status = Match.Status.DECLINED
        return self

    def withdraw_acceptance(self, withdrawn_by: Registration) -> Match:
        """Clear ``withdrawn_by``'s acceptance and revert to PROPOSED, in memory.

        Model-logic layer (VERB-103 / ADR 0017): the inverse of the first
        accept. Mutates only this instance's own fields — the withdrawing
        side's ``*_accepted_at`` timestamp (cleared to ``None``) and, via
        ``set_status``, ``status`` (``PENDING → PROPOSED``). Never saves, never
        touches another object, and never fires a side effect. The withdrawing
        side is derived from the registration's own role via ``side_of``.
        Persisting the change and writing the transition log belong to
        ``matching.services.withdraw_acceptance``.

        A clean, no-penalty un-accept: nothing is re-queued and no flake
        penalty is applied. The guard that the match is ``PENDING`` (rather
        than ``ACCEPTED``) is what keeps it safe — a mutually-accepted match is
        terminal and contact-revealed, so there is no window in which a
        withdrawal could un-reveal PII (Invariant 1).

        Args:
            withdrawn_by: The registration (ambassador or referee) withdrawing.

        Returns:
            self, so calls can be chained.

        Raises:
            StateTransitionError: if ``self.status`` is not ``PENDING``, or if
                the withdrawing side has not accepted (nothing to withdraw).
                Both are fail-hard-low guards: the proposed target is
                ``PROPOSED``.
        """
        if self.status != Match.Status.PENDING:
            raise StateTransitionError(
                current=self.status,
                proposed=Match.Status.PROPOSED,
                obj=self,
            )

        side = self.side_of(withdrawn_by)
        if side == Match.Side.AMBASSADOR:
            if self.ambassador_accepted_at is None:
                raise StateTransitionError(
                    current=self.status,
                    proposed=Match.Status.PROPOSED,
                    obj=self,
                )
            self.ambassador_accepted_at = None
        else:
            if self.referee_accepted_at is None:
                raise StateTransitionError(
                    current=self.status,
                    proposed=Match.Status.PROPOSED,
                    obj=self,
                )
            self.referee_accepted_at = None

        # On a PENDING match exactly one side had accepted; clearing it leaves
        # neither, so set_status derives PROPOSED.
        self.set_status()
        return self

    def cancel(self, reported_by: Registration) -> Match:
        """Record a post-accept no-show and revert to CANCELLED, in memory only.

        Model-logic layer (VERB-104 / ADR 0017): mutates only this instance's
        own fields — ``no_show_reported_by``, ``no_show_reported_at``, and
        ``status`` (``ACCEPTED → CANCELLED``). Never saves, never touches
        another object (e.g. the accused's Registration or a Payment), and
        never fires a side effect (e.g. an email). The reporting side is
        derived from the registration's own role via ``side_of``: the caller
        passes the reporter, not a side. Suspending the accused, forfeiting
        their deposit, re-queuing the reporter, and notifying the accused all
        belong to ``matching.services.report_no_show``.

        Args:
            reported_by: The reporter's registration (the party who showed up).

        Returns:
            self, so calls can be chained.

        Raises:
            StateTransitionError: if ``self.status`` is not ``ACCEPTED``, or if
                a no-show has already been reported on this match
                (first-report-wins). Both are fail-hard-low guards: the
                proposed target is ``CANCELLED``.
        """
        if self.status != Match.Status.ACCEPTED or self.no_show_reported_by:
            raise StateTransitionError(
                current=self.status,
                proposed=Match.Status.CANCELLED,
                obj=self,
            )
        self.no_show_reported_by = self.side_of(reported_by)
        self.no_show_reported_at = timezone.now()
        self.status = Match.Status.CANCELLED
        return self

    def side_of(self, registration: Registration) -> Match.Side:
        """Return which side of this match ``registration`` is on.

        Compares by primary key so the check works on both in-memory and
        freshly-fetched instances.

        Raises:
            ValueError: if ``registration`` is neither party on this match.
        """
        if registration.pk == self.ambassador_registration_id:
            return Match.Side.AMBASSADOR
        if registration.pk == self.referee_registration_id:
            return Match.Side.REFEREE
        raise ValueError(
            f"Registration pk={registration.pk} is not a party on Match pk={self.pk}."
        )
