"""Admin registration for the matching app.

The Registration admin shows each participant's queue place — the number they
read on their own account page — as a sortable changelist column (one
correlated subquery, ``RegistrationQuerySet.with_queue_position``) and, on the
change form, as a sentence that also says why someone is not queuing
(``matching.selectors.queue_standing``).
"""

import csv
from typing import Any, cast

from django.contrib import admin
from django.core.exceptions import PermissionDenied
from django.db.models import QuerySet
from django.http import HttpRequest, HttpResponse, HttpResponseRedirect
from django.shortcuts import get_object_or_404
from django.urls import URLPattern, path, reverse
from django.utils.html import format_html
from django.utils.translation import gettext_lazy as _

from core.impersonation import impersonate_handoff_url

from . import selectors
from .models import Match, Registration, RegistrationQuerySet


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
        "queue_position",
        "priority",
        "fee_chf",
        "preferred_location",
        "nationality",
        "registration_country",
        "registration_region",
        "created_at",
        "view_as",
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
        "queue_standing",
        "accepted_terms",
        "terms_accepted_at",
        "fee_chf",
        "registration_country",
        "registration_region",
        "created_at",
        "updated_at",
    ]

    def get_fields(self, request: HttpRequest, obj: Registration | None = None) -> Any:
        """Lead the change form with the queue position — the question asked most."""
        fields = [f for f in super().get_fields(request, obj) if f != "queue_standing"]
        return fields if obj is None else ["queue_standing", *fields]

    def get_queryset(self, request: HttpRequest) -> QuerySet[Registration]:
        """Annotate every row with its queue place for the changelist column."""
        queryset = cast(RegistrationQuerySet, super().get_queryset(request))
        return queryset.with_queue_position()

    @admin.display(description=_("Queue"), ordering="queue_position")
    def queue_position(self, obj: Registration) -> str:
        """The participant's place in their queue, as their account page shows it.

        Blank (the admin's empty value) when they are not waiting — open the
        registration to see why.
        """
        position = getattr(obj, "queue_position", None)
        return "" if position is None else f"#{position}"

    @admin.display(description=_("Queue position"))
    def queue_standing(self, obj: Registration) -> str:
        """Where the participant stands, or why they are not queuing.

        Links the active match when that is what keeps them out of the queue.
        """
        if obj.pk is None:
            return ""
        standing = selectors.queue_standing(obj)
        match = standing["active_match"]
        if match is None:
            return standing["summary"]
        return format_html(
            '{} <a href="{}">{}</a>',
            standing["summary"],
            reverse("admin:matching_match_change", args=[match.pk]),
            _("View match #%(pk)s") % {"pk": match.pk},
        )

    def get_urls(self) -> list[URLPattern]:
        """Add the "View as" hand-off route ahead of the default admin URLs."""
        return [
            path(
                "<path:object_id>/view-as/",
                self.admin_site.admin_view(self.view_as_redirect),
                name="matching_registration_view_as",
            ),
            *super().get_urls(),
        ]

    def view_as_redirect(self, request: HttpRequest, object_id: str) -> HttpResponse:
        """Hand the superuser to the public site, impersonating this participant.

        Mints the signed hand-off token at click time (so its 60-second expiry
        starts now, not when the changelist rendered) and redirects to the
        public hand-off view, which signs the superuser in on the public host
        before starting the impersonation (ADR 0028).
        """
        if not request.user.is_superuser:
            raise PermissionDenied
        registration = get_object_or_404(Registration, pk=object_id)
        return HttpResponseRedirect(
            impersonate_handoff_url(request.user, registration.user_id)
        )

    @admin.display(description=_("View as"))
    def view_as(self, obj: Registration) -> str:
        """Link that starts a read-only impersonation of this participant.

        Opens in a new tab: on a split-host deployment the link leaves the
        admin host for the public site (see ``core.impersonation``).
        """
        return format_html(
            '<a href="{}" target="_blank" rel="noopener">{}</a>',
            reverse("admin:matching_registration_view_as", args=[obj.pk]),
            _("View as"),
        )


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
