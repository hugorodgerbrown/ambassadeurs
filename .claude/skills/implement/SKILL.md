---
name: implement
description: |
  Take a scoped Linear ticket from Ready-for-dev through to an open PR in one
  flow: create the branch, plan, wait for plan approval, implement + review,
  push, and open the PR. Use when the user says "implement VERB-NN", "implement
  this", "go on VERB-NN", or just "implement" / "go" / "continue" when a
  feature branch is already checked out. Do NOT use for: scoping a ticket
  (`scope` skill), ad-hoc edits unrelated to a ticket, or any message without
  either a VERB-NN reference or an existing VERB-NN branch checked out.
allowed-tools: Task, Bash, Read, Edit, Write, Grep, Glob, mcp__linear
---

# Implement VERB-$1

End-to-end implementation flow. The **only** mandatory stop is plan approval.
Everything after that runs through to an open PR without prompting the user,
unless they appended a pause directive to the trigger.

## Optional pause directives

The user may append a pause directive, e.g. *"implement VERB-123, but stop
before pushing"*. Parse it against the table below and treat the matched
checkpoint as a hard stop. If the phrasing is ambiguous, ask which checkpoint
they mean — do not guess.

- **plan-only** — phrases like "plan only", "stop after the plan", "just the
  plan", "don't implement yet". Stop after step 3 (plan approval). Do not
  invoke the implementer.
- **stop-before-push** — phrases like "stop before pushing", "stop before the
  PR", "don't open the PR yet", "review only". Run through step 4
  (implement + review). Stop before step 5.

When stopping at a non-default checkpoint, tell the user explicitly:

> "Stopped at <checkpoint>. Say `continue` (or `push the PR` / `open the PR`)
> to resume — the branch is ready."

Resuming uses the existing branch state. No re-plan, no re-review.

## Step 1 — Detect mode

Two entry modes:

- **Fresh ticket mode** — trigger included `VERB-NN`. Run steps 2 onward.
- **Resume mode** — no ticket number in the trigger. Run:

  ```bash
  git branch --show-current
  git status --short
  ```

  - Current branch must include `VERB-NN`. If on `main`, stop and ask the user
    which ticket they want.
  - Working tree should be clean. If dirty, ask how to proceed.
  - Skip to whichever step the user is resuming from (usually step 4 or 5,
    based on the context of the previous session).

## Step 2 — Set up the branch (fresh-ticket mode only)

### 2a. Verify ticket is Ready

Use Linear MCP `get_issue` to fetch VERB-$1.

**Hard precondition:** state must be `Ready for dev`. If it is `Todo` or
`Backlog`, tell the user the ticket needs scoping first and stop. If it is
`In Progress`, ask whether they want to resume existing work (in which case
they should switch branches manually, not run this skill). If `In Review`
or `Done`, stop.

Read the ticket description AND comments — the scope lives in a comment
posted by the `scope` skill. If there is no scoping comment, stop and tell
the user to scope first. Store the Linear-supplied git branch name.

### 2b. Create local branch

```bash
git checkout main && \
git pull --ff-only && \
git checkout -b {{git_branch_name}}
```

If branch creation fails because the branch already exists, stop and ask the
user how to proceed. Do not silently switch to an existing branch.

### 2c. Transition ticket to In Progress

Use Linear MCP `save_issue` with the ticket's internal `id` and the
`In Progress` state. Do not push the branch — Linear's GitHub integration
only cares about PR open / merge, not branch existence.

## Step 3 — Produce plan and wait for approval

Now produce an implementation plan. **Use plan mode** so the plan renders
in the sidebar UI with a proper approval button — not as a wall of
conversational text:

1. Call `EnterPlanMode` to enter plan mode. While in plan mode, read the
   relevant parts of the codebase to ground the plan and reference the
   scope (from the Linear comment) for acceptance criteria. Edits and
   writes are blocked in plan mode, which is the point — research only.
2. When the plan is ready, call `ExitPlanMode` with the full plan as the
   `plan` argument. The plan should cover:

   - **Files to touch** — concrete paths in the Ambassadeurs repo
   - **Order of changes** — what gets built first, second, third
   - **Test strategy** — what tests to write or update, what existing tests
     cover this surface
   - **Risks / open questions** — anything that might bite during
     implementation

   `ExitPlanMode` renders the plan in the sidebar with an approve / reject
   control. The user reviews and acts via the UI.

