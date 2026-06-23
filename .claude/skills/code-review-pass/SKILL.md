---
name: code-review-pass
description: |
  Run a longitudinal whole-codebase review cycle: execute a drift / dead-code /
  pattern-consistency audit against the Ambassadeurs CLAUDE.md conventions and
  invariants, write the dated findings doc `docs/code-reviews/YYYY-MM-DD.md`,
  land trivial fixes inline, spin off non-trivial findings as Linear VERB
  tickets, run tox, and open a PR. Use when the user says "run a code review
  pass", "do the code review cycle", or "start a new review cycle". Also driven
  by a weekly Routine — when invoked with `routine` (or `weekly` /
  `--no-approval`) in the args, runs end-to-end with no approval gate. Do NOT
  use for a per-PR / per-diff review (that's the built-in `/code-review` skill
  and the `reviewer` agent) — this is the whole-codebase drift audit.
user-invocable: true
allowed-tools: Task, Bash, Read, Edit, Write, Grep, Glob, mcp__linear
---

# Code review pass

A recurring whole-codebase audit for **drift, dead code, and pattern
consistency** against the conventions and invariants in `CLAUDE.md`. This is
*not* a diff review — for that, use `/code-review` or the `reviewer` agent.
Each cycle ships one artefact: `docs/code-reviews/YYYY-MM-DD.md`, plus inline
fixes, child tickets, and a PR.

The audit logic lives in the **`code-auditor` agent** (`.claude/agents/`).
This skill is the orchestrator: it runs the auditor, then acts on its
findings (fix / ticket / watch), assembles the doc, and opens the PR.

Read these before starting:
- `docs/code-reviews/README.md` if it exists — cadence, template,
  trivial-vs-spin-off rule. (On the first cycle, create the directory and a
  short README capturing that contract.)
- The most recent `docs/code-reviews/YYYY-MM-DD.md` — the previous cycle, for
  carry-forward watching items and "biggest movers" framing.

## Modes

Two modes. The audit and doc-assembly steps are identical; only the
approval-and-ship path differs.

### Interactive mode (default)

A human asked. Run the audit, present the proposed doc + the inline-fix list
+ the spin-off list **for approval via plan mode**, and only then land fixes,
create tickets, and open the PR.

### Routine mode

Invoked from the weekly scheduled Routine. **Trigger phrases** — any of:

- The invocation args contain `routine`, `weekly`, or `--no-approval`.
- The first user message looks like a scheduled-task header (e.g. starts
  with `[scheduled]`, names a cron schedule, or comes from
  `mcp__scheduled-tasks__*`).

In routine mode:

- **Skip the approval gate.** Run the full flow end-to-end: branch → audit →
  doc → inline fixes → child tickets → tox → push → PR. This matches the
  project's autonomous-push-PR norm.
- **No human is watching**, so the discipline matters more, not less:
  dedup tickets, keep inline fixes genuinely trivial, and never push a red
  branch.
- **Exit non-zero on any unrecoverable error** (Linear API failure that
  blocks the cycle, tox still red after a fix attempt, push/PR failure) so
  the Routine runtime surfaces it.
- **Never leak secrets** into ticket bodies, the doc, or the PR.

## Step 1 — Resolve the cycle date and previous cycle

- **Cycle date** = today, `YYYY-MM-DD` (UTC). This names the doc and the
  branch. Get it with `date -u +%F`.
- **Previous cycle** = newest existing `docs/code-reviews/*.md` that isn't
  `README.md`. Record its path and ticket for the doc's "Previous cycle"
  line and for carry-forward watching items.

If a doc already exists for today's date, you're re-running the same cycle —
update the existing file rather than creating a second one.

## Step 2 — Create this cycle's Linear ticket

The cadence is **one cycle per Linear ticket**. Each run gets a fresh ticket.

