---
name: scope
description: |
  Scope a Linear ticket: read the description, explore the codebase, produce a
  written scope, post it as a Linear comment, and transition the ticket from
  Todo to Ready for dev. Use when the user asks to "scope VERB-NN", "let's
  scope VERB-NN", "work out VERB-NN", "spec VERB-NN", or any phrasing where
  they want a Linear ticket turned from a sentence into a proper scope. Do NOT
  use for: starting work on an already-scoped ticket, asking questions about a
  ticket without producing a scope, or any message that doesn't explicitly
  reference a VERB-NN identifier.
allowed-tools: Task, Bash, Read, Grep, Glob, EnterPlanMode, ExitPlanMode, mcp__linear
---

# Scope VERB-$1

You are scoping Linear ticket VERB-$1 in the Ambassadeurs codebase.

## Step 1 — Verify ticket state

Use the Linear MCP `get_issue` tool to fetch VERB-$1.

**Hard precondition:** the ticket must be in `Todo` state. If it is in
`Backlog`, tell the user it needs to be prioritised first and stop. If it
is in `Ready for dev`, `In Progress`, `In Review`, or `Done`, tell the user
it has already been scoped and stop. Do not proceed past this step on a
wrong-state ticket.

## Step 2 — Delegate scoping to the scoper agent

Invoke the `scoper` subagent via the Task tool. Pass it:
- The Linear ticket ID (`VERB-$1`)
- The Linear ticket git branch name as fetched
- The ticket title and description as fetched
- A clear instruction to produce a scope document and return it as the final
  message

The scoper runs in an isolated context. It will explore the codebase to ground
the scope in what actually exists. Its working tokens stay in its own context —
you only see the final scope document.

## Step 3 — Present the scope via plan mode for approval

Once the scoper returns, present the scope through plan mode so it renders
in the sidebar UI with a proper approve / reject control — not as inline
conversational text the user has to reply to:

1. Call `EnterPlanMode` to enter plan mode. No research needed here — the
   scoper already did it. Edits and writes are blocked in plan mode, which
   keeps this step strictly read-only.
2. Immediately call `ExitPlanMode` with the **full scope document verbatim**
   as the `plan` argument. Do not summarise, reformat, or wrap it in extra
   commentary — the sidebar should show exactly what would be posted to
   Linear.

`ExitPlanMode` renders the scope in the sidebar with an approve / reject
control. The user reviews and acts via the UI.

If the user rejects with edits, revise and re-present via `ExitPlanMode`
again — each revision goes through the sidebar, not as inline text. Do not
invoke the scoper again unless the user explicitly asks for a fresh take —
refinements are usually small and you can handle them in the main thread.

**Hard gate:** do not post to Linear or transition the ticket until the
user has approved the scope via the sidebar.

## Step 4 — On approval, post and transition

When the sidebar approval comes through:

1. Post the scope as a comment on the Linear ticket using `save_comment` (the
   comment's `issueId` is the internal `id` from `get_issue`, NOT the `VERB-NN`
   identifier).
2. Transition the ticket to `Ready for dev` using `save_issue` with the existing
   `id` and the new `stateId`.
3. Confirm to the user: "Scope posted, VERB-$1 moved to Ready for dev."
