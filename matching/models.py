# Foundational matchmaking models: Season and PriceCategory.
#
# Registrations, the matching pool, and matches are scoped to a Season. A
# PriceCategory is ordered data whose ordering drives match eligibility
# (CLAUDE.md "Match eligibility"). Fixed choice values are TextChoices with
# UPPER_CASE values.

from __future__ import annotations

from decimal import Decimal

from django.db import models
from django.utils.translation import gettext_lazy as _

from accounts.models import Account
from core.models import BaseModel, BaseQuerySet


class SeasonQuerySet(BaseQuerySet):
    """Queryset for Season."""

    def active(self) -> SeasonQuerySet:
        """Return the season(s) currently open for registration."""
        return self.filter(is_active=True)


class Season(BaseModel):
    """A campaign period that scopes registrations, the pool, and matches."""

    name = models.CharField(max_length=32, unique=True, help_text="e.g. 2026/27")
    slug = models.SlugField(unique=True)
    is_active = models.BooleanField(
        default=False,
        help_text="Whether this season is currently open for registration.",
    )
    contact_window_hours = models.PositiveIntegerField(
        default=72,
        help_text=(
            "Hours a matched pair has to mutually accept before the match is "
            "cancelled and both re-queue."
        ),
    )
    registration_opens_at = models.DateTimeField(null=True, blank=True)
    registration_closes_at = models.DateTimeField(null=True, blank=True)

    objects = SeasonQuerySet.as_manager()

    class Meta:
        ordering = ["-name"]

    def to_string(self) -> str:
        """Return the season's name."""
        return self.name


class PriceCategoryQuerySet(BaseQuerySet):
    """Queryset for PriceCategory."""

    def for_season(self, season: Season) -> PriceCategoryQuerySet:
        """Return the price categories belonging to ``season``."""
        return self.filter(season=season)


class PriceCategory(BaseModel):
    """An ordered pass price category within a season.

    ``order`` defines the price-category ranking used by match eligibility: a
    referee's category must rank greater than or equal to the ambassador's.
    """

    class Code(models.TextChoices):
        """Pass price categories, ordered child < adult < senior."""

        CHILD = "CHILD", _("Child")
        ADULT = "ADULT", _("Adult")
        SENIOR = "SENIOR", _("Senior")

    season = models.ForeignKey(
        Season,
        on_delete=models.CASCADE,
        related_name="price_categories",
    )
    code = models.CharField(max_length=16, choices=Code.choices)
    order = models.PositiveIntegerField(
        help_text="Rank within the season; higher means a higher category."
    )
    label = models.CharField(
        max_length=64,
        help_text="Display label (translated at render time).",
    )
    full_price = models.DecimalField(
        max_digits=8, decimal_places=2, default=Decimal("0.00")
    )
    discounted_price = models.DecimalField(
        max_digits=8, decimal_places=2, default=Decimal("0.00")
    )

    objects = PriceCategoryQuerySet.as_manager()

    class Meta:
        ordering = ["season", "order"]
        constraints = [
            models.UniqueConstraint(
                fields=["season", "code"], name="unique_category_per_season"
            ),
            models.UniqueConstraint(
                fields=["season", "order"], name="unique_order_per_season"
            ),
        ]

    def to_string(self) -> str:
        """Return a human-readable label for the category."""
        return f"{self.season} · {self.get_code_display()}"


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

    def for_season(self, season: Season) -> RegistrationQuerySet:
        """Return registrations scoped to ``season``."""
        return self.filter(season=season)

    def ambassadors(self) -> RegistrationQuerySet:
        """Return ambassador (referrer) registrations."""
        return self.filter(role=Registration.Role.AMBASSADOR)

    def referees(self) -> RegistrationQuerySet:
        """Return referee (referred) registrations."""
        return self.filter(role=Registration.Role.REFEREE)

    def waiting(self) -> RegistrationQuerySet:
        """Return registrations still waiting in the pool."""
        return self.filter(status=Registration.Status.WAITING)


class Registration(BaseModel):
    """A participant's enrolment into a season's pool in one role.

    Holds the role, chosen price category, soft location preference, the
    prior-season attestation that drives match eligibility, the pool status, and
    the queue priority. One registration per account per season.
    """

    class Role(models.TextChoices):
        """The two participant roles. Fixed once registered (CLAUDE.md)."""

        AMBASSADOR = "AMBASSADOR", _("Ambassador")
        REFEREE = "REFEREE", _("Referee")

    class Status(models.TextChoices):
        """Lifecycle of a registration in the pool."""

        WAITING = "WAITING", _("Waiting")
        MATCHED = "MATCHED", _("Matched")
        CONFIRMED = "CONFIRMED", _("Confirmed")
        WITHDRAWN = "WITHDRAWN", _("Withdrawn")

    season = models.ForeignKey(
        Season,
        on_delete=models.CASCADE,
        related_name="registrations",
    )
    account = models.ForeignKey(
        Account,
        on_delete=models.CASCADE,
        related_name="registrations",
    )
    role = models.CharField(max_length=16, choices=Role.choices)
    price_category = models.ForeignKey(
        PriceCategory,
        on_delete=models.PROTECT,
        related_name="registrations",
    )
    preferred_location = models.CharField(
        max_length=16,
        choices=Resort.choices,
        blank=True,
        help_text="Soft preference; used to rank matches, never to gate them.",
    )
    held_prior_pass = models.BooleanField(
        help_text=(
            "Prior-season attestation. Ambassadors confirm they held a 4 Vallées "
            "pass (True); referees confirm they did not (False)."
        ),
    )
    discount_eligible = models.BooleanField(
        default=True,
        help_text=(
            "False for Mont 4 / special-reduction ambassadors, who still supply a "
            "valid match but take no discount themselves."
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

    objects = RegistrationQuerySet.as_manager()

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["season", "account"],
                name="unique_registration_per_season",
            ),
        ]

    def to_string(self) -> str:
        """Return a human-readable label for the registration."""
        return f"{self.account.user} · {self.get_role_display()} · {self.season}"
