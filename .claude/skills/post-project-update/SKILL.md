---
name: post-project-update
description: >
  Draft and post a Linear project status update. Use when the user asks
  for a daily, weekly, or on-demand project update — e.g. "post a daily
  update for <project>", "project update for Ambassadeurs", "status update
  on <project>". Gathers everything shipped since the last update,
  groups it by theme, lists newly-logged tickets, surfaces what's still
  open for the next milestone, and posts via the Linear MCP
  `save_status_update` tool. Also used by an autonomous Routine for
  daily updates — when invoked with `routine` (or `daily` /
  `--no-approval`) in the args, runs end-to-end without an approval
  gate.
user-invocable: true
---

# Project status update workflow

A Linear project status update answers three questions for the reader:

1. **What has shipped since last time?** Every ticket, grouped by theme.
2. **What has been newly logged?** Tickets added since the last update.
3. **What's still open for the next milestone?**

## Modes

This skill runs in one of two modes. The drafting rules (Steps 1–5,
formatting, theme grouping) are identical in both — only the
approval-and-post path differs.

### Interactive mode (default)

A human asked for the update. Draft, present, wait for explicit
approval, **then** post. The full Step 1–8 workflow below applies.

### Routine mode

The skill was invoked from a scheduled task. Skip the approval gate and
post directly. Trigger phrases — any of:

- The invocation args contain `routine`, `daily`, or `--no-approval`.
- The first user message looks like a scheduled-task header (e.g.
  starts with `[scheduled]`, names a cron schedule, or comes from
  `mcp__scheduled-tasks__*`).

In routine mode:

- **No human is watching.** Cross-checking git log against Linear
  matters more, not less — there is no second pair of eyes.
- **No writing to local files.** Routines have no persistent disk so
  do not attempt to write e.g. drafts locally.
- **Exit non-zero on any Linear API error** so the scheduled-task
  runtime surfaces the failure.
- **Never share secrets.** The body is posted to Linear; do not include
  environment variables, tokens, internal URLs, or anything that
  wasn't already in a ticket.

### Empty-day handling

When git log + Linear both show no movement since the last update:

- **Interactive mode** → tell the user; ask whether to post a stub or
  skip.
- **Routine mode** → still post, to keep the daily cadence visible.
  Use a one-paragraph stub:

  ```
  **Project update — DD Month YYYY**

  No new tickets shipped or logged since the last update on
  <DD Month>. Open work for <milestone> is unchanged: <bullet list>.
  ```

  **Exception:** if the *previous* post was already an empty-day stub
  for the same milestone-list, **edit that post** rather than create a
  new one — call `save_status_update` with the prior `id` and append a
  short "still no movement on <today's date>" line. This avoids spamming
  the project activity feed during quiet stretches.

## Step 1 — Resolve project + last update

Confirm the project exists, grab its id, and find the timestamp of the
most recent status update. That timestamp is the lower bound for
"everything since last time".

```
mcp__<linear>__list_projects(query="<project name>")
mcp__<linear>__get_status_updates(type="project", project="<project name>", limit=1)
```

If the user gave a project alias (e.g. "ambassadeurs"), match case-insensitively.

## Step 2 — Gather shipped tickets since the last update

**Do not trust Linear's `updatedAt` alone.** Some tickets have their
status set by GitHub integration when a PR merges, and the Linear
`updatedAt` on the ticket may lag. Cross-check against git.

1. `git log --since='<last update iso date>' --pretty=format:'%h %ad %s' --date=short` — extract every `VERB-xxx` referenced in commit subjects.
2. For each ticket id, fetch the current status via Linear. Include any that are `Done` / canceled-duplicate / otherwise completed.
3. **Re-read the previous 1–2 status updates** to detect tickets that shipped *before* that update but were missed. If the previous update didn't list a ticket that git shows merged during its window, include it in the current update under a header like "Shipped since last update (including X from the previous window)".

This is the single most common failure mode — **always do step 3**.

## Step 3 — Group by theme

Reading a flat list of 14 ticket links is painful. Group the shipped
work into 2–5 themes (e.g. "Registration flow", "Matching engine",
"Match lifecycle & contact reveal", "Auth & accounts", "Admin oversight",
"i18n & copy"). Put the umbrella ticket in the theme header when one exists.

