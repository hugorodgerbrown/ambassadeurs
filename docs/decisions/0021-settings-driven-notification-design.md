# ADR 0021 — Settings-driven notification design, decoupled from ordering

**Status:** Accepted
**Date:** 2026-07-07
**Ticket:** VERB-123

---

## Context

`Notification.priority` (VERB-109) was a single `IntegerChoices` field
(`NEUTRAL/LOW/NORMAL/HIGH`) doing two unrelated jobs:

1. **Stacking order** in the strip — `Meta.ordering = ["-priority", "-created_at"]`.
2. **Visual styling** — `priority_tone` mapped each value to a semantic string
   (`neutral/low/normal/high`) emitted as `data-priority`, which
   `src/css/main.css` mapped to one of four fixed colour pairs.

Adding a new banner look, or changing which look staff could pick without also
changing the stacking semantics, required a code change (new
`IntegerChoices` member, a migration, and a new CSS rule) even though the
product need was purely cosmetic. Program staff wanted more banner styles
without a schema change each time, and wanted to force a notice to the top of
the strip independently of how it looked.

## Decision

**Split the field into two, one settings-driven and one plain:**

- **`Notification.weight`** — a plain `IntegerField` (default `0`, higher
  sorts first). `Meta.ordering = ["-weight", "-created_at"]`. This is the only
  ordering axis; it carries no styling meaning.
- **`Notification.design`** — a free-form `CharField` (no model-level
  `choices=`) naming a key into a new `settings.NOTIFICATION_DESIGNS` dict.
  Each `NotificationDesign` (a `NamedTuple`) carries `label`, `description`
  (both `gettext_lazy`-wrapped, staff-facing) and `css_classes` — one or more
  class names appended to the banner's `class="…"` attribute.

This mirrors the existing `settings.CUSTOM_NOTIFICATION_GROUPS` /
`Notification.custom_group_key` pattern (VERB-109): a `CharField` validated in
`core.admin.NotificationForm.clean()` against the settings dict's keys, with
the admin's `design` `ChoiceField` populated from
`sorted(settings.NOTIFICATION_DESIGNS)` in `NotificationForm.__init__`.
Adding, renaming, or retiring a design is now a settings edit — no
model/migration change — exactly as adding a custom notification group
already was.

### Why no model-level `choices=`

Django evaluates `choices=` at class-definition (import) time. `settings.py`
is not guaranteed to be fully configured when models are first imported (the
same reasoning that keeps `CUSTOM_NOTIFICATION_GROUPS`'s lazy, in-function
model imports out of the settings module). A model-level `choices=` sourced
from `settings.NOTIFICATION_DESIGNS` would freeze the choice list at import
time and require a process restart — or worse, silently diverge — whenever
the dict changed. Validating in the admin form's `clean()` instead re-reads
the current dict on every request.

### Migrating existing rows

Existing `priority` values are ported by an explicit int → design-key table in
the migration's `RunPython` step (not by importing the now-deleted
`Notification.Priority`, which the same migration removes):

| Old `priority` (`Notification.Priority`) | New `design` |
|---|---|
| `0` (`NEUTRAL`) | `INFO` |
| `1` (`LOW`) | `MUTED` |
| `2` (`NORMAL`) | `NOTICE` |
| `3` (`HIGH`) | `URGENT` |

The same migration copies the old `priority` integer into `weight` so
existing rows keep their current stacking order unchanged. The four seed
`NOTIFICATION_DESIGNS` entries (`INFO`/`MUTED`/`NOTICE`/`URGENT`) reproduce the
four colour pairs previously hard-coded in
`.notification-banner[data-priority="…"]`, via four new component classes in
`src/css/main.css` (`.notification-info`/`-muted`/`-notice`/`-urgent` — see
"No inline `style=`" below), so the migration is a like-for-like visual swap
with no observable change to any existing notification.

### No inline `style="…"` — production CSP forbids it

The first version of this change gave each `NotificationDesign` both
`css_classes` and a `css_styles` string rendered into the banner's
`style="…"` attribute. This was rejected in review: `CSP_DIRECTIVES["style-src"]`
in `config/settings/base.py` has no `'unsafe-inline'` (only `SELF`, the Google
Fonts stylesheet origin, and the `templates/500.html` inline `<style>` block's
hash), and production enforces the policy (`development.py` only runs it
report-only, which is why the dev/e2e suite did not catch the problem). An
element-level `style="…"` attribute is exactly what `style-src` without
`'unsafe-inline'` exists to block — the browser would have silently dropped
every notification's colour in production.

`NotificationDesign` therefore carries only `css_classes`; each design names
one of four new component classes in `src/css/main.css`
(`.notification-info`, `.notification-muted`, `.notification-notice`,
`.notification-urgent`), each reproducing one of the four colour pairs. These
rules are compiled by the normal Tailwind build like any other class in
`main.css` — no CSP exception, and no Tailwind JIT/content-scan concern, since
they are plain CSS selectors rather than dynamically generated utility
classes.

### Why fresh design keys, not a 1:1 rename

`INFO`/`MUTED`/`NOTICE`/`URGENT` were chosen to describe what each design
*looks and reads like*, not to echo the old `NEUTRAL`/`LOW`/`NORMAL`/`HIGH`
priority language — the old names conflated "how urgent" with "how it looks",
which is exactly the coupling this ADR removes. Future designs are free to use
whatever descriptive name fits (e.g. a seasonal or promotional look) without
having to fit an urgency scale.

## Consequences

- **Two independent knobs.** Staff can force a notice to the top (`weight`)
  without changing its colour (`design`), and vice versa.
- **New looks are a settings + review cycle, not a migration.** A developer
  adds an entry to `NOTIFICATION_DESIGNS`; it is immediately selectable in the
  admin `design` dropdown.
- **`css_classes` is developer-authored, not translated.** Only
  `label`/`description` are wrapped in `gettext_lazy`; `css_classes` is
  rendered as-is (Django's normal auto-escaping applies — Invariant 4 is not
  implicated, since it never originates from user input).
- **No inline styling, ever.** Every design's look must be expressible as a
  CSS class in `src/css/main.css`. This is a hard constraint, not a
  convenience: production's CSP blocks inline `style="…"` outright.
- **A design key can go stale.** If a key is removed from
  `NOTIFICATION_DESIGNS` while a `Notification` row still references it, the
  model's `design_label`/`design_description`/`design_classes` properties
  fall back to an empty string rather than raising — the banner renders with
  no extra class rather than a 500.
