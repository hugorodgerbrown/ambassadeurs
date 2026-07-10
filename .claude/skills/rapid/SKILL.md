---
name: rapid
description: |
  Rapid iteration mode — a temporary "controls off" period layered ON TOP of a
  feature that `implement` has already built. On the existing VERB branch, make
  fast changes to the design / output with the quality gate suspended: no
  reviewer agent, no tox, no per-change tests, no docs. Keeps the dev server
  running so each change is visible, commits the rough work so nothing is lost,
  and loops until you are happy with the OUTPUT (not the code). Use when the user
  says "/rapid", "rapid iteration", "throw off the controls", "let me iterate on
  the design", "just make it look right", or "stop reviewing every change". Do
  NOT use to start a feature from scratch (`implement`), for a throwaway
  experiment on its own branch (`spike`), or to finalise and open the PR
  (`harden`). When the user is happy, hand off to `/harden`.
user-invocable: true
allowed-tools: Bash, Read, Edit, Write, Grep, Glob, Skill, mcp__Claude_Preview__preview_start, mcp__Claude_Preview__preview_screenshot, mcp__Claude_Preview__preview_eval, mcp__Claude_Preview__preview_snapshot, mcp__Claude_Preview__preview_console_logs, mcp__Claude_Preview__preview_logs, mcp__Claude_Preview__preview_resize
---

# /rapid — $ARGUMENTS

Rapid iteration is a **mode, not a feature build**. `implement` has already
produced a first working iteration on the current VERB branch; `/rapid` drops
the controls so you can iterate quickly on how it *looks and behaves*. The
output is the point — the code is allowed to be rough. `/harden` puts the
controls back on afterwards.

**What is suspended while in this mode:**

- the `reviewer` agent and the blocker loop
- `tox` (fmt / lint / mypy / django-checks / test)
- writing or updating tests
- docstrings, ADRs, glossary, CLAUDE.md
- the plan-approval gate

**What still holds** (breaking these only creates rework at harden time, so
respect them even here):

- the code must **run** — every iteration is demonstrable in the browser
- the Invariants in `CLAUDE.md` (PII hidden until mutual accept, no `mark_safe`
  on user content, email lowercasing, `require_htmx` on partials, i18n wrapping)
- **CSP-safe patterns** — no inline `style=` attributes and no `hx-on:*`; both
  are dead under the production CSP and invisible in dev, so you would only rip
  them out at harden. Style via Tailwind classes; keep behaviour server-side.

## Step 1 — Confirm context

```bash
git branch --show-current
git status --short
```

- The branch **must** be a `VERB-NN` feature branch. If it is `main` or a
  non-VERB branch, stop and ask — `/rapid` layers on top of in-flight feature
  work, it does not create a branch. (For a fresh sandbox, that's `/spike`; to
  start a ticket, that's `/implement`.)
- A dirty tree is fine here — uncommitted work from the `implement` run is
  expected. Note it and carry on.

## Step 2 — Frame the loop

State in one or two sentences what you're about to change and how you'll know
it's right (what the user will look at). Keep it short — this is a checkpoint so
the user can redirect, not a plan for approval. Then remind them, in one line,
that the controls are off and `/harden` is the exit.

## Step 3 — Keep the app visible

The value of this mode is seeing each change immediately.

- Start (or reuse) the dev server with `preview_start`.
- Start the Tailwind watcher **once** in the background so template / CSS edits
  compile automatically:

  ```bash
  npx @tailwindcss/cli -i ./src/css/main.css -o ./static/css/output.css --watch
  ```

  (run in the background). If a change touches CSS or templates and the output
  looks stale, the watcher hasn't caught up — reload and re-check.

## Step 4 — Iterate

Loop, fast:

1. Make the change (edit inline — do **not** delegate to the `implementer`
   agent; the point is speed and tight feedback).
2. Reload if HMR isn't active (`preview_eval` → `window.location.reload()`).
3. Show the result: `preview_screenshot` for visual changes, `preview_snapshot`
   for structure, `preview_resize` (mobile / dark) when layout or theming is in
   play. Check `preview_console_logs` / `preview_logs` for errors — a broken
   page is not a valid iteration.
4. Report what changed and ask what's next.

Commit the rough work periodically so nothing is lost, using a marker prefix so
`/harden` (and the eventual squash-merge) can see what was design iteration:

```bash
git add <files> && git commit -m "rapid(VERB-NN): <what changed>"
```

Do not run `tox`, invoke the reviewer, or write tests in this loop. If you spot
a real bug that isn't just cosmetic, note it for harden rather than opening a
full review cycle mid-flow.

## Step 5 — Exit

When the user says they're happy with the output, stop and hand off — do **not**
run any gate yourself:

> "Output locked in. The branch has `rapid(VERB-NN)` commits with the controls
> off. Run `/harden` to bring it up to standard — reviewer, tests, security, QA,
> docs, full tox — and open the PR."

Leave the dev server and Tailwind watcher running in case they want another
pass before hardening.
