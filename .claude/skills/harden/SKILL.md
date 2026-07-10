---
name: harden
description: |
  Put the controls back on. Takes a VERB branch whose code works but has not
  been reviewed — typically the output of a `/rapid` iteration session — and
  runs the full quality pass, then opens the PR: reviewer + blocker loop, tests
  to the 90% target, security audit, QA scenarios, docs, and the full tox gate,
  finishing with push → PR → Linear In Review. Use when the user says
  "/harden", "harden this", "finalise it", "bring it up to standard", "clean it
  up and open the PR", or after `/rapid` when they're happy with the output. Do
  NOT use to scope or build a feature from scratch (`implement` / `work-on`), or
  for the whole-codebase drift audit (`code-review-pass`).
user-invocable: true
allowed-tools: Task, Bash, Read, Edit, Write, Grep, Glob, Skill, mcp__linear
---

# /harden

The counterpart to `/rapid`. Where `/rapid` suspends the quality gate to iterate
on output, `/harden` re-applies every control and takes the branch to an open
PR. It is the tail of the `implement` flow (review → gate → PR) generalised to
"whatever is on this branch now", plus the security / QA / docs passes.

The whole run is autonomous through to the PR **except** where a check surfaces
something that needs a human decision (see stop conditions). Do not ask for
approval between passes.

## Step 0 — Context and surface

```bash
git branch --show-current
git status --short
git fetch origin
git diff origin/main...HEAD --stat
```

- Must be on a `VERB-NN` branch. If on `main`, stop and ask.
- If the tree is dirty, commit the outstanding work first
  (`chore(VERB-NN): …` or fold it into a sensible commit) — harden reviews
  committed state.
- The **surface to harden** is the branch diff vs `origin/main`
  (`git diff origin/main...HEAD`). Harden does not care which commits were
  `rapid(...)` vs `implement` — it brings the whole diff up to standard.
- Fetch the ticket for scope / acceptance criteria: Linear MCP `get_issue` on
  VERB-NN plus its comments (the scope lives in a `scope`-skill comment).

Announce the plan in one line: the passes below, in order, then the PR.

## Step 1 — Review (reviewer + blocker loop)

Invoke the `reviewer` subagent (Task tool) in a fresh context. Pass it the
ticket number, the branch name, the diff surface, and an instruction to check:
scope acceptance criteria met, no correctness bugs, no convention drift, no
Invariant violations, test coverage present. It returns **clean**,
**blockers**, or **suggestions**.

- **Blockers:** hand them to the `implementer` subagent to fix, then re-run the
  reviewer. Loop until clean — or until the same blocker survives twice, in
  which case stop and surface it (something is structurally wrong).
- **Clean / suggestions only:** continue, carrying the suggestions forward.

`/rapid` work is the usual source of blockers here — inline `style=` or
`hx-on:*` that need moving to classes / server-side, missing `require_htmx`
guards, unwrapped copy, missing docstrings. Expect them and fix them.

## Step 2 — Tests

The new surface must be covered to the project's 90% target. Have the
`implementer` subagent write or update tests to mirror the source tree under
`tests/`, using FactoryBoy `.create()` factories, tz-aware datetimes, and
`uv run tox -e test`. Re-run the reviewer if it flagged coverage gaps, so the
verdict reflects the added tests.

## Step 3 — Security audit

Invoke the `security-auditor` subagent against the branch diff, with the
project threat surface (signed-link tokens, PII reveal before mutual accept,
self-matching, eligibility spoofing, HTMX partials, split settings). It is
read-only and produces findings. Feed any blocking findings back through the
`implementer` and re-review; note non-blocking ones for the PR body.

## Step 4 — QA scenarios

Invoke the `qa` subagent to generate the manual test document for the built /
changed feature (happy paths + common handled failures). It is read-only. Save
its output where the qa agent places it and reference it in the PR body so a
human tester can follow it.

## Step 5 — Docs

Invoke the `documenter` subagent to bring documentation in line with the
implemented change: docstrings and header comments, and where the change is
architecturally non-obvious, a `docs/decisions/` ADR, a `docs/glossary.md` line
for any new domain term → symbol, and CLAUDE.md routing-table / convention
updates. Run docs **after** review + security so they describe the final shape.

Do **not** touch `locale/` here — catalogue rebuilds are a decoupled
single-purpose task (ADR 0016). Wrapping new copy for translation is in scope
(Invariant 8); rebuilding the `.po`/`.mo` files is not.

## Step 6 — Full tox gate

```bash
uv run tox
```

(Prefix with `PATH=~/.local/bin:$PATH` if uv isn't on PATH.) All envs
(`fmt`, `lint`, `mypy`, `django-checks`, `test`) must be green.

- **Green:** continue.
- **Red:** if trivial (formatting, a missed hint, a stale system check), fix
  inline and re-run. If it needs real work, loop back to the relevant pass. Do
  not push a red branch.

## Step 7 — Push and open the PR

Mirrors `implement` steps 5b–5e:

```bash
git log origin/main..HEAD --oneline
git push
```

Then `gh pr create` with:

- **Title:** `VERB-NN: <short imperative summary>`
- **Body:**
  - **What** — one-paragraph summary
  - **Why** — link the ticket, brief context
  - **How** — bullets of the main changes
  - **Testing** — the tests added and the QA doc reference (Step 4)
  - **Security** — any non-blocking findings carried from Step 3, or "clean"
  - `Closes VERB-NN`

Then move the ticket to **In Review** (Linear MCP `save_issue`) and post the PR
URL as a comment (`save_comment`).

Optionally offer to squash the rough `rapid(...)` commits into meaningful ones
before the PR — but the repo squash-merges, so the branch's commit shape does
not survive to `main`; only do it if the user wants a clean pre-merge history.

## Step 8 — Report

> "PR opened: <url>. VERB-NN is now In Review. Passes run: review, tests,
> security, QA, docs, tox — all green."

List any non-blocking reviewer / security suggestions so the user can decide
whether to fold them in before merge.

## Stop conditions

Stop and surface to the user (do not push through) if: the same reviewer
blocker survives two fix cycles; the security audit finds an Invariant
violation the fix isn't obvious for; tox stays red after a trivial-fix attempt;
or hardening reveals the branch diff no longer matches the ticket scope (comment
on the Linear issue first).
