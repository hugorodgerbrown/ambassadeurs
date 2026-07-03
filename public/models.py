# Public-app models.
#
# Holds FormDownload — a lightweight record of each application-form PDF
# download. No PII is stored: one row per download with only the timestamp
# from BaseModel. The count and date histogram are the conversion metric for the
# programme (there is no analytics stack in the project).
#
# Also holds SurveyResponse (VERB-111) — a willingness-to-pay survey shown to
# free-tier registrants on the register_done ("You're in the queue") page. It
# asks whether a hypothetical refundable deposit at a shown price point
# (CHF 5/10/20) would have changed their decision, with a randomised framing
# variant ("deposit" vs "fee") and an optional payment-model preference
# question. Responses are internal-only (Django admin) and inform the
# October-December deposit tier amounts (ADR 0014). Deterministic,
# non-persisted variant derivation (survey_price_for / survey_framing_for)
# keeps the shown price/framing stable across page refreshes without a write
# on GET.

from __future__ import annotations

from django.db import models
from django.utils.translation import gettext_lazy as _

from core.models import BaseModel, BaseQuerySet
from matching.models import Registration

# The three hypothetical deposit price points (CHF) shown to survey
# respondents, mirroring the October-December ADR 0014 tier schedule.
SURVEY_PRICE_POINTS_CHF = (5, 10, 20)


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

    ``price_chf_shown`` and ``framing_shown`` are re-derived server-side from
    the registration's pk (see ``survey_price_for`` / ``survey_framing_for``
    below) at submission time — never trusted from client input — so the
    persisted row always reflects what the respondent actually saw.
    """

    class Framing(models.TextChoices):
        """The hypothetical-charge wording variant shown to the respondent."""

        DEPOSIT = "DEPOSIT", _("Deposit")
        FEE = "FEE", _("Fee")

    class Q1Answer(models.TextChoices):
        """Would the shown price have changed the respondent's decision to register?"""

        DEFINITELY = "DEFINITELY", _("Definitely would have registered anyway")
        PROBABLY = "PROBABLY", _("Probably would have registered anyway")
        PROBABLY_NOT = "PROBABLY_NOT", _("Probably would not have registered")
        DEFINITELY_NOT = "DEFINITELY_NOT", _("Definitely would not have registered")

    class Q2Answer(models.TextChoices):
        """Optional payment-model preference."""

        FEE_AT_REGISTRATION = "FEE_AT_REGISTRATION", _("Pay at registration")
        FEE_ON_MATCH_ONLY = "FEE_ON_MATCH_ONLY", _("Pay only once matched")
        WOULD_NOT_PAY = "WOULD_NOT_PAY", _("Would not pay either way")

    registration = models.OneToOneField(
        Registration,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="survey_response",
        help_text=(
            "The registration this response belongs to. SET_NULL so the "
            "research row survives account deletion."
        ),
    )
    price_chf_shown = models.PositiveSmallIntegerField(
        help_text="The hypothetical CHF price point shown to the respondent.",
    )
    framing_shown = models.CharField(
        max_length=16,
        choices=Framing.choices,
        help_text="The wording variant ('deposit' vs 'fee') shown to the respondent.",
    )
    q1_answer = models.CharField(
        max_length=16,
        choices=Q1Answer.choices,
        help_text="Would the shown price have changed the decision to register?",
    )
    q2_answer = models.CharField(
        max_length=32,
        choices=Q2Answer.choices,
        blank=True,
        default="",
        help_text="Optional payment-model preference.",
    )

    objects = SurveyResponseQuerySet.as_manager()

    class Meta:
        ordering = ["-created_at"]

    def to_string(self) -> str:
        """Return a human-readable label summarising the response."""
        return (
            f"Survey response · CHF {self.price_chf_shown} "
            f"{self.get_framing_shown_display()} · {self.get_q1_answer_display()}"
        )

    def __str__(self) -> str:
        """Delegate to to_string."""
        return self.to_string()


def survey_price_for(registration: Registration) -> int:
    """Return the deterministic hypothetical CHF price shown to ``registration``.

    Derived from the registration's pk, so the same respondent always sees
    the same price point across repeated GETs of ``register_done`` (no write,
    no session state needed to keep it stable).
    """
    index = registration.pk % len(SURVEY_PRICE_POINTS_CHF)
    return SURVEY_PRICE_POINTS_CHF[index]


def survey_framing_for(registration: Registration) -> SurveyResponse.Framing:
    """Return the deterministic framing variant shown to ``registration``.

    Alternates DEPOSIT/FEE across successive blocks of registration pks so
    both framings are reached roughly equally, while staying stable for a
    given respondent across repeated GETs.
    """
    if (registration.pk // len(SURVEY_PRICE_POINTS_CHF)) % 2 == 0:
        return SurveyResponse.Framing.DEPOSIT
    return SurveyResponse.Framing.FEE
