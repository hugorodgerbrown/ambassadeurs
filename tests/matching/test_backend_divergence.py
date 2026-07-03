# Backend-divergence regression test (VERB-98).
#
# SQLite silently accepts `SELECT ... FOR UPDATE` chained onto a `DISTINCT`
# query, but Postgres raises `NotSupportedError`. This exact combination was
# the root cause of VERB-97 (the matching engine's `_without_active_match()`
# queryset, since fixed on main). This test encodes the divergence itself —
# not the fixed query — so it stays green on both backends and would have
# failed loudly, on the Postgres CI lane, before VERB-97 was fixed.

import pytest
from django.db import NotSupportedError, connection, transaction

from matching.models import Registration
from tests.matching.factories import RegistrationFactory

pytestmark = pytest.mark.django_db


def test_select_for_update_with_distinct_diverges_by_backend() -> None:
    """`.select_for_update()` chained onto `.distinct()` diverges by backend.

    Postgres rejects `SELECT ... FOR UPDATE` combined with `DISTINCT`
    (`NotSupportedError`); SQLite silently ignores `FOR UPDATE` and never
    raises. Forcing evaluation inside a transaction reproduces the exact
    query shape that caused VERB-97.
    """
    RegistrationFactory.create()

    with transaction.atomic():
        queryset = Registration.objects.distinct().select_for_update()

        if connection.vendor == "postgresql":
            with pytest.raises(NotSupportedError):
                list(queryset)
        else:
            list(queryset)
