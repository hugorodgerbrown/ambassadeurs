# Foundational matchmaking models: Season and PriceCategory.
#
# Registrations, the matching pool, and matches are scoped to a Season. A
# PriceCategory is ordered data whose ordering drives match eligibility
# (CLAUDE.md "Match eligibility"). Fixed choice values are TextChoices with
# UPPER_CASE values.

from decimal import Decimal

from django.db import models
from django.utils.translation import gettext_lazy as _

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
