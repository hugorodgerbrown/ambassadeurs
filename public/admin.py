"""Admin registration for the public app."""

from django.contrib import admin

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
        "price_chf_shown",
        "framing_shown",
        "q1_answer",
        "q2_answer",
        "created_at",
    ]
    list_filter = ["price_chf_shown", "framing_shown", "q1_answer", "q2_answer"]
    raw_id_fields = ["registration"]

    def has_add_permission(self, request: object) -> bool:
        """Disallow creating SurveyResponse rows by hand.

        A response must correspond to a real submission from register_done;
        an admin-created row would misrepresent the research data.
        """
        return False

    readonly_fields = [
        "registration",
        "price_chf_shown",
        "framing_shown",
        "q1_answer",
        "q2_answer",
        "created_at",
        "updated_at",
    ]
