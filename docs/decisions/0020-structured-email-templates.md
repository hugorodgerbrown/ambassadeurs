# ADR 0020 — Structured, translatable email templates (text + HTML)

**Status:** Accepted
**Date:** 2026-07-03
**Ticket:** VERB-108

---

## Context

Every outgoing email in the codebase used one of two ad-hoc patterns:

1. **`accounts/services.py`** (`send_login_email`, `send_already_registered_email`,
   `send_confirmation_email`) rendered a flat pair of templates —
   `templates/email/<name>_subject.txt` / `<name>_body.txt` — via `render_to_string`,
   then called `django.core.mail.send_mail`.
2. **`matching/side_effects.py`** (the six `_email_*` render helpers behind the
   match-transition notifications, ADR 0018) built subject and body as inline
   `gettext()` calls in Python, interpolated with `%`, then also called `send_mail`.

Both patterns are plain-text only — there is no HTML alternative — and the
second keeps user-facing copy in Python rather than in a template, which is
harder for a non-developer to review or edit and mixes translation-string
maintenance with transition logic.

### Alternative considered: `django-appmail`

`django-appmail` stores email templates as database rows (subject, text body,
HTML body per language) editable via the admin, with a `send_email(name, ...)`
helper. It was rejected for this project:

- **Extra dependency and a DB-seeding mechanism.** The templates would need to
  be created via a migration or a management command and kept in sync with
  code changes across environments (dev, staging, production) — one more thing
  to seed and one more thing that can drift from the code that renders it.
- **No English fallback for a missing French row.** Django's own `gettext`
  falls back to the English source string when a `msgid` has no French
  `msgstr` yet (this is exactly how ADR 0016's decoupled catalogue maintenance
  works — a string ships in English immediately and gets translated on the
  next scheduled rebuild). `django-appmail`'s per-language template rows have
  no equivalent: a template not yet translated into French either 404s or
  requires bespoke fallback logic to be built and maintained.
- **`LoggedMessage` would put contact PII in the database.** `django-appmail`
  optionally logs every rendered send (`LoggedMessage`) for admin visibility.
  Several of this project's emails carry the counterpart's name, email, and
  phone (the `match_confirmed` reveal, Invariant 1) — logging the rendered
  body would duplicate that contact PII into a second, less-guarded table for
  no product benefit.

A file-based template convention plus one shared Python helper avoids all
three: templates are reviewed and merged like any other code, ship with the
same English-fallback behaviour every other translated string already has, and
nothing renders to a database row.

## Decision

**One convention, one shared helper.** Every email is a template triple under
`templates/email/<name>/`:

- `subject.txt` — a single `{% translate %}`/`{% blocktranslate %}` line.
- `body.txt` — the plain-text part, wrapped in `{% autoescape off %}` (see
  below).
- `body.html` — the HTML part, extending a shared `templates/email/base.html`
  wrapper (a minimal single-column, inline-styled layout with no logo asset;
  the same neutral "— 4 Vallées Ambassador Offer" sign-off as the text
  parts).

`core.emails.send_templated_email(name, context, to, language=None)` renders
all three and sends a `django.core.mail.EmailMultiAlternatives` with the HTML
part attached via `attach_alternative(html, "text/html")`. The subject is
collapsed to a single line (`" ".join(rendered.split())`) to strip the leading
newline `{% load i18n %}` leaves and to close off any header-injection vector
from a multi-line rendered subject. Neither `accounts/services.py` nor
`matching/side_effects.py` builds subject/body strings by hand any more —
`accounts/services.py` calls the helper with no `language` (rendering in the
active request language, unchanged from before), and each `_email_*` helper in
`matching/side_effects.py` calls it with
`language=registration.preferred_language or settings.LANGUAGE_CODE`, exactly
as the previous `translation.override(lang)` block did.

The three existing accounts templates moved into the convention via `git mv`
(`login/`, `already_registered/`, `confirmation/`) with their `subject.txt` /
`body.txt` content unchanged, so their `msgid`s stay stable. The six matching
notifications (previously inline `gettext()` in Python) got new directories
with copy ported verbatim into `{% blocktranslate trimmed %}` blocks:
`match_proposed`, `partner_accepted`, `match_confirmed`, `requeued`,
`window_expired`, `no_show`.

### Deliberate: `{% autoescape off %}` in every `body.txt`

Plain text is not HTML — a name like `O'Brien` or a phone number containing
`&` should render literally, not as `O&#x27;Brien`. Every `body.txt` (the
three moved accounts templates and the six new matching ones) wraps its
content in `{% autoescape off %}`. This is a plain-text-only concern: every
`body.html` keeps Django's default autoescaping (Invariant 4 — no
`mark_safe`/`|safe` on user-supplied content), since HTML escaping is correct
there. **Do not "fix" the text-part `{% autoescape off %}` later** — removing
it reintroduces HTML entity-escaping into a channel that is never rendered as
HTML.

## Consequences

- **Every outgoing email is now multipart.** Text and HTML alternatives are
  built from the same context in one call, so they cannot drift into
  inconsistent copy.
- **All email copy lives in templates, not Python.** The six matching
  notifications are reviewable and editable the same way as the three accounts
  emails always were.
- **Msgid churn for the six matching emails.** Moving their copy from Python
  `_()` calls into `{% blocktranslate %}` template blocks changes those
  `msgid`s. Per ADR 0016, this branch does not run `makemessages` — the French
  translations for these six emails go stale until the next scheduled
  catalogue rebuild, same as any other new/changed string.
- **One call site, one behaviour.** `send_templated_email` is now the only
  path by which the codebase sends an email; a future email type is a new
  template directory plus one call, not a new `render_to_string` +
  `send_mail` pair.
