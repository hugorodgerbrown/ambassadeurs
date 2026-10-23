"""Admin registration for the accounts app.

Account has been removed. Participant attributes live on matching.Registration.
The one model here is ``Passkey`` (ADR 0030): staff can see and remove a
user's passkeys, but never add one — a passkey is only ever made by the
user's own device, from the account page.
"""

from django.contrib import admin
from django.http import HttpRequest

from .models import Passkey


@admin.register(Passkey)
class PasskeyAdmin(admin.ModelAdmin):
    """Read-only list of passkeys; staff may delete one, not add or edit."""

    list_display = ["name", "user", "backed_up", "created_at", "last_used_at"]
    list_filter = ["backed_up"]
    search_fields = ["user__email", "name"]
    list_select_related = ["user"]
    readonly_fields = [
        "user",
        "name",
        "credential_id",
        "sign_count",
        "backed_up",
        "last_used_at",
        "created_at",
        "updated_at",
    ]
    exclude = ["public_key"]

    def has_add_permission(self, request: HttpRequest) -> bool:
        """Passkeys are made by the user's device, never in the admin."""
        return False

    def has_change_permission(
        self, request: HttpRequest, obj: Passkey | None = None
    ) -> bool:
        """Every field is the authenticator's; nothing here is editable."""
        return False
