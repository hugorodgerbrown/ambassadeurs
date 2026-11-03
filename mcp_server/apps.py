"""App configuration for the staff MCP server."""

from django.apps import AppConfig


class McpServerConfig(AppConfig):
    """The staff-only MCP server: authentication is mcp_auth's, the tools are ours.

    Named ``mcp_server`` rather than ``mcp`` so it can never shadow the PyPI
    ``mcp`` package in the dev environment.
    """

    default_auto_field = "django.db.models.BigAutoField"
    name = "mcp_server"
