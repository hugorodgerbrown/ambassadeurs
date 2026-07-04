"""Admin registration for the public app."""

from typing import Any

from django.contrib import admin
from django.http import HttpRequest

from .models import FormDownload, SurveyResponse


@admin.register(FormDownload)
class FormDownloadAdmin(admin.ModelAdmin):
    """Admin for FormDownload.

    Browsable by day via date_hierarchy so programme staff can see the
    download-rate trend over the registration period.
    """

    list_display = ["__str__", "created_at"]
    readonly_fields = ["created_at", "updated_at"]
    date_hierarchy = "created_at"


@admin.register(SurveyResponse)
class SurveyResponseAdmin(admin.ModelAdmin):
    """Admin for SurveyResponse.

    Read-only, modelled on billing.admin.PaymentAdmin — responses are
    research data informing the October-December deposit tiers (ADR 0014)
    and must never be hand-edited or hand-created in the admin.
    """

    list_display = [
        "pk",
        "registration",
        "max_deposit",
        "created_at",
    ]
    list_filter = ["max_deposit"]
    raw_id_fields = ["registration"]

    def has_add_permission(self, request: HttpRequest) -> bool:
        """Disallow creating SurveyResponse rows by hand.

        A response must correspond to a real submission from register_done;
        an admin-created row would misrepresent the research data.
        """
        return False

    def has_change_permission(self, request: HttpRequest, obj: Any = None) -> bool:
        """Disallow editing SurveyResponse rows (mirrors StateTransitionLogAdmin).

        Responses are research data — even a superuser must not be able to
        alter what a respondent actually submitted.
        """
        return False

    def has_delete_permission(self, request: HttpRequest, obj: Any = None) -> bool:
        """Disallow deleting SurveyResponse rows — responses are research data."""
        return False

    readonly_fields = [
        "registration",
        "max_deposit",
        "created_at",
        "updated_at",
    ]