Keep each bullet to one sentence that explains *what changed* — not the
ticket title verbatim. A reader should be able to tell whether to dig
deeper without opening the ticket.

## Step 4 — Logged, housekeeping, open

Three more short sections:

* **Logged for later** — any new tickets created since the last update
  that aren't already shipped (umbrellas, follow-ups, spikes, known
  issues).
* **Housekeeping** — cancelations, duplicates merged, scope changes,
  status flips worth noting. Keep it terse.
* **Still open for <next milestone>** — copy the open list from the
  previous update, remove anything shipped, add any new launch tickets.

## Step 5 — Draft the body

Format the body as Markdown. Use Linear issue URLs
(`https://linear.app/<workspace>/issue/<ID>`), not bare `VERB-43`
mentions, so every ticket reference is a link.

Start with one short paragraph summarising the window — something the
reader can skim in ten seconds ("A heavy two-day sprint. Since the
last update we've shipped 14 tickets across three themes…").

## Step 6 — Present for approval

**Interactive mode.** Show the user the complete draft. If plan mode is
active, write it into the plan file and call `ExitPlanMode`. Otherwise
show it inline and ask for explicit approval before moving on. **Do not
post unapproved.**

**Routine mode: skip this step.** Ignore the draft completely, do not attempt
to write the body as Routines run in an ephemeral environment.

## Step 7 — Post

Call:

```
mcp__<linear>__save_status_update(
  type="project",
  project="<project name>",
  health="onTrack" | "atRisk" | "offTrack",
  body="<approved markdown>",
)
```

Default health is `onTrack` unless the user has flagged a blocker or a
missed commitment. **Routine mode**: never auto-downgrade — see Health
heuristics for the explicit signals that allow a non-`onTrack` value.

## Step 8 — Confirm

Re-fetch via `get_status_updates(type="project", project=..., limit=1)`
and report the update URL back to the user.

**Routine mode:** append the resulting URL to the log file from Step 6
and emit it to stdout. There is no user to "report back" to.

## Health heuristics

* **onTrack** — shipping at or ahead of plan; no blockers.
* **atRisk** — known blocker or slipped commitment, but a path forward.
* **offTrack** — stalled or target date missed with no credible recovery.

If the user doesn't say otherwise, prefer `onTrack` and surface anything
that would argue for a different rating in the `Still open` or a
`Risks` section.

**Routine mode default — `onTrack`.** The routine never auto-downgrades.
Set `atRisk` or `offTrack` only when one of these explicit signals is
present:

1. The Linear project carries a label named `at-risk` or `off-track`.
2. A comment on the most recent project status update, posted by the
   project lead within the last 24h, contains the literal token
   `health: atRisk` or `health: offTrack`.
3. The Routine prompt itself explicitly instructs a non-`onTrack`
   value.

Anything softer (slipping vibes, gut feel, an unhappy comment somewhere
in a ticket) does not flip health in routine mode — the routine should
flag concerns inside the body's `Still open` / `Risks` section instead.

## Common pitfalls

* **Posting autonomously in interactive mode.** Always get approval.
  Use plan mode if it's active. (Routine mode is the only path that
  posts without approval, and only when its trigger phrases match.)
* **Missing tickets from the previous window.** Always cross-check the
  prior 1–2 updates against git log. The prior update may have missed
  tickets that merged earlier the same day. **Critical in routine
  mode** — no human will catch the omission.
* **Bare ticket mentions instead of links.** Every `VERB-NNN`
  reference should be a Markdown link to the Linear issue URL.
* **Copy-pasting ticket titles as bullets.** The title is context-free;
  rewrite the bullet as one sentence explaining *what changed* for the
  reader.
* **Omitting the summary paragraph.** The reader wants a 10-second
  skim; give them one.
* **Routine duplicating empty-day stubs.** If the previous post was
  already a stub and nothing has moved, *edit* it (`save_status_update`
  with the prior `id`) instead of posting a near-identical entry.
* **Routine swallowing failures.** Any Linear API error must propagate
  as a non-zero exit so the scheduled-task runtime can alert. Silent
  failure on a daily update means the cadence dies unnoticed.
