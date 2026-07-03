# Backend-divergence regression test (VERB-98).
#
# SQLite silently accepts `SELECT ... FOR UPDATE` chained onto a `DISTINCT`
# query, but Postgres rejects it. This exact combination was the root cause of
# VERB-97 (the matching engine's `_without_active_match()` queryset, since fixed
# on main). This test encodes the divergence itself — not the fixed query — so
# it stays green on both backends and would have failed loudly, on the Postgres
# CI lane, before VERB-97 was fixed.
#
# Note on structure: Postgres raises the `NotSupportedError` server-side, at
# statement execution, which aborts the surrounding transaction. `pytest.raises`
# must therefore wrap the whole `transaction.atomic()` block so the error
# propagates through `atomic().__exit__` — which issues `ROLLBACK TO SAVEPOINT`
# and recovers the connection — rather than being caught inside the block, which
# would leave the aborted transaction to fail on the trailing `RELEASE SAVEPOINT`.

import pytest
from django.db import NotSupportedError, connection, transaction

from matching.models import Registration
from tests.matching.factories import RegistrationFactory

pytestmark = pytest.mark.django_db


def test_select_for_update_with_distinct_diverges_by_backend() -> None:
    """`.select_for_update()` chained onto `.distinct()` diverges by backend.

    Postgres rejects `SELECT ... FOR UPDATE` combined with `DISTINCT`
    (`NotSupportedError`, raised server-side); SQLite silently ignores
    `FOR UPDATE` and never raises. Forcing evaluation inside a transaction
    reproduces the exact query shape that caused VERB-97.
    """
    registration = RegistrationFactory.create()

    if connection.vendor == "postgresql":
        # The error aborts the transaction, so the atomic() block lives INSIDE
        # pytest.raises: atomic() rolls back its savepoint as the error unwinds,
        # leaving the connection usable for the rest of the test session.
        with pytest.raises(NotSupportedError), transaction.atomic():
            list(Registration.objects.distinct().select_for_update())
    else:
        # SQLite silently ignores FOR UPDATE, so evaluation succeeds and returns
        # the registration we created — no error raised.
        with transaction.atomic():
            queryset = Registration.objects.distinct().select_for_update()
            assert registration in list(queryset)
