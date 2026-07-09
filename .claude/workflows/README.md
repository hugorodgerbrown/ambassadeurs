# Workflows

Multi-agent orchestration scripts run by the `Workflow` tool. Each script fans
out subagents deterministically (fan-out, verify, synthesize) rather than
letting a single agent decide the control flow. Invoke a script by its `meta.name`:

```
Workflow({ name: '<name>' })
Workflow({ name: '<name>', args: { … } })
```

Scripts are plain JavaScript (not TypeScript). See the `Workflow` tool docs for
the available hooks (`agent`, `parallel`, `pipeline`, `phase`, `log`, `args`).

| Workflow | What it does |
|----------|--------------|
| [`adversarial-code-review`](adversarial-code-review.js) | Reviews the branch diff across five dimensions (correctness, invariants/security, Django-ORM, convention drift, tests), then makes every finding survive an adversarial refutation panel before reporting it. |

## adversarial-code-review

One reviewer per dimension runs over `git diff <base>...HEAD` (plus uncommitted
changes), reusing the repo's `reviewer` agent so it already knows the CLAUDE.md
conventions and the nine invariants. Each raised finding is then attacked by a
panel of independent skeptics — each with a distinct lens (reproduce,
already-handled, convention-check, false-positive) and prompted to **refute by
default**. A finding is reported only if it survives a majority of the panel.
Review and verification run as a pipeline, so a dimension's findings start being
refuted the moment that dimension finishes.

**Why adversarial:** a single-pass reviewer emits plausible-but-wrong findings
(misread control flow, a "bug" the tests already cover, a convention it only
thinks is violated). Forcing each finding to earn its place against skeptics
strips those out, leaving a high-signal report.

**Args** (all optional):

| Arg | Default | Meaning |
|-----|---------|---------|
| `base` | `origin/main` | Git ref to diff against. |
| `votes` | `3` | Skeptics per finding; a finding dies on a majority refute. |
| `paths` | *(whole diff)* | Array of path prefixes to restrict the review to. |

**Returns** `{ base, filesReviewed, raised, refuted, confirmed[], report }` —
`report` is the ranked markdown; `confirmed[]` is the structured survivor list.

This is a **pre-PR / per-diff** review — the whole-diff counterpart to the
`reviewer` agent, with an adversarial verification stage bolted on. For the
whole-codebase drift audit use the `code-review-pass` skill; for a security
audit use `/audit`.
