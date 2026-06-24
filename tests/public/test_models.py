# Tests for public app models.

import datetime

import pytest

from public.models import FormDownload, FormDownloadQuerySet
from tests.public.factories import FormDownloadFactory

pytestmark = pytest.mark.django_db


def test_form_download_to_string_format() -> None:
    """FormDownload.to_string returns the expected date-prefixed label."""
    fd = FormDownloadFactory.create()
    s = str(fd)
    assert s.startswith("Form download · ")
    # The date portion must be present and non-empty.
    assert fd.created_at.strftime("%Y-%m-%d") in s


def test_form_download_default_ordering_newest_first() -> None:
    """FormDownload rows are ordered newest-first by default.

    Use queryset .update() to assign explicit distinct timestamps, bypassing
    auto_now_add — the two .create() calls can otherwise land in the same
    microsecond under SQLite, making the ordering non-deterministic.
    """
    first = FormDownloadFactory.create()
    second = FormDownloadFactory.create()
    # Assign well-separated tz-aware timestamps so ordering is deterministic.
    FormDownload.objects.filter(pk=first.pk).update(
        created_at=datetime.datetime(2024, 1, 1, 12, 0, 0, tzinfo=datetime.UTC)
    )
    FormDownload.objects.filter(pk=second.pk).update(
        created_at=datetime.datetime(2024, 1, 1, 13, 0, 0, tzinfo=datetime.UTC)
    )
    first.refresh_from_db()
    second.refresh_from_db()
    rows = list(FormDownload.objects.all())
    # The more recently created row (second, 13:00) must come first.
    assert rows[0] == second
    assert rows[1] == first


def test_form_download_manager_is_custom_queryset() -> None:
    """The default manager produces FormDownloadQuerySet instances."""
    assert isinstance(FormDownload.objects.all(), FormDownloadQuerySet)
