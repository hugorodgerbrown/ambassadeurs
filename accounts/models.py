# The Account profile model.
#
# Auth uses the default Django ``User`` (CLAUDE.md "Authentication"). Account
# holds the non-core attributes a participant needs and FKs 1:1 back to User.
# Admin-only users have no Account.

from django.conf import settings
from django.contrib.auth.models import User
from django.db import models

from core.models import BaseModel, BaseQuerySet


class AccountQuerySet(BaseQuerySet):
    """Queryset for Account."""

    def for_user(self, user: User) -> AccountQuerySet:
        """Return accounts belonging to ``user``."""
        return self.filter(user=user)


class Account(BaseModel):
    """Profile attributes for a participant, attached 1:1 to a Django User."""

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="account",
    )
    # Contact detail revealed to a matched partner only after mutual accept;
    # treat as sensitive (CLAUDE.md invariant 1).
    phone = models.CharField(max_length=32, blank=True)
    # Language codes are external identifiers (ISO 639) keyed to settings.LANGUAGES,
    # not a domain enum, so the UPPER_CASE TextChoices rule does not apply here.
    preferred_language = models.CharField(
        max_length=8,
        choices=settings.LANGUAGES,
        blank=True,
    )

    objects = AccountQuerySet.as_manager()

    class Meta:
        ordering = ["-created_at"]

    def to_string(self) -> str:
        """Return a human-readable label for the account."""
        return f"Account for {self.user}"
