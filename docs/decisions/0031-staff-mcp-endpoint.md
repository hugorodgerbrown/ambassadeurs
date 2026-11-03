# ADR 0031 — A staff-only MCP endpoint, ported from the Titan template

**Status:** Accepted
**Date:** 2026-11-03
**Ticket:** SKI-181 (follow-up to SKI-180)

---

## Context

Participants ask staff where they are in the queue. They can see it on their
account page; staff could only find out by impersonating them. SKI-180 added the
number to the Registration admin, and Hugo also wants to ask for it from
Claude, over MCP.

The site had no MCP server ([ADR 0024](0024-llm-visibility-content-signal-and-llms-txt.md)
noted that as deferred). The Titan template (`hugorodgerbrown/django-titan-template`)
ships an optional MCP app authenticated by `mcp-auth`, Hugo's shared OAuth 2.1
package on top of django-oauth-toolkit, with a `staff_only` connect policy.

## Decision

1. **Port Titan's MCP app rather than design a new one.** `mcp_server/` is
   Titan's `apps/mcp` without its MCP Apps UI resources and fivebar events:
   a stateless JSON-RPC view at `/mcp` (`initialize`, `ping`, `tools/list`,
   `tools/call`), wrapped in `mcp_auth.resource.mcp_endpoint`. It is named
   `mcp_server` so it can't shadow the PyPI `mcp` package in the dev env.
2. **Staff only.** `MCP_AUTH["CAN_CONNECT"] = "mcp_server.policy.staff_only"`
   (active and `is_staff`). mcp_auth runs it on the consent page *and* on every
   call, so unticking someone's staff flag cuts their connected clients off at
   once. Nothing participant-facing is exposed.
3. **Read-only tools on existing selectors.** The only tool, `queue_position`,
   takes a name or email and returns `matching.selectors.queue_standing` for
   each match — the same function the admin's change form reads, which reads
   the same queue place as the participant's account page. A query matching
   more than ten registrations is refused, so the tool can't be used to list
   the pool.
4. **`CONSENT_RETURN = "page"`.** mcp_auth's consent view widens CSP
   `form-action` through Django's built-in CSP, but this site's policy is
   django-csp-plus ([ADR 0027](0027-runtime-editable-csp.md)), which that
   override can't touch. Rather than add `claude.ai`, `claude.com` and
   loopback ports to the site-wide `form-action`, Allow/Cancel answer with
   mcp_auth's `returning.html` (a meta refresh plus a link), which
   `form-action` does not govern.
5. **Sign-in returns to the consent page.** `@login_required` sends a
   signed-out user to `accounts:login?next=…`. `login_request` now keeps a
   safe (same-host) `next` in the session and both the magic link and the
   passkey sign-in land there. It is not put in the login token, which stays
   `{user_pk}` only (Invariant 6); a link opened in another browser falls back
   to the account page, and the user starts the connection again.
6. **Routes are unprefixed.** `/mcp`, `/oauth/…` and `/.well-known/oauth-*`
   are machine-facing and registered with clients, so they live outside
   `i18n_patterns` in `config/urls_public.py` ([ADR 0025](0025-language-prefixed-urls.md)).
   The consent page's copy is wrapped for translation; it renders in English.

## Consequences

- To connect: in Claude, add a custom connector with the URL
  `https://skiparrainage.com/mcp`, sign in with a staff account, and Connect.
  Connected apps are listed (and can be disconnected) at `/oauth/connected/`.
- mcp-auth is a git dependency pinned to a tag in `[tool.uv.sources]` and
  named in full in `tox.ini`; `pip-audit` skips it (it can't look up a git
  package) but still audits django-oauth-toolkit and the rest.
- New tables from `oauth2_provider` (applications, grants, tokens; only hashes
  of tokens are stored).
- Rate limits (mcp_auth's per-IP and per-user) use the default LocMemCache, so
  they are per worker — the same caveat as django-ratelimit.
- Every tool call is logged (`mcp.tool name=… user=…`), the audit trail of
  which staff member looked participants up.
