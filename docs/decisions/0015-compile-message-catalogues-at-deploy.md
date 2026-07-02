# ADR 0015 — Compile gettext catalogues at deploy time

**Status:** Accepted
**Date:** 2026-07-02

---

## Context

The UI ships in English (default) and French via Django i18n. Translation
sources live in `locale/en/LC_MESSAGES/django.po` and
`locale/fr/LC_MESSAGES/django.po`, both tracked in git.

Django reads translations from **compiled** `.mo` catalogues, not the `.po`
sources. When no `.mo` is found for a locale, `gettext` falls back to the source
(English) string. Compiled catalogues are a build artefact: `*.mo` is gitignored
(`.gitignore`), so no `.mo` is committed.

The Render build step (`build.sh`) ran `collectstatic` and `migrate` but never
`compilemessages`. The consequence: **no `.mo` catalogue existed at runtime in
production, so every locale — French included — served the untranslated English
source string.** The French translation catalogue was complete (VERB-68) but was
never actually served. The same gap affects the cron services
(`expire_matches`, `run_matching`, `close_season`), whose notification emails
render translated copy.

This mirrors the test-environment behaviour already documented: the `tox -e
test` env compiles no catalogues, so tests must never assert translated copy.
The difference is that production is *meant* to serve French, and did not.

## Decision

Compile the catalogues during the deploy build. `build.sh` now runs, before
`collectstatic`:

```bash
uv run python manage.py compilemessages -l en -l fr
```

`build.sh` is shared by the web service and all cron services in `render.yaml`,
so a single line covers every service — including the crons that send
translated emails. `compilemessages` requires the gettext `msgfmt` binary, which
is present in Render's native Python build image.

Catalogues stay uncompiled in git (`*.mo` remains gitignored). Compilation
happens fresh on every deploy from the committed `.po` sources, so the compiled
output never drifts from source and never needs merge-conflict resolution.

## Consequences

- French is served in production. Both locales resolve against real catalogues
  rather than falling back to the English source.
- Notification emails from the cron services render in the recipient's
  preferred language.
- The deploy depends on `msgfmt` being on the build image's PATH. If a future
  Render runtime change drops gettext, `compilemessages` fails the build loudly
  rather than silently shipping English — the failure mode is fail-fast, not
  silent regression.
- A malformed `.po` (bad placeholder, syntax error) now fails the deploy build.
  Guard against this by keeping `.po` sources valid; `msgfmt --check` locally
  catches it before push.
