"""The staff MCP endpoint: JSON-RPC over the Streamable HTTP transport.

Ported from the Titan template's MCP app (hugorodgerbrown/django-titan-template),
without its MCP Apps resources and fivebar events. Stateless and synchronous:
every request is a POST answered with one JSON body, so it runs under
gunicorn's sync workers like the rest of the site. There is no
server-to-client stream; GET answers 405 as the spec allows.

Authentication is mcp_auth's ``@mcp_endpoint``: a bearer token bound to this
URL whose user passes ``MCP_AUTH["CAN_CONNECT"]`` (staff only), or a 401 that
starts OAuth discovery.
"""

import json
import logging
from typing import Any

from django.http import HttpRequest, HttpResponse, JsonResponse
from mcp_auth.resource import mcp_endpoint

from .tools import TOOLS, ToolError

logger = logging.getLogger(__name__)

SUPPORTED_VERSIONS = ("2025-11-25", "2025-06-18", "2025-03-26")
SERVER_INFO = {"name": "ski-parrainage", "title": "Ski Parrainage", "version": "1.0.0"}
INSTRUCTIONS = (
    "Ski Parrainage pairs returning 4 Vallées pass holders (ambassadors) with new "
    "ones (referees). These tools are for its staff: use queue_position to find "
    "where a named participant stands in the matching queue."
)

PARSE_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602


class RpcError(Exception):
    """A JSON-RPC error with its code."""

    def __init__(self, code: int, message: str) -> None:
        """Keep the JSON-RPC error code alongside the message."""
        super().__init__(message)
        self.code = code
        self.message = message


@mcp_endpoint
def mcp(request: HttpRequest) -> HttpResponse:
    """Answer one JSON-RPC message as the token's user."""
    version = request.headers.get("MCP-Protocol-Version")
    if version and version not in SUPPORTED_VERSIONS:
        return _error_response(
            None, INVALID_REQUEST, f"Unsupported protocol version {version}"
        )

    try:
        message = json.loads(request.body)
    except ValueError:
        return _error_response(None, PARSE_ERROR, "Parse error")
    if not isinstance(message, dict) or message.get("jsonrpc") != "2.0":
        return _error_response(
            None, INVALID_REQUEST, "Expected one JSON-RPC 2.0 message"
        )

    # Notifications and responses from the client need no answer.
    if "method" not in message or "id" not in message:
        return HttpResponse(status=202)

    msg_id = message["id"]
    try:
        result = _dispatch(request.user, message["method"], message.get("params") or {})
    except RpcError as exc:
        return _error_response(msg_id, exc.code, exc.message)
    return JsonResponse({"jsonrpc": "2.0", "id": msg_id, "result": result})


def _error_response(msg_id: Any, code: int, message: str) -> JsonResponse:
    """Return a JSON-RPC error; HTTP 400 when there is no id to answer."""
    status = 400 if msg_id is None else 200
    return JsonResponse(
        {"jsonrpc": "2.0", "id": msg_id, "error": {"code": code, "message": message}},
        status=status,
    )


def _dispatch(user: Any, method: str, params: Any) -> dict[str, Any]:
    """Route a JSON-RPC method to its handler."""
    if not isinstance(params, dict):
        raise RpcError(INVALID_PARAMS, "params must be an object")
    match method:
        case "initialize":
            requested = params.get("protocolVersion")
            return {
                "protocolVersion": requested
                if requested in SUPPORTED_VERSIONS
                else SUPPORTED_VERSIONS[0],
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": SERVER_INFO,
                "instructions": INSTRUCTIONS,
            }
        case "ping":
            return {}
        case "tools/list":
            return {"tools": [t.describe() for t in TOOLS.values()]}
        case "tools/call":
            return _call_tool(user, params)
        case _:
            raise RpcError(METHOD_NOT_FOUND, f"Method not found: {method}")


def _call_tool(user: Any, params: dict[str, Any]) -> dict[str, Any]:
    """Run one tool; a ToolError becomes an isError result the model can read.

    Each call is logged with the tool and the staff user, as the audit trail
    of who looked up participants.
    """
    name = params.get("name")
    tool = TOOLS.get(name) if isinstance(name, str) else None
    if tool is None:
        raise RpcError(INVALID_PARAMS, f"Unknown tool: {name}")
    # Absent or null is no arguments; anything else must be an object.
    args = params.get("arguments")
    if args is None:
        args = {}
    if not isinstance(args, dict):
        raise RpcError(INVALID_PARAMS, "arguments must be an object")
    logger.info("mcp.tool name=%s user=%s", tool.name, user.pk)
    try:
        result = tool.func(user, args)
    except ToolError as exc:
        return {"content": [{"type": "text", "text": str(exc)}], "isError": True}
    text = json.dumps(result, ensure_ascii=False)
    return {"content": [{"type": "text", "text": text}], "structuredContent": result}
