---
name: work-on
description: |
  Work a Linear ticket end-to-end in a single session: scope it (if it still
  needs scoping) and then immediately implement it through to an open PR —
  Todo → Ready for dev → In Progress → In Review, with both approval gates
  intact. The ticket may be referenced as a bare number — every ticket in
  this project is VERB-prefixed, so "work on 42" means VERB-42. Use when
  the user says "work on VERB-NN", "work on NN", "take NN through to
  review", "scope and implement NN", "do VERB-NN end to end", or any
  phrasing that asks for both scoping and implementation of one ticket in
  one go. Do NOT use for: scoping only (`scope` skill), implementing an
  already-scoped ticket when the user only asks to implement (`implement`
  skill), resuming work on an existing branch, or any message without a
  ticket reference.
allowed-tools: Skill, mcp__linear
---

# Work on VERB-$1

Run the `scope` and `implement` skills back to back so VERB-$1 goes from
Todo to In Review in one session. This skill is a pure orchestrator: it
adds state dispatch and the handoff between phases, and nothing else. Each
phase's own rules — preconditions, approval gates, pause directives, stop
conditions — apply unchanged. Never duplicate or shortcut a sub-skill's
steps from here; invoke the skill and follow it.

## Step 0 — Parse the arguments

- **Ticket:** the canonical form is the bare number (`/work-on 42`), since
  every ticket in this project carries the `VERB-` prefix. Accept
  `VERB-42` too, but normalise to the bare number for passing to the
  sub-skills (they template it as `VERB-$1`).
- **Pause directive:** any trailing instruction (e.g. *"work on VERB-123
  but stop before pushing"*) belongs to the implement phase. Hold it and
  pass it through verbatim in step 3 — do not act on it during scoping.

## Step 1 — Dispatch on ticket state

Fetch the ticket with the Linear MCP `get_issue` tool and branch on state:

- **Todo** → full run: step 2, then step 3.
- **Ready for dev** → already scoped; tell the user you're skipping the
  scope phase and go straight to step 3.
- **Backlog** → stop: the ticket needs prioritising into Todo first.
- **In Progress** → stop: work already exists. Suggest switching to the
  ticket's branch and using `/implement` in resume mode instead.
- **In Review / Done** → stop: nothing to do.

The sub-skills re-check state themselves — that redundancy is fine and
intentional. This dispatch exists so the user gets a clear answer up front
rather than a failure two steps in.

## Step 2 — Scope phase

Invoke the Skill tool with `skill: scope` and the ticket number as args,
and follow the scope skill in full — including presenting the scope via
plan mode and waiting for sidebar approval.

- If the user approves: the scope is posted to Linear and the ticket is in
  `Ready for dev`. Continue to step 3 **without asking whether to
  continue** — proceeding is the whole point of this skill. A brief
  "Scope posted — moving on to implementation." is enough.
- If the user rejects and asks for revisions: stay in the scope phase
  until a revision is approved.
- If the user rejects and abandons (or the scope skill hits one of its
  hard stops): stop entirely. Do not implement a ticket whose scope was
  not approved.

## Step 3 — Implement phase

Invoke the Skill tool with `skill: implement` and args of the ticket
number plus any pause directive held from step 0. Follow the implement
skill in full: branch creation, ticket to In Progress, the plan-approval
hard gate, implementer/reviewer loop, tox gate, push, PR, ticket
to In Review.

The implement skill re-fetches the ticket and its comments, so it will
find the scoping comment posted in step 2 (or pre-existing, if step 2 was
skipped). If it cannot find a scoping comment after step 2 reported
success, stop and surface that — something went wrong with the Linear
write; do not improvise a scope.

## Step 4 — Report

The implement skill's own final report stands. Add a one-line wrap-up for
the whole run, e.g.:

> "VERB-NN worked end to end: scoped (or: scope pre-existing), PR opened
> at <url>, ticket now In Review."

## Failure handling

If either sub-skill stops at one of its hard stops (wrong state, missing
scoping comment, branch already exists, repeated review blocker), that
stop is final for this skill too. Report where in the chain it stopped and
why, so the user can resume with the narrower skill (`/scope` or
`/implement`) once the blocker is resolved. Never work around a
sub-skill's stop to keep the chain moving.
