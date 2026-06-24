# Public-app models.
#
# Currently holds FormDownload — a lightweight record of each application-form
# PDF download. No PII is stored: one row per download with only the timestamp
# from BaseModel. The count and date histogram are the conversion metric for the
# programme (there is no analytics stack in the project).

from __future__ import annotations

from core.models import BaseModel, BaseQuerySet


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
