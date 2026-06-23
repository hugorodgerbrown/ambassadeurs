---
name: ticket-authoring-guide
description: Use when creating a new Linear ticket, updating an existing ticket's description, or posting a scoping comment on a ticket. Covers the scoping comment contract, ticket decomposition rules (one ticket per independently-shippable unit), MCP parameter gotchas (priority/estimate enums, state name matching, `blocks` relationship quirk), and the rule that only a clean scoping comment promotes a ticket to `Ready for dev`. Do NOT use when implementing a ticket that's already scoped — use ticket-implementation-guide for that.
---

# Linear ticket authoring guide

This skill governs how tickets are **created, scoped, and updated** in the
Ambassadeurs Linear workspace (team prefix `VERB-`). The narrative lives in
`CLAUDE.md` (Linear workflow section); this skill is the agent-facing rulebook.

Linear is the source of truth. Nothing of substance lives only in a chat
window — if it matters, it goes on the ticket.

## When this skill applies

- Creating a new ticket (from a planning conversation, a bug just noticed,
  a discussion that decomposed into work).
- Posting a scoping comment on an existing ticket to move it to
  `Ready for dev`.
- Updating a ticket's description, labels, priority, or estimate.
- Posting a project update on a Linear project.

If the user is asking to **implement** a ticket that's already scoped,
stop — that's the `ticket-implementation-guide` skill's job.

## The two creation modes

### Mode 1 — Single ticket, scoped in one pass

Conversation has produced a single, clear piece of work. Create the issue
*and* post the scoping comment *and* move to `Ready for dev` in one
sequence. No intermediate `Todo` stop.

### Mode 2 — Multiple tickets from one discussion

Conversation decomposed into several independent pieces of work. For each
piece:

- Create the issue with title, label, priority, and a one-paragraph
  description.
- If the approach is settled in the discussion → post the scoping comment
  and move to `Ready for dev`.
- If the approach is still fuzzy → leave at `Todo` and add a note naming
  the open question.

After creation, report back a summary like:

> Created VERB-102 (Ready for dev), VERB-103 (Ready for dev),
> VERB-104 (Todo — needs decision on the contact-window length).

## Decomposition rules

- **One ticket per independently-shippable unit.** "Add the match-accept
  flow + refactor the surrounding matching module" is two tickets, not one.
- **Don't promote underspecified work.** If the approach for a ticket isn't
  settled, it stays at `Todo` with a note on what's missing — even if the
  adjacent tickets are ready. Underspecified tickets leaking into
  implementation is the failure mode this rule prevents.
- **Title, label, priority, one-paragraph description are mandatory** at
  creation time. The scoping comment is in addition to the description,
  not instead of it.

## Scoping comment contract

This is the handoff artefact. The implementer reads it on pickup and
inherits full context. Every scoping comment has these four sections:

- **Approach** — 2–4 sentences on the chosen solution.
- **Touch list** — files/modules expected to change.
- **Tests** — what will be covered.
- **Open questions** — anything still undecided.

**Promotion rule:** if `Open questions` is non-empty, the ticket stays at
`Todo`. Only a clean scoping comment (no open questions) moves a ticket to
`Ready for dev`.

## Status transitions this skill owns

This skill writes to Linear up to `Ready for dev`. Post-commit states
(`In Progress`, `In Review`, `Done`) are handled by the GitHub–Linear
integration and are not this skill's concern.

| Status          | When this skill sets it                                     |
|-----------------|-------------------------------------------------------------|
| `Backlog`       | Ticket created but not yet triaged                          |
| `Todo`          | Ready to be picked up, approach not yet settled             |
| `Ready for dev` | Clean scoping comment posted (no open questions)            |

## Linear MCP tool gotchas

These are the traps. Get them wrong and the call silently does the wrong
thing or fails opaquely.

### `save_issue` — priority is an integer enum

| Value | Meaning |
|-------|---------|
| 0     | None    |
| 1     | Urgent  |
| 2     | High    |
| 3     | Medium  |
| 4     | Low     |

Pass an integer, not a string. `"High"` is not valid; `2` is.

### `save_issue` — estimate is Fibonacci

| Value | T-shirt |
|-------|---------|
| 1     | XS      |
| 2     | S       |
| 3     | M       |
| 5     | L       |

No `4`, no `8`. Stick to the scale.

### Issue states must match exact team-configured names

`save_issue` takes state by name. The name must match exactly — capitalisation,
spacing, punctuation. If unsure, call `list_issue_statuses` for the team
first rather than guessing. Guessing wastes a round trip when the call fails.

### The `blocks` relationship is unreliable via MCP

The `blocks` relationship parameter does not reliably accept any known format
through the current MCP surface. **Don't use it.** Instead, document the
blocking relationship in the ticket description or a comment:

> Blocks VERB-108 — the match-accept token contract lands here first.

> Blocked by VERB-95 (must ship before this can be picked up).

This is a workaround, not a preference. Revisit if the MCP surface changes.

### Project updates are not a comment, not a document

When asked to "post a project update" for a Linear project, post it as a
Linear **project update** — not a comment on a project, not a document.
`create_project_update` is not exposed via the current MCP surface, so:

1. Write the formatted text of the update.
2. Hand it back along with the direct project URL in the form
   `https://linear.app/hugorodgerbrown/project/[slug]` for manual paste.

Don't silently downgrade to posting a comment — that puts the update in the
wrong place and breaks the project update feed.

## House style

- **Decisions before copy.** If a decision is still being made in the chat,
  don't write ticket copy yet. Fold the decision into the description once
  settled, then write.
- **Descriptions are paragraphs, not bullet soup.** A one-paragraph
  description that reads like a human wrote it beats a bullet list of
  fragments.
- **Link ruthlessly.** Reference related tickets by `VERB-xxx`; Linear
  auto-links them.
- **No chat-window residue.** Don't paste "per our conversation" or
  "as we just discussed" into ticket copy. Write as if the ticket is the
  first time the reader is seeing the problem.

## Verification step

After creating multiple tickets via Mode 2, **remind the user to verify in
Linear** before handing any of them to the implementer. The chat window is
not the source of truth; Linear is, and mistakes are cheapest to catch
before the implementer picks up a malformed ticket.

## When to stop and ask

- The user asks for a ticket but the approach in the discussion is genuinely
  unclear → propose a `Todo` ticket with the open question called out,
  rather than guessing the approach.
- The user asks to promote a ticket to `Ready for dev` but there are open
  questions in the scoping comment → refuse the promotion and name the
  open questions.
- Priority or label is ambiguous from context → ask once, don't guess.
