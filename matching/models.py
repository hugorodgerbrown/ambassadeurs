# Matching-domain models: Registration and Match.
#
# The data model is intentionally lean — one season at a time (configured via
# REGISTRATION_OPENS_AT / REGISTRATION_CLOSES_AT env vars), adults-only (no
# PriceCategory), one registration per user (OneToOneField). See
# docs/decisions/0005-single-season-matching-engine.md for the rationale.
#
# Fixed choice values are TextChoices with UPPER_CASE values (CLAUDE.md).

from __future__ import annotations

from django.conf import settings
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

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

    def waiting(self) -> RegistrationQuerySet:
        """Return registrations still waiting in the pool."""
        return self.filter(status=Registration.Status.WAITING)

    def eligible_ambassadors(self) -> RegistrationQuerySet:
        """Return waiting ambassadors who hold a valid prior pass."""
        return (
            self.ambassadors()
            .waiting()
            .filter(
                prior_pass__in=[
                    Registration.PriorPass.SEASONAL,
                    Registration.PriorPass.ANNUAL,
                    Registration.PriorPass.MONT4,
                ]
            )
        )

    def eligible_referees(self) -> RegistrationQuerySet:
        """Return waiting referees who are genuinely new (no prior pass)."""
        return self.referees().waiting().filter(prior_pass=Registration.PriorPass.NONE)


class Registration(BaseModel):
    """A participant's enrolment in the current season's pool.

    One registration per user (OneToOneField). Holds the role, prior-pass
    attestation that gates match eligibility, soft location preference, the pool
    status, and the queue priority.
    """

    class Role(models.TextChoices):
        """The two participant roles. Fixed once registered (CLAUDE.md)."""

        AMBASSADOR = "AMBASSADOR", _("Ambassador")
        REFEREE = "REFEREE", _("Referee")

    class Status(models.TextChoices):
        """Lifecycle of a registration in the pool.

        PENDING: created from the combined form but not yet email-confirmed;
        never matched. WAITING: confirmed and waiting in the pool.
        """

        PENDING = "PENDING", _("Pending")
        WAITING = "WAITING", _("Waiting")
        MATCHED = "MATCHED", _("Matched")
        CONFIRMED = "CONFIRMED", _("Confirmed")
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
        default=Status.WAITING,
    )
    priority = models.IntegerField(
        default=0,
        help_text="Queue priority; higher is nearer the front. Adjusted by flaking.",
    )
    prior_decline_count = models.PositiveIntegerField(
        default=0,
        help_text=(
            "Number of prior DECLINED matches associated with this email address "
            "before this registration was created. Computed at registration time "
            "from Match.declined_by_email_hash."
        ),
    )
    flake_count = models.IntegerField(
        default=0,
        help_text=(
            "Recorded flakes (non-responses and post-accept no-shows; not declines). "
            "2 auto-suspends."
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

    objects = RegistrationQuerySet.as_manager()

    class Meta:
        ordering = ["-created_at"]

    def is_ambassador(self) -> bool:
        """Return True if role is AMBASSADOR."""
        return self.role == self.Role.AMBASSADOR

    def is_referee(self) -> bool:
        """Return True if role is REFEREE."""
        return self.role == self.Role.REFEREE

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

    def lapsed(self) -> MatchQuerySet:
        """Return PROPOSED matches whose contact window has expired.

        Shorthand for the expiry-sweep candidate set: proposed() filtered
        to those whose expires_at is at or before the current instant.
        """
        return self.proposed().filter(expires_at__lte=timezone.now())

    def active(self) -> MatchQuerySet:
        """Return non-terminal matches (PROPOSED and ACCEPTED).

        Excludes DECLINED, EXPIRED, and ABANDONED — all three are terminal
        states in the match state machine.
        """
        return self.exclude(
            status__in=[
                Match.Status.DECLINED,
                Match.Status.EXPIRED,
                Match.Status.ABANDONED,
            ]
        )

    def for_decline_hash(self, email_hash: str) -> MatchQuerySet:
        """Return DECLINED matches whose decliner's email hash matches ``email_hash``.

        Used at registration time to count prior declines by the same address.
        The method name avoids a clash with the model field
        ``declined_by_email_hash``.
        """
        return self.filter(
            status=Match.Status.DECLINED,
            declined_by_email_hash=email_hash,
        )


class Match(BaseModel):
    """A system-proposed pairing of one ambassador and one referee.

    Created by the matching engine when an eligible pair is found. Accumulates
    rows (no unique constraint on the registration FKs) so that declined and
    expired matches are preserved as history.

    State machine:
        PROPOSED → ACCEPTED | DECLINED | EXPIRED
        ACCEPTED → ABANDONED  (post-accept no-show report; see ADR 0007)

    Contact PII is never revealed until both parties accept (Invariant 1).

    Per-party responses are tracked as typed nullable columns rather than
    generic JSON or separate FK rows — the parties are always exactly the two
    registrations on the match, so a FK is over-built (ADR 0007).
    """

    class Status(models.TextChoices):
        """Match lifecycle states. UPPER_CASE values (CLAUDE.md).

        ABANDONED is added in VERB-18: a mutually-accepted match where one
        party was reported as a post-accept no-show (ADR 0007).
        """

        PROPOSED = "PROPOSED", _("Proposed")
        ACCEPTED = "ACCEPTED", _("Accepted")
        DECLINED = "DECLINED", _("Declined")
        EXPIRED = "EXPIRED", _("Expired")
        ABANDONED = "ABANDONED", _("Abandoned")

    class Side(models.TextChoices):
        """Which side of the match a party is on. UPPER_CASE values (CLAUDE.md).

        Used to record which party declined or reported a no-show without
        storing a full FK to Registration (the two parties are always known
        from the match itself).
        """

        AMBASSADOR = "AMBASSADOR", _("Ambassador")
        REFEREE = "REFEREE", _("Referee")

    ambassador_registration = models.ForeignKey(
        Registration,
        on_delete=models.SET_NULL,
        null=True,
        related_name="matches_as_ambassador",
    )
    referee_registration = models.ForeignKey(
        Registration,
        on_delete=models.SET_NULL,
        null=True,
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
    declined_by_email_hash = models.CharField(
        max_length=64,
        blank=True,
        db_index=True,
        help_text=(
            "HMAC-SHA256 hex digest of the decliner's normalised email address, "
            "set at decline time so that prior-decline history is preserved after "
            "the decliner's User and Registration rows are deleted."
        ),
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

        Registration FKs are nullable (SET_NULL on User delete) so either side
        may be None on DECLINED matches where the decliner's row was removed.
        """
        amb_reg = self.ambassador_registration
        amb = str(amb_reg.user) if amb_reg is not None else "(deleted)"
        ref_reg = self.referee_registration
        ref = str(ref_reg.user) if ref_reg is not None else "(deleted)"
        return f"Match {self.pk}: {amb} ↔ {ref} [{self.get_status_display()}]"

    def __str__(self) -> str:
        """Delegate to to_string."""
        return self.to_string()

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
