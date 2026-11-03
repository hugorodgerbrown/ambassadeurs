"""The MCP tools. Each is a function of (user, arguments) returning JSON-able data.

Every tool is read-only and staff-only (the endpoint's connect rule, see
``mcp_server.policy``). Add a tool by writing a function and listing it in
``TOOLS`` with its JSON Schema. Raise ``ToolError`` for a mistake the model
should see and correct.

``queue_position`` answers "where is <name> in the queue?" with exactly what
the Registration admin shows, because both read
``matching.selectors.queue_standing``.
"""

from collections.abc import Callable
from dataclasses import dataclass
from functools import reduce
from operator import and_
from typing import Any

from django.db.models import Q

from core.emails import normalise_email
from matching.models import Registration
from matching.selectors import queue_standing

# More matches than this and the model should ask for a narrower query.
MAX_MATCHES = 10


class ToolError(Exception):
    """A problem with the call that the model can fix and retry."""


@dataclass(frozen=True)
class Tool:
    """One MCP tool: its name, description, input schema and implementation."""

    name: str
    description: str
    input_schema: dict[str, Any]
    func: Callable[[Any, dict[str, Any]], dict[str, Any]]
    read_only: bool = True

    def describe(self) -> dict[str, Any]:
        """Return the tool as ``tools/list`` lists it."""
        return {
            "name": self.name,
            "description": self.description,
            "inputSchema": self.input_schema,
            "annotations": {"readOnlyHint": self.read_only},
        }


def _matching_registrations(query: str) -> Q:
    """Match an email address, or every word of a name against first/last name."""
    if "@" in query:
        return Q(user__email=normalise_email(query))
    words = query.split()
    per_word = [
        Q(user__first_name__icontains=w)
        | Q(user__last_name__icontains=w)
        | Q(user__email__icontains=w)
        for w in words
    ]
    return reduce(and_, per_word)


def queue_position(user: Any, args: dict[str, Any]) -> dict[str, Any]:
    """Return where each participant matching ``query`` stands in their queue."""
    query = args.get("query")
    if not isinstance(query, str) or not query.strip():
        raise ToolError("query must be a participant's name or email address.")
    registrations = list(
        Registration.objects.filter(_matching_registrations(query.strip()))
        .select_related("user")
        .order_by("user__last_name", "user__first_name", "pk")[: MAX_MATCHES + 1]
    )
    if len(registrations) > MAX_MATCHES:
        raise ToolError(
            f"More than {MAX_MATCHES} participants match {query!r}; "
            "use a full name or an email address."
        )
    participants = []
    for registration in registrations:
        standing = queue_standing(registration)
        match = standing["active_match"]
        participants.append(
            {
                "name": registration.user.get_full_name(),
                "email": registration.user.email,
                "role": registration.role,
                "status": registration.status,
                "queue_position": standing["position"],
                "waiting_in_queue": standing["waiting"],
                "summary": standing["summary"],
                "active_match": None
                if match is None
                else {"id": match.pk, "status": match.status},
                "registered_at": registration.created_at.isoformat(),
            }
        )
    return {"query": query, "participants": participants}


TOOLS: dict[str, Tool] = {
    t.name: t
    for t in [
        Tool(
            name="queue_position",
            description=(
                "Look up where a participant stands in the matching queue, by name "
                "or email address. Returns each matching participant's role, "
                "status, queue position (the same number they see on their "
                "account page; ambassadors and referees queue separately), how "
                "many are waiting in that queue, and, when they are not waiting, "
                "why (paused, in a match, or ineligible)."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "A full or partial name, or an email address.",
                        "minLength": 1,
                        "maxLength": 200,
                    }
                },
                "required": ["query"],
            },
            func=queue_position,
        ),
    ]
}
