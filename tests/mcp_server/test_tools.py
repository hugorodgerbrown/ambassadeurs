# Tests for the MCP tools.

from typing import Any

import pytest

from matching.models import Match, Registration
from mcp_server.tools import MAX_MATCHES, ToolError, queue_position
from tests.accounts.factories import UserFactory
from tests.matching.factories import MatchFactory, RegistrationFactory

pytestmark = pytest.mark.django_db


def _person(first: str, last: str, **kwargs: Any) -> Registration:
    """A registration whose user has the given name."""
    return RegistrationFactory.create(
        user__first_name=first,
        user__last_name=last,
        user__email=f"{first}.{last}@example.com".lower(),
        **kwargs,
    )


def test_finds_by_full_name_and_reports_the_admin_standing() -> None:
    """A full name finds the participant, with the number the admin shows."""
    _person("Luca", "Bianchi", priority=1)
    _person("Camille", "Rey")

    result = queue_position(UserFactory.create(), {"query": "camille rey"})

    [found] = result["participants"]
    assert found["name"] == "Camille Rey"
    assert found["email"] == "camille.rey@example.com"
    assert found["role"] == Registration.Role.AMBASSADOR
    assert found["queue_position"] == 2
    assert found["waiting_in_queue"] == 2
    assert found["summary"] == "Number 2 of 2 in the ambassador queue."
    assert found["active_match"] is None


def test_finds_by_email_case_insensitively() -> None:
    """An email address is normalised and matched exactly."""
    _person("Noah", "Fischer")
    _person("Noah", "Fischerman")

    result = queue_position(
        UserFactory.create(), {"query": " Noah.Fischer@Example.com "}
    )

    assert [p["name"] for p in result["participants"]] == ["Noah Fischer"]


def test_a_matched_participant_carries_the_match() -> None:
    """Someone out of the queue because of a match says which one."""
    match = MatchFactory.create(
        status=Match.Status.PENDING,
        ambassador_registration__user__first_name="Jonas",
    )

    [found] = queue_position(UserFactory.create(), {"query": "jonas"})["participants"]

    assert found["queue_position"] is None
    assert found["active_match"] == {"id": match.pk, "status": Match.Status.PENDING}


def test_no_match_is_an_empty_list() -> None:
    """A query nobody matches answers with no participants, not an error."""
    assert (
        queue_position(UserFactory.create(), {"query": "nobody"})["participants"] == []
    )


@pytest.mark.parametrize("args", [{}, {"query": ""}, {"query": "  "}, {"query": 3}])
def test_query_is_required(args: dict[str, Any]) -> None:
    """A missing, blank or non-string query is a ToolError."""
    with pytest.raises(ToolError):
        queue_position(UserFactory.create(), args)


def test_too_many_matches_asks_for_a_narrower_query() -> None:
    """Past the cap the model is told to narrow the query, not given a partial list."""
    for i in range(MAX_MATCHES + 1):
        _person("Sam", f"Smith{i}")
    with pytest.raises(ToolError, match="More than"):
        queue_position(UserFactory.create(), {"query": "sam"})