If the user pushes back, revise and re-present via `ExitPlanMode` again —
each revision goes through the sidebar, not as inline text.

**Hard gate:** do not write any code or invoke the implementer until the
user has approved a plan via the sidebar.

**Pause check:** if the directive is `plan-only`, stop here after approval
with the resume message. Otherwise continue to step 4.

## Step 4 — Implement and review

### 4a. Implementer agent

Use the Task tool to invoke the `implementer` subagent. Pass it:

- The Linear ticket number & branch name
- A reminder to consult the Linear ticket scope (in comments) and the
  approved plan from this session's context
- An instruction to commit incrementally with conventional commit messages,
  run tests as it goes, and report back when the plan is fully implemented

The implementer runs in its own context. Its exploration and intermediate
work do not pollute the main thread.

### 4b. Reviewer agent

Once the implementer reports done, invoke the `reviewer` subagent in a fresh
forked context. Pass it:

- The ticket number
- The branch name
- An instruction to check: tests pass, linter clean, scope acceptance
  criteria met, no obvious mistakes or omissions

The reviewer returns one of: **clean**, **blockers** (with specifics), or
**suggestions** (non-blocking).

### 4c. Blocker loop

- If reviewer returns **blockers**: pass them back to the implementer with a
  request to address them. Re-invoke the reviewer when done. Loop until
  clean or until the same blocker appears twice — in which case stop and
  surface to the user; something is structurally wrong.
- If reviewer returns **clean** or **suggestions only**: continue.

**Pause check:** if the directive is `stop-before-push`, stop here with the
resume message. List any non-blocking suggestions so the user can decide
whether to address them before the PR. Otherwise continue to step 5.

## Step 5 — Push and open the PR

### 5a. Run the full tox gate

`uv run tox` is the project's pre-PR gate (`fmt`, `lint`, `mypy`,
`django-checks`, `test`). Running it before pushing — not after — means any
fix lands in the same PR rather than a follow-up.

Run:

```bash
uv run tox
```

(Prefix with `PATH=~/.local/bin:$PATH` if uv isn't on the shell's PATH.)

- **All green:** continue to 5b.
- **Any env red:** decide whether the fix is trivial.
  - If trivial (formatting, a missed type hint, a stale system check), fix
    inline and re-run `tox`.
  - If it needs real work, loop back into step 4 with the failure as a
    blocker. Do not push a red branch.

### 5b. Push

```bash
git log origin/main..HEAD --oneline
git push
```

If there are zero commits ahead of `origin/main`, stop — there's nothing to
PR (this should not happen after a successful step 4, so investigate).

### 5c. Generate PR title and body

Read:

- The Linear ticket scope (from `get_issue` + comments)
- The commit log on this branch

Produce:

- **Title:** `VERB-NN: <short summary, sentence case, no trailing period>`
- **Body:** structured as:
  - **What** — one-paragraph summary of the change
  - **Why** — link to the Linear ticket, brief context
  - **How** — bullet list of the main changes
  - **Testing** — what tests cover this, anything to verify manually
  - `Closes VERB-NN` (Linear's GitHub integration auto-transitions on merge)

Print the title and body to the transcript before opening the PR — purely
informational, not a gate. The PR can be edited cheaply with `gh pr edit`
afterwards if anything needs tweaking.

### 5d. Open the PR

Use `gh pr create` with the title and body via HEREDOC. Capture the PR URL
from the output.

### 5e. Transition ticket to In Review

Use Linear MCP `save_issue` with the ticket's internal `id` and `In Review`
state. Post a comment on the ticket with the PR URL via `save_comment`.

## Step 6 — Report

> "PR opened: <url>. VERB-NN is now In Review."

If the reviewer left non-blocking suggestions earlier, list them again here
so the user can decide whether to address them in a follow-up.

Stop. The user takes it from here — review, merge. On merge to `main`, Render
auto-deploys (`build.sh` runs migrations) and Linear's GitHub integration
auto-transitions the ticket to Done if the `Closes VERB-NN` line is in the PR
body.
