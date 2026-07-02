# ADR 0016 — Translation-catalogue maintenance is decoupled from feature work

**Status:** Accepted
**Date:** 2026-07-02
**Ticket:** _n/a (workflow change)_

---

## Context

The UI ships in English and French (Django i18n). Every feature that adds
user-facing copy wraps it in a translation function, which means the string
becomes a new `msgid` that must eventually land in `locale/en/LC_MESSAGES/django.po`
and `locale/fr/LC_MESSAGES/django.po` (and be compiled to `.mo` for production).

The previous policy (CLAUDE.md, "Internationalisation") told every author to run
`makemessages` on the branch that introduced the string, so the catalogues were
always current. In practice this produced constant, unavoidable merge pain:

1. **Every branch that adds copy edits the same two files.** With several
   feature branches open at once, each one runs `makemessages` and rewrites
   `django.po` for both locales. The extractions interleave new `msgid` blocks
   in the same regions, so the branches conflict on merge — over and over. This
   is recorded as a recurring cost (memory `parallel-po-merge-conflicts`).
2. **`--no-location` reduces but does not remove the churn.** Dropping the
   `#: file:line` comments (see the i18n section of CLAUDE.md) stopped the line
   numbers from shifting on every extraction, but add/add conflicts in the same
   `.po` region still happen whenever two branches add strings.
3. **A rebuild is mechanical bulk churn attached to unrelated review.** A
   `makemessages` run rewrites large spans of two generated files. Reviewing a
   feature diff that is 90% regenerated catalogue is noise, and it invites
   rubber-stamping the very files most likely to hide a bad merge.
4. **Nothing enforced it anyway.** `tox`, pre-commit, and CI never checked that
   the catalogues were current, so the "always run `makemessages`" rule was
   honoured unevenly — the worst of both worlds: churn when followed, drift when
   not.

The catalogues are a generated artefact whose churn is orthogonal to any one
feature — the same shape as a locked dependency file. We already treat
dependency bumps as their own single-purpose change (`uv lock --upgrade` in its
own PR), not as a rider on feature work.

## Decision

**Stop maintaining the translation catalogues inside feature branches. Rebuild
and translate them as a periodic, single-purpose task — like a dependency bump.**

### 1. Feature branches wrap copy but never touch the catalogues

Invariant 8 is unchanged: all user-facing copy must be wrapped in the i18n
functions (`gettext`/`gettext_lazy`, `{% translate %}`/`{% blocktranslate %}`).
That is what makes a string *translatable*, and it is enforced per PR by the
`reviewer` agent and the review checklists.

What changes: a feature branch **must not** run `makemessages` or
`compilemessages`, and **must not** edit `locale/*/LC_MESSAGES/django.po` or
`.mo`. A PR that modifies `locale/` is out of scope unless it is the dedicated
update-messages PR (below). Because production `gettext` falls back to the
English source string when a `msgid` is missing from the compiled catalogue, a
newly-added English string renders correctly in English the moment it ships; it
is only the French translation that waits for the next catalogue rebuild.

### 2. Catalogue rebuild is a dedicated single-purpose task

One task, one branch, one PR, touching only `locale/`:

```bash
uv run python manage.py update_messages          # extract + compile + report
```

`update_messages` (in `core/`) wraps `makemessages -l en -l fr --no-location`
followed by `compilemessages`, then reports the untranslated/fuzzy count per
locale. The task's human/agent step is to fill in the new French `msgstr`
entries between extraction and compilation. The resulting PR is titled like a
chore (`chore: update translation catalogues`) and contains only catalogue
changes, so it merges without competing with feature diffs.

### 3. The trigger is a count threshold, detected by the review machinery

Untranslated is defined as an empty `msgstr` **or** a `fuzzy`-flagged entry in a
committed `django.po`. Only the **non-source** locales are counted: the source
language (`settings.LANGUAGE_CODE`, English) has empty `msgstr` entries by design
— its `msgid` *is* the display text and `gettext` falls back to it — so its
catalogue is never "untranslated". Today that means the count is the French
backlog.

- `manage.py update_messages --check` counts the untranslated/fuzzy entries in
  the committed catalogues (it reads the `.po` files; it does **not** run
  `makemessages`) and exits non-zero when the total reaches the threshold.
- The threshold is `settings.I18N_UPDATE_MESSAGES_THRESHOLD` (env var
  `I18N_UPDATE_MESSAGES_THRESHOLD`, **default 10**). Below it, the catalogues
  are left alone — a rebuild for two strings is not worth the churn. At or above
  it, the backlog is worth a dedicated pass.
- The longitudinal `code-review-pass` audit (its `code-auditor` i18n check) runs
  `update_messages --check` each cycle. When the count is at/above the
  threshold and no open update-messages ticket already exists, it spins off an
  **"Update translation catalogues"** VERB ticket into `Ready for dev`.

Note the shift in what the review machinery checks. The per-PR `reviewer` no
longer verifies that a feature's new strings have matching catalogue entries —
that sync is now out-of-band by design. It only verifies the strings are
wrapped. The catalogue backlog is owned by the audit + the update-messages task.

### 4. A scheduled Routine executes the task on a cadence

A weekly scheduled Routine (separate from the weekly `code-review-pass`
Routine) runs the `update-messages` skill, which: checks the count against the
threshold, and — only when at/above it — branches, runs `update_messages`,
translates the new French entries, compiles, runs `tox`, opens the
single-purpose PR, and closes any open update-messages ticket. Below the
threshold it exits cleanly with no PR. This makes the task self-driving while
keeping the count threshold as the single gate shared by the audit, the CLI
`--check`, and the Routine.

## Consequences

- **No more parallel-branch `.po` conflicts from feature work.** Feature
  branches stop editing the catalogues, so the recurring add/add merge conflicts
  disappear. The only branch that edits `locale/` is the update-messages branch,
  and there is at most one open at a time (deduped by the ticket check).
- **Cleaner feature diffs.** Reviewers no longer wade through regenerated
  catalogue churn to find the actual change.
- **A bounded, visible translation backlog.** French can lag by up to a
  rebuild cycle, gated at the threshold. English never lags (source-string
  fallback). The backlog is surfaced as a ticket rather than silently drifting.
- **One canonical command and one threshold.** `update_messages` (with
  `--check`) is the single source of truth for both the rebuild and the count;
  `I18N_UPDATE_MESSAGES_THRESHOLD` is the single knob.
- **Trade-off: French translations are eventually-consistent.** A string
  shipped mid-cycle shows its English source to French users until the next
  rebuild. Below-threshold backlogs are deliberately left untranslated for a
  while. This is acceptable for a community program with a small, controlled
  string set; if French lag ever becomes user-visible-bad, lower the threshold
  (down to 1 for "translate everything immediately") or run the task on demand.
- **Deploy-time compilation is a separate concern (ADR 0015).** `.mo` files are
  gitignored (`.gitignore`: `*.mo`) and never committed; only the `.po` sources
  are tracked. How the compiled catalogue reaches production is settled
  separately by [ADR 0015](0015-compile-message-catalogues-at-deploy.md), which
  runs `compilemessages` in `build.sh`. This ADR governs only *when the `.po`
  catalogues are maintained*. The two compose: a merged update-messages PR
  changes the tracked `.po`, and the next deploy compiles it. The
  update-messages task also runs `compilemessages` locally to validate that the
  edited `.po` compiles.
