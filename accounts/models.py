# Accounts app models.
#
# Auth uses the default Django ``User`` (CLAUDE.md "Authentication"). The Account
# model has been removed — participant attributes (phone, preferred_language)
# now live directly on ``matching.Registration`` (OneToOneField to User).
# Admin-only users have a User but no Registration.
#
# ``Passkey`` (ADR 0030) is a WebAuthn credential a signed-in user adds from
# the account page; from then on it signs them in with no email to wait for.
# The verification itself lives in ``accounts.passkeys``.

from __future__ import annotations

from typing import TYPE_CHECKING

from django.conf import settings
from django.db import models
from django.utils.translation import gettext_lazy as _

from core.models import BaseModel, BaseQuerySet

if TYPE_CHECKING:
    from django.contrib.auth.models import AbstractBaseUser


class PasskeyQuerySet(BaseQuerySet):
    """Queryset for Passkey."""

    def for_user(self, user: AbstractBaseUser) -> PasskeyQuerySet:
        """Return the passkeys ``user`` registered."""
        return self.filter(user=user)


class Passkey(BaseModel):
    """A WebAuthn credential a user added from the account page.

    ``credential_id`` (base64url) is how the browser names it at sign-in;
    ``public_key`` (COSE) verifies the signature. ``sign_count`` going
    backwards would mean a cloned authenticator; synced passkeys report 0.
    """

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="passkeys"
    )
    credential_id = models.CharField(max_length=1024, unique=True)
    public_key = models.BinaryField()
    sign_count = models.PositiveBigIntegerField(default=0)
    name = models.CharField(max_length=100)
    backed_up = models.BooleanField(
        default=False,
        help_text=_("Synced to the user's other devices (iCloud, Google…)."),
    )
    last_used_at = models.DateTimeField(null=True, blank=True)

    objects = PasskeyQuerySet.as_manager()

    class Meta:
        ordering = ["-created_at"]

    def to_string(self) -> str:
        """Return the passkey's name."""
        return self.name
