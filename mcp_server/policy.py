"""Who may connect an MCP client, and how the consent page names them.

``MCP_AUTH["CAN_CONNECT"]`` points at ``staff_only``: mcp_auth runs it on the
consent page and on every MCP call, so taking staff status away cuts a user's
connected clients off at once. ``MCP_AUTH["ACCOUNT_NAME"]`` points at
``account_name``, the masked address the magic-link confirm page already uses.
"""

from typing import Any

from core.emails import mask_email


def staff_only(user: Any) -> bool:
    """An active staff member may connect; nobody else may."""
    return bool(user.is_active and user.is_staff)


def account_name(user: Any) -> str:
    """Name the signed-in account by its masked email address, or its username."""
    email = getattr(user, "email", "")
    return mask_email(email) if email else str(user.get_username())
