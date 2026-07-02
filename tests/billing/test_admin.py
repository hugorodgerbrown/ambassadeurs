# Tests for billing admin classes.

import pytest
from django.contrib.auth.models import User
from django.test import Client
from django.urls import reverse

from billing.admin import PaymentAdmin
from tests.accounts.factories import UserFactory
from tests.billing.factories import PaymentFactory

pytestmark = pytest.mark.django_db


def make_staff_user() -> User:
    """Create and return a superuser for admin access in tests."""
    user = UserFactory.create(
        username="billing_staff_admin",
        is_staff=True,
        is_superuser=True,
    )
    user.set_password("password")
    user.save()
    return user


def test_payment_changelist_returns_200(client: Client) -> None:
    """GET the Payment changelist as a staff user returns HTTP 200."""
    PaymentFactory.create()
    staff = make_staff_user()
    client.force_login(staff)
    url = reverse("admin:billing_payment_changelist")
    response = client.get(url)
    assert response.status_code == 200


def test_stripe_ids_and_status_are_readonly() -> None:
    """The Stripe identifier fields, amount, and status/reason are readonly."""
    readonly = PaymentAdmin.readonly_fields
    for field in (
        "stripe_customer_id",
        "stripe_payment_intent_id",
        "stripe_refund_id",
        "amount_chf",
        "status",
        "reason",
    ):
        assert field in readonly