1. Resolve the Ambassadeurs team id via `list_teams` (prefix `VERB-`).
2. Create a new issue with `save_issue`:
   - **Team:** Ambassadeurs.
   - **Title:** `Code review pass — YYYY-MM-DD (drift, dead code, pattern consistency)`.
   - **Description:** the cycle's scope — a drift / dead-code /
     pattern-consistency review against the `CLAUDE.md` conventions and
     invariants — with the "Previous cycle" pointer set to the prior doc.
   - **Estimate:** `M` (3). **Label:** `Improvement` (or the team's nearest
     equivalent — resolve via `list_issue_labels`).
   - **State:** `In Progress` (resolve the state id via `list_issue_statuses`
     for the team — don't hardcode).
3. Note the new ticket's `VERB-NNN` identifier and internal `id` — you'll
   need both for the branch, child tickets, PR, and `Closes`.

If ticket creation fails in routine mode, exit non-zero — there's no cycle
without a ticket.

## Step 3 — Create the branch

```bash
git -C <repo-root> checkout main && \
git -C <repo-root> pull --ff-only && \
git -C <repo-root> checkout -b chore/VERB-NNN-code-review-YYYY-MM-DD
```

Verify the working tree is clean first. If the branch already exists, stop
(interactive) or exit non-zero (routine).

## Step 4 — Run the audit

Invoke the **`code-auditor`** subagent via the Task tool. Pass it:

- The repo root and the cycle date.
- The previous-cycle doc path (from Step 1), so it frames findings as
  carry-forward / resolved / new.
- An instruction to run its full checklist against the `CLAUDE.md`
  conventions and invariants, and return the structured report in its
  documented output format.

The auditor runs `tox -e test` for coverage and greps the tree; its working
tokens stay in its own context. You receive the structured findings:
inline-fixable, spin-off candidates, watching, the per-item checklist
results, and the tox baseline.

> For an unusually large drift backlog you may fan out **multiple**
> `code-auditor` instances over checklist groups in parallel and merge their
> reports. Default to a single invocation — it's simpler and more reliable
> for unattended runs.

## Step 5 — Land the inline fixes

For each **inline-fixable** finding, make the edit. Hold the line on what
"trivial" means — single-file, no behaviour change, no new test, no new
abstraction. If applying a fix turns out to need more than that, **demote it
to a spin-off** instead of forcing it.

After editing templates, run `pre-commit run djangofmt --files <paths>` so
the hook doesn't reformat on commit.

## Step 6 — Spin off the non-trivial findings

For each **spin-off candidate**, before creating anything:

**Dedup against open tickets.** Search Linear (`list_issues` filtered to the
Ambassadeurs team, open states; use the auditor's `existing-ticket-hint` and a
keyword query) for an existing open ticket covering the same finding. If one
exists, **do not** create a duplicate — record it under **Watching** in the
doc as "tracked by VERB-NN" and move on.

Otherwise create a child ticket with `save_issue`:

- **Team:** Ambassadeurs. **Parent:** this cycle's ticket (Step 2).
- **Title:** specific and imperative (e.g. `Raise public/views.py coverage to ≥90%`).
- **Description:** the finding, the file(s), and why it's non-trivial — enough
  for someone to pick it up cold. Size with a t-shirt estimate.
- **State:** the team's triage/backlog entry state (`list_issue_statuses`);
  do not auto-move child tickets into the active workflow.

Collect the created/〈matched〉 ticket URLs for the doc.

## Step 7 — Write the dated doc

Create (or update) `docs/code-reviews/YYYY-MM-DD.md`:

```markdown
# Code review — YYYY-MM-DD

**Reviewer:** <name — "Code review Routine" in routine mode, else the user>
**Branch:** chore/VERB-NNN-code-review-YYYY-MM-DD
**Tox baseline:** all green (N passed, M skipped, P% coverage) | N failures
**Previous cycle:** [YYYY-MM-DD](YYYY-MM-DD.md) (VERB-NN) | none (first cycle)

## Summary
One paragraph — overall health, biggest movers since the previous cycle.

## Inline-fixed
| Item | File(s) | Why trivial |
|------|---------|-------------|

## Spun off
| Item | Child ticket | Why non-trivial |
|------|--------------|-----------------|

## Watching
| Item | Notes |
|------|-------|

## Checklist results
1. <name> — ✅ / 🔧 / 📋 / 👀 — <evidence>
... (every checklist item present, even as "✅ no drift found")
```

If `docs/code-reviews/README.md` carries a "Most recent cycle" pointer, update
it to this cycle.

## Step 8 — Approval gate (interactive mode only)

Present, via plan mode, for approval **before** committing/pushing:

1. `EnterPlanMode`, then `ExitPlanMode` with: the full proposed doc, the list
   of inline fixes applied, and the list of tickets that would be
   created/matched.
2. **Hard gate:** do not commit, push, or open the PR until approved. On
   reject-with-edits, revise and re-present.

**Routine mode: skip this step entirely.**

> Tickets in Step 6 are created before this gate so the doc can link them.
> That's acceptable — they land in a triage state and dedup prevents spam. If
> you want a zero-side-effect preview in interactive mode, the user can ask;
> otherwise proceed as written.

## Step 9 — Verify green, then commit

Run the full suite the project gates on:

```bash
PATH=~/.local/bin:$PATH uv run tox
```

- **Green:** continue.
- **Red because of an inline fix:** fix it, or revert that fix and demote it
  to a spin-off. Re-run. **Never** push a red branch — exit non-zero in
  routine mode if it can't be made green.

Stage and commit. Subject: `VERB-NNN: code review pass YYYY-MM-DD`. Include
the doc, any README pointer update, and any inline-fix files.

## Step 10 — Push and open the PR

```bash
git -C <repo-root> push -u origin chore/VERB-NNN-code-review-YYYY-MM-DD
```

Open the PR with `gh pr create`:

- **Title:** `VERB-NNN: code review pass YYYY-MM-DD`.
- **Body:** what the cycle found (counts: inline-fixed / spun off / watching),
  a link to the dated doc, the tox baseline, the list of child tickets, and
  the magic line `Closes VERB-NNN` so Linear's GitHub integration closes the
  cycle ticket on merge.

Then move the cycle ticket to `In Review` (`save_issue`) and post the PR URL
as a comment (`save_comment`). Linear's integration handles `In Review`/`Done`
on PR open/merge, but set it explicitly in case the branch-name heuristic
misses.

## Step 11 — Report

- **Interactive:** "Cycle YYYY-MM-DD done. PR: <url>. Inline-fixed N, spun off
  M (tickets …), watching P. VERB-NNN is In Review."
- **Routine:** emit the same summary plus the PR URL to stdout. No user to
  report back to — the PR and the doc are the record.

## Common pitfalls

- **Confusing this with a diff review.** This audits the *whole codebase*
  against conventions; `/code-review` reviews a *diff*. Don't conflate them.
- **Inline-fixing something non-trivial.** If a "trivial" fix grows tests or
  touches a second module, demote it to a spin-off. Especially in routine
  mode — an unattended risky edit is worse than a ticket.
- **Duplicate tickets every week.** Always dedup against open Linear tickets
  before spinning off; recurring findings go to **Watching** as "tracked by
  VERB-NN".
- **Skipping checklist items.** Every checklist line must appear in the doc,
  even as "✅ no drift found" — the longitudinal record is the value.
- **Pushing red.** Run `tox` to green before push. Routine mode exits
  non-zero rather than shipping a failing branch.
- **Hardcoding Linear ids.** Resolve team/state ids at runtime — they drift.
