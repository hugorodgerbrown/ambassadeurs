---
name: update-messages
description: |
  Run the single-purpose translation-catalogue update task: extract new strings
  with makemessages, fill in the French (and any English) translations, compile,
  run tox, and open a PR touching only `locale/`. This is the decoupled
  catalogue rebuild (ADR 0016) — the counterpart to a dependency bump, kept off
  feature branches to avoid parallel-branch `.po` merge conflicts. Use when the
  user says "update the translation catalogues", "run makemessages", "translate
  the missing strings", or picks up an open "Update translation catalogues"
  ticket. Also driven by a weekly Routine — when invoked with `routine`
  (or `weekly` / `--no-approval`) in the args, runs end-to-end with no approval
  gate. Do NOT use to add copy for a feature (wrap strings in the feature PR and
  leave the catalogues alone) or for a per-PR review.
user-invocable: true
allowed-tools: Bash, Read, Edit, Write, Grep, Glob, mcp__linear
---

# Update messages

The **single-purpose translation-catalogue rebuild**. One branch, one PR,
touching only `locale/`. It exists so feature branches never run `makemessages`
and never edit `.po` files — that is what caused the recurring parallel-branch
`.po` merge conflicts this task removes. See
[ADR 0016](../../docs/decisions/0016-decoupled-catalogue-maintenance.md).

**Prerequisite:** the GNU gettext binaries (`xgettext`, `msgfmt`) must be
installed — `makemessages`/`compilemessages` shell out to them. If they are
missing, stop (interactive) or exit non-zero (routine) with a clear message.

## Modes

Two modes; the work is identical, only the approval-and-ship path differs.

### Interactive mode (default)

A human asked. Do the extract + translate, then present the diff (the filled-in
`.po` entries) **for approval via plan mode** before running tox, pushing, and
opening the PR.

### Routine mode

Invoked from the weekly scheduled Routine. **Trigger phrases** — any of:
`routine`, `weekly`, `--no-approval` in the args, or a scheduled-task header.

In routine mode: skip the approval gate and run the full flow end-to-end
(check → branch → extract → translate → compile → tox → push → PR). Exit
non-zero on any unrecoverable error (gettext missing, tox still red after a fix
attempt, push/PR failure) so the runtime surfaces it. Never push a red branch.

## Step 1 — Gate on the threshold

```bash
uv run python manage.py update_messages --check
```

This counts untranslated/fuzzy entries in the committed catalogues and exits
non-zero once the total reaches `settings.I18N_UPDATE_MESSAGES_THRESHOLD`
(default 10). **If it exits zero (below threshold), stop** — a rebuild for a
handful of strings is not worth the churn. In routine mode this is a clean,
successful no-op; say so and exit. Proceed only when it exits non-zero.

Exception: an explicit human request ("translate everything now") overrides the
gate — proceed even below the threshold.

## Step 2 — Resolve or create the ticket, then branch

The project convention is one ticket per branch.

1. Search Linear (`list_issues`, Ambassadeurs team, open states) for an open
   **"Update translation catalogues"** ticket (the `code-review-pass` audit may
   have already spun one off). Also check for an existing open update-messages
   branch/PR — there must be at most one in flight; if one exists, stop.
2. If a ticket exists, use its `VERB-NN`. If none exists, create one with
   `save_issue` (title `Update translation catalogues`, state `Ready for dev`,
   description referencing ADR 0016), and use the new `VERB-NN`.
3. From a clean tree on an up-to-date `main`, create
   `chore/VERB-NN-update-translation-catalogues`. Move the ticket to
   `In Progress` (no push yet).

## Step 3 — Extract and compile

```bash
uv run python manage.py update_messages
```

This runs `makemessages -l en -l fr --no-location` (the `--no-location` flag is
mandatory — it keeps the `#: file:line` churn out of the `.po` files) then
`compilemessages`, and reports the per-locale untranslated/fuzzy counts.

## Step 4 — Fill in the translations

Edit `locale/fr/LC_MESSAGES/django.po` (and `locale/en/` if any English entry is
empty). For every empty or `fuzzy` `msgstr`:

- Write a natural, correct French translation. Match the tone of the
  surrounding UI copy (see the app's existing French entries).
- **Preserve every placeholder and markup token exactly** — `%(name)s`,
  `%s`, `{count}`, `<a href="…">`, HTML tags, and leading/trailing whitespace.
  A reordered or dropped placeholder is a runtime error.
- Handle plural forms (`msgstr[0]`, `msgstr[1]`) per French pluralisation.
- Remove the `#, fuzzy` flag from each entry once you have confirmed or
  corrected its translation.
- Do not translate the 4 Vallées-neutral service branding or the named
  application contact (Téléverbier) — see CLAUDE.md.

## Step 5 — Recompile and re-check

```bash
uv run python manage.py compilemessages
uv run python manage.py update_messages --check
```

`--check` should now exit zero (or report a much lower count). Any remaining
entries you intentionally left untranslated should be noted in the PR body.

## Step 6 — Verify green

```bash
uv run tox
```

Must be all green. Note: the test env compiles no `.mo` catalogues and `gettext`
falls back to the source string, so tests never assert translated copy — a
correct `.po` change should not affect them. If tox is red, the cause is almost
certainly unrelated (a malformed `.po` that fails `compilemessages`, or a
pre-existing failure). Fix a malformed `.po`; otherwise stop (interactive) or
exit non-zero (routine). Never push a red branch.

## Step 7 — Commit, push, open the PR

- **Stage only `locale/`.** The diff must contain nothing but catalogue changes.
  If `makemessages` picked up unrelated churn, investigate before committing.
- Commit subject `VERB-NN: update translation catalogues`.
- Push and open a PR titled `VERB-NN: update translation catalogues`. The body
  starts with `Closes VERB-NN`, and lists the before/after untranslated counts
  per locale and anything intentionally left untranslated. Reference ADR 0016.

## Step 8 — Report

Report the ticket, the PR URL, and the before/after counts. In routine mode,
this is the notification payload.

## Guardrails

- **Only `locale/` changes.** If you find yourself editing source, templates, or
  settings, you are out of scope — stop.
- **One in flight.** Never open a second update-messages PR while one is open.
- **`--no-location` always.** Never regenerate the catalogues without it.
- **Never leak secrets** into the ticket, PR, or commit.
