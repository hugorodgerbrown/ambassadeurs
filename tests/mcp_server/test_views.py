# Tests for the staff MCP endpoint: the shared mcp_auth contract, then JSON-RPC.

import json
from io import StringIO
from typing import Any

import pytest
from django.contrib.auth.models import User
from django.core.management import call_command
from django.test import Client
from mcp_auth.testing import MCPAuthContract

from mcp_server import views
from mcp_server.tools import TOOLS
from tests.accounts.factories import UserFactory


class TestMCPAuth(MCPAuthContract):
    """The OAuth contract every Titan MCP server passes, held to staff only."""

    mcp_path = "/mcp"

    def make_allowed_user(self, django_user_model: Any) -> Any:
        """An active staff member may connect."""
        return UserFactory.create(username="mcp_staff", is_staff=True)

    def make_refused_user(self, django_user_model: Any) -> Any:
        """An active participant without staff status may not."""
        return UserFactory.create(username="mcp_participant")


pytestmark = pytest.mark.django_db


@pytest.fixture
def staff() -> User:
    """An active staff user to call the tools as."""
    return UserFactory.create(username="rpc_staff", is_staff=True)


def rpc(user: User, method: str, params: Any = None) -> dict[str, Any]:
    """Dispatch one JSON-RPC method as ``user``, bypassing OAuth (see the contract)."""
    return views._dispatch(user, method, {} if params is None else params)


def test_initialize_names_the_server(staff: User) -> None:
    """initialize answers the requested version and names the server."""
    result = rpc(staff, "initialize", {"protocolVersion": "2025-06-18"})
    assert result["protocolVersion"] == "2025-06-18"
    assert result["serverInfo"]["name"] == "ski-parrainage"
    assert "queue_position" in result["instructions"]


def test_initialize_falls_back_to_the_newest_version(staff: User) -> None:
    """An unknown requested version is answered with the newest supported one."""
    result = rpc(staff, "initialize", {"protocolVersion": "1999-01-01"})
    assert result["protocolVersion"] == views.SUPPORTED_VERSIONS[0]


def test_ping_and_tools_list(staff: User) -> None:
    """ping answers empty; tools/list lists every tool, read-only."""
    assert rpc(staff, "ping") == {}
    tools = rpc(staff, "tools/list")["tools"]
    assert {t["name"] for t in tools} == set(TOOLS)
    assert all(t["annotations"]["readOnlyHint"] for t in tools)


def test_unknown_method_is_an_rpc_error(staff: User) -> None:
    """A method the server doesn't have is METHOD_NOT_FOUND."""
    with pytest.raises(views.RpcError) as exc:
        rpc(staff, "resources/list")
    assert exc.value.code == views.METHOD_NOT_FOUND


@pytest.mark.parametrize(
    ("method", "params"),
    [
        ("ping", []),
        ("tools/call", {"name": "nope"}),
        ("tools/call", {"name": "queue_position", "arguments": ["x"]}),
    ],
)
def test_bad_params_are_invalid_params(staff: User, method: str, params: Any) -> None:
    """Non-object params, an unknown tool or non-object arguments are refused."""
    with pytest.raises(views.RpcError) as exc:
        rpc(staff, method, params)
    assert exc.value.code == views.INVALID_PARAMS


def test_tool_call_returns_text_and_structured_content(staff: User) -> None:
    """A tool's result comes back as JSON text and as structuredContent."""
    result = rpc(
        staff, "tools/call", {"name": "queue_position", "arguments": {"query": "x"}}
    )
    assert result["structuredContent"] == {"query": "x", "participants": []}
    assert json.loads(result["content"][0]["text"]) == result["structuredContent"]


def test_tool_error_is_an_is_error_result(staff: User) -> None:
    """A ToolError reaches the model as an isError result it can correct."""
    result = rpc(staff, "tools/call", {"name": "queue_position"})
    assert result["isError"] is True
    assert "query must be" in result["content"][0]["text"]


# ---------------------------------------------------------------------------
# The HTTP layer, with a real bearer token
# ---------------------------------------------------------------------------


@pytest.fixture
def bearer(staff: User) -> dict[str, str]:
    """Headers carrying a valid MCP access token for ``staff``."""
    out = StringIO()
    call_command(
        "mint_mcp_token",
        "--commit",
        f"--username={staff.username}",
        "--resource=http://testserver/mcp",
        stdout=out,
        verbosity=0,
    )
    return {"HTTP_AUTHORIZATION": f"Bearer {out.getvalue().strip()}"}


def post(client: Client, body: Any, **headers: str) -> Any:
    """POST a raw JSON body to /mcp."""
    data = body if isinstance(body, str) else json.dumps(body)
    return client.post("/mcp", data, content_type="application/json", **headers)


def test_http_round_trip(client: Client, bearer: dict[str, str]) -> None:
    """A ping over HTTP with a staff token answers its id."""
    response = post(client, {"jsonrpc": "2.0", "id": 7, "method": "ping"}, **bearer)
    assert response.status_code == 200
    assert response.json() == {"jsonrpc": "2.0", "id": 7, "result": {}}


@pytest.mark.parametrize(
    ("body", "code"),
    [
        ("{not json", views.PARSE_ERROR),
        ([{"jsonrpc": "2.0"}], views.INVALID_REQUEST),
    ],
)
def test_http_malformed_bodies(
    client: Client, bearer: dict[str, str], body: Any, code: int
) -> None:
    """Unparseable or non-2.0 bodies are a 400 JSON-RPC error with no id."""
    response = post(client, body, **bearer)
    assert response.status_code == 400
    assert response.json()["error"]["code"] == code


def test_http_unsupported_protocol_version(
    client: Client, bearer: dict[str, str]
) -> None:
    """An MCP-Protocol-Version the server doesn't speak is refused."""
    response = post(
        client,
        {"jsonrpc": "2.0", "id": 1, "method": "ping"},
        HTTP_MCP_PROTOCOL_VERSION="1999-01-01",
        **bearer,
    )
    assert response.status_code == 400


def test_http_notification_is_accepted(client: Client, bearer: dict[str, str]) -> None:
    """A notification (no id) is acknowledged with 202 and no body."""
    response = post(
        client, {"jsonrpc": "2.0", "method": "notifications/initialized"}, **bearer
    )
    assert response.status_code == 202


def test_http_method_not_found_keeps_the_id(
    client: Client, bearer: dict[str, str]
) -> None:
    """An error for a request with an id answers 200 with that id."""
    response = post(client, {"jsonrpc": "2.0", "id": 3, "method": "nope"}, **bearer)
    assert response.status_code == 200
    assert response.json()["id"] == 3
