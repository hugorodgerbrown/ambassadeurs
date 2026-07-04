# Public-app models.
#
# Holds FormDownload — a lightweight record of each application-form PDF
# download. No PII is stored: one row per download with only the timestamp
# from BaseModel. The count and date histogram are the conversion metric for the
# programme (there is no analytics stack in the project).
#
# Also holds SurveyResponse (VERB-111) — a single-question willingness-to-pay
# survey shown to free-tier registrants on the register_done ("You're in the
# queue") page. It asks directly for the highest refundable deposit the
# respondent would have been happy to pay to register. Responses are
# internal-only (Django admin) and inform the October-December deposit tier
# amounts (ADR 0014).

from __future__ import annotations

from django.db import models
from django.utils.translation import gettext_lazy as _

from core.models import BaseModel, BaseQuerySet
from matching.models import Registration


class FormDownloadQuerySet(BaseQuerySet):
    """Queryset for FormDownload."""


class FormDownload(BaseModel):
    """A record that the application-form PDF was downloaded.

    One row is created per request to the download view. No user FK, no IP
    address — the only data is the inherited ``created_at`` timestamp. This
    keeps the model free of PII while still providing a queryable conversion
    metric (how many visitors downloaded the form?).
    """

    objects = FormDownloadQuerySet.as_manager()

    class Meta:
        ordering = ["-created_at"]

    def to_string(self) -> str:
        """Return a human-readable label showing the download date and time."""
        return f"Form download · {self.created_at:%Y-%m-%d %H:%M}"

    def __str__(self) -> str:
        """Delegate to to_string."""
        return self.to_string()


class SurveyResponseQuerySet(BaseQuerySet):
    """Queryset for SurveyResponse."""


class SurveyResponse(BaseModel):
    """A willingness-to-pay survey response from a free-tier registrant (VERB-111).

    Shown once, on the ``register_done`` page, to a VERIFIED, free-tier
    (``fee_chf == 0``) registration that has not already responded.
    ``registration`` is a ``OneToOneField`` — a unique FK — so the database
    itself enforces "at most one response per registration"; ``SET_NULL``
    preserves the research row if the account is later deleted.

    A single required question asks directly for the highest refundable
    deposit the respondent would have been happy to pay to register
    (``max_deposit``).
    """

    class MaxDeposit(models.TextChoices):
        """The highest deposit the respondent would have been happy to pay."""

        NONE = "NONE", _("I would not have registered if there was a deposit")
        CHF_5 = "CHF_5", _("CHF 5")
        CHF_10 = "CHF_10", _("CHF 10")
        CHF_20 = "CHF_20", _("CHF 20")
        MORE = "MORE", _("More than CHF 20")

    registration = models.OneToOneField(
        Registration,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="survey_response",
        help_text=_(
            "The registration this response belongs to. SET_NULL so the "
            "research row survives account deletion."
        ),
    )
    max_deposit = models.CharField(
        max_length=16,
        choices=MaxDeposit.choices,
        help_text=_(
            "The highest deposit the respondent would have been happy to pay "
            "to register."
        ),
    )

    objects = SurveyResponseQuerySet.as_manager()

    class Meta:
        ordering = ["-created_at"]

    def to_string(self) -> str:
        """Return a human-readable label summarising the response."""
        return f"Survey response · {self.max_deposit}"

    def __str__(self) -> str:
        """Delegate to to_string."""
        return self.to_string()
