# Tests for core views.

import pytest
from django.test import Client
from django.urls import reverse


@pytest.mark.django_db
def test_healthz_returns_200_with_ok_body() -> None:
    """GET /healthz/ returns HTTP 200 with plain-text body 'ok'."""
    response = Client().get(reverse("healthz"))
    assert response.status_code == 200
    assert response.content == b"ok"
    assert response["Content-Type"].startswith("text/plain")


@pytest.mark.django_db
def test_healthz_rejects_post() -> None:
    """POST /healthz/ returns HTTP 405 (require_GET decorator)."""
    response = Client().post(reverse("healthz"))
    assert response.status_code == 405
