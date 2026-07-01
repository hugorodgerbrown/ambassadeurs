# Tests for the run_matching management command (VERB-83).
#
# Mirrors the conventions in tests/matching/test_expire_matches.py: pytest +
# FactoryBoy, tz-aware datetimes, factories called with .create(). The command
# delegates to matching.services.run_matching (covered in test_services.py);
# these tests focus on the command surface: dry-run vs --commit and the
# non-zero exit on a partially failed batch.

from io import StringIO
from unittest.mock import patch

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase

from matching.models import Match, Registration
from tests.matching.factories import RegistrationFactory

pytestmark = pytest.mark.django_db


def _make_pair() -> None:
    """Create one eligible ambassador and one eligible referee."""
    RegistrationFactory.create(
        role=Registration.Role.AMBASSADOR,
        prior_pass=Registration.PriorPass.SEASONAL,
    )
    RegistrationFactory.create(referee=True)


def test_command_dry_run_by_default_creates_no_matches() -> None:
    """A bare invocation is read-only: it reports intent and writes nothing."""
    _make_pair()

    stdout = StringIO()
    call_command("run_matching", stdout=stdout)

    assert Match.objects.count() == 0
    output = stdout.getvalue()
    assert "would propose 1" in output
    assert "--commit" in output


def test_command_commit_creates_matches() -> None:
    """--commit proposes the matches for real."""
    _make_pair()

    stdout = StringIO()
    with TestCase.captureOnCommitCallbacks(execute=True):
        call_command("run_matching", "--commit", stdout=stdout)

    assert Match.objects.filter(status=Match.Status.PROPOSED).count() == 1
    output = stdout.getvalue()
    assert "Proposed 1" in output


def test_command_reports_zero_when_pool_empty() -> None:
    """With nothing to match, the dry-run reports zero and creates nothing."""
    stdout = StringIO()
    call_command("run_matching", stdout=stdout)

    assert Match.objects.count() == 0
    assert "would propose 0" in stdout.getvalue()


def test_command_exits_non_zero_on_partial_failure() -> None:
    """A failed proposal in the batch raises CommandError (non-zero exit)."""
    for _ in range(2):
        _make_pair()

    _call_count = {"n": 0}
    import matching.services as _svc

    _real = _svc.propose_match

    def _failing_propose(registration: Registration) -> Match | None:
        """Raise on the first invocation; delegate to the real function after."""
        _call_count["n"] += 1
        if _call_count["n"] == 1:
            raise RuntimeError("simulated proposal failure")
        return _real(registration)

    with patch.object(_svc, "propose_match", _failing_propose):
        with TestCase.captureOnCommitCallbacks(execute=True):
            with pytest.raises(CommandError, match="1 proposal"):
                call_command("run_matching", "--commit")

    # The non-failing pair was still matched despite the isolated failure.
    assert Match.objects.count() == 1


def test_command_respects_verbosity_zero_suppresses_summary() -> None:
    """--verbosity 0 suppresses the summary line."""
    _make_pair()

    stdout = StringIO()
    call_command("run_matching", verbosity=0, stdout=stdout)

    assert stdout.getvalue() == ""
