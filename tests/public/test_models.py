# Tests for public app models.

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
    """FormDownload rows are ordered newest-first by default."""
    first = FormDownloadFactory.create()
    second = FormDownloadFactory.create()
    rows = list(FormDownload.objects.all())
    # Both rows exist; the more recently created one is first.
    assert rows[0] == second
    assert rows[1] == first


def test_form_download_manager_is_custom_queryset() -> None:
    """The default manager produces FormDownloadQuerySet instances."""
    assert isinstance(FormDownload.objects.all(), FormDownloadQuerySet)
