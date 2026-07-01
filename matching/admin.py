"""Admin registration for the matching app."""

import csv
from typing import Any

from django.contrib import admin
from django.http import HttpRequest, HttpResponse
from django.utils.translation import gettext_lazy as _

from .models import Match, Registration


@admin.action(description=_("Export selected cancelled matches as CSV"))
def export_cancelled_as_csv(
    model_admin: admin.ModelAdmin,
    request: HttpRequest,
    queryset: Any,
) -> HttpResponse:
    """Stream a CSV of the CANCELLED matches from the selected queryset.

    Filters the queryset to CANCELLED status before writing rows. Returns a
    header-only CSV when no selected matches are CANCELLED. Emails are read
    via select_related to avoid N+1 queries.
    """
    cancelled = queryset.filter(status=Match.Status.CANCELLED).select_related(
        "ambassador_registration__user",
        "referee_registration__user",
    )

    response = HttpResponse(content_type="text/csv")
    response["Content-Disposition"] = "attachment; filename=cancelled_matches.csv"

    writer = csv.writer(response)
    writer.writerow(
        [
            "match_id",
            "created_at",
            "no_show_reported_at",
            "no_show_reported_by",
            "ambassador_email",
            "referee_email",
        ]
    )

    for match in cancelled:
        writer.writerow(
            [
                match.pk,
                match.created_at,
                match.no_show_reported_at,
                match.no_show_reported_by,
                match.ambassador_registration.user.email,
                match.referee_registration.user.email,
            ]
        )

    return response


@admin.register(Registration)
class RegistrationAdmin(admin.ModelAdmin):
    """Admin for Registration."""

    list_display = [
        "user",
        "role",
        "prior_pass",
        "status",
        "priority",
        "fee_chf",
        "preferred_location",
        "nationality",
        "registration_country",
        "registration_region",
        "created_at",
    ]
    list_filter = [
        "role",
        "status",
        "prior_pass",
        "preferred_location",
        "nationality",
    ]
    search_fields = ["user__email", "user__first_name", "user__last_name"]
    raw_id_fields = ["user"]
    readonly_fields = [
        "accepted_terms",
        "terms_accepted_at",
        "fee_chf",
        "registration_country",
        "registration_region",
        "created_at",
        "updated_at",
    ]


@admin.register(Match)
class MatchAdmin(admin.ModelAdmin):
    """Admin for Match."""

    actions = [export_cancelled_as_csv]

    list_display = [
        "pk",
        "ambassador_registration",
        "referee_registration",
        "status",
        "no_show_reported_by",
        "no_show_reported_at",
        "expires_at",
        "created_at",
    ]
    list_filter = ["status"]
    search_fields = [
        "ambassador_registration__user__email",
        "referee_registration__user__email",
    ]
    raw_id_fields = ["ambassador_registration", "referee_registration"]
    readonly_fields = [
        "ambassador_accepted_at",
        "referee_accepted_at",
        "declined_by",
        "declined_at",
        "no_show_reported_by",
        "no_show_reported_at",
        "created_at",
        "updated_at",
    ]
