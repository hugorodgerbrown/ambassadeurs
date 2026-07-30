# ADR 0025 — Language-prefixed URLs

**Status:** Accepted
**Date:** 2026-07-30

---

## Context

The site ships in English and French, but until now both were served from the
*same* URL. `LocaleMiddleware` picked the language from the session cookie or the
`Accept-Language` header, so `/faq/` was English or French depending on who asked.
Verified against production before the change:

```
GET /faq/  Accept-Language: en  ->  <html lang="en">  "Frequently asked questions"
GET /faq/  Accept-Language: fr  ->  <html lang="fr">  "Questions françaises"
```

The response carried no `hreflang` and no `rel="alternate"`. Three consequences:

- **The French site could not be indexed.** No search engine or LLM can list a
  page it cannot link to, and no URL addressed the French content.
- **A crawler could not tell which variant it had received**, nor find the other.
- **Nobody could share a French link.** Pasting `/faq/` into the Verbier Facebook
  group — the programme's main channel — sends a French speaker to whatever their
  browser negotiates.

This also blocked the deferred LLM-visibility work ([ADR 0024](0024-llm-visibility-content-signal-and-llms-txt.md)):
Markdown representations would have inherited the same ambiguity, so there was no
point building them first.

## Decision

Adopt `i18n_patterns(..., prefix_default_language=False)`.

```
/faq/     -> English   (unchanged)
/fr/faq/  -> French    (new)
```

**The URL is authoritative for language.** `/faq/` is English even when the
browser sends `Accept-Language: fr`. That is the whole point: a stable, linkable,
indexable address per language.

`prefix_default_language=False` is what makes this cheap. The alternative moves
every English URL to `/en/…`, requiring redirects for every inbound link, a
sitemap rewrite, and rework of every PostHog funnel keyed on a URL. Keeping
English unprefixed means **no existing link breaks** — including signed match and
login links already sitting in people's inboxes.

### Which routes carry a prefix

*Unprefixed* — `healthz/`, `i18n/`, `sitemap.xml`, `robots.txt`, `llms.txt`,
`webhooks/stripe/`, `debug/`. None render translated content for a human, and
several are fetched by clients that must find them at a fixed, well-known path.
The language switcher (`i18n/`) is posted to in order to *change* the language,
so prefixing it with the current one would be circular.

*Prefixed* — `account/` and the whole `public.urls` catch-all.

The signed-token routes (match actions, registration confirmation, magic-link
login) sit **inside** the prefix so an emailed link can pin the recipient's
language. Tokens issued before this change carry unprefixed URLs, which still
resolve as English.

### Emailed links need their own override

`core.emails.send_templated_email` renders under `translation.override(language)`,
but that wraps only the **template render**. The URL is built by the caller and
passed in as context, so it is *not* covered.

Once the URL carries the language, an unprefixed link **pins the reader to
English** — which would have been a regression, since the old behaviour let
`LocaleMiddleware` negotiate French for a French reader. `matching/side_effects.py`
therefore builds each link through `_localised_url(view_name, language, *args)`,
which reverses under the recipient's `preferred_language`. These handlers run
from the *other* party's request or from the expire-matches cron, so the ambient
language says nothing about the recipient.

The `accounts/` flows are different and unchanged: they build their URL from
`request.build_absolute_uri()` while the user is in the browser, so the request's
own language is already correct.

### hreflang

`core.i18n.language_alternate_paths` maps any path to its full set of language
variants plus `x-default`; the `language_alternates` template tag makes them
absolute and `_meta.html` emits them on every page. The mapping is symmetric —
`/faq/` and `/fr/faq/` advertise the same set — which is what lets a crawler
arriving at either one find the other.

The helper resolves the path under whichever language *matches* it rather than
under the active one. In a request those coincide, because `LocaleMiddleware`
activates the language named by the prefix. Not depending on that keeps the
function correct outside the request cycle and unit-testable: `resolve()` matches
`i18n_patterns` against the active language, so `/fr/faq/` does not resolve while
English is active.

The sitemap uses Django's native `i18n` / `alternates` / `x_default` attributes,
so it emits both variants with `xhtml:link` alternates without custom code.

### The language switcher needs a resolved `next`

The footer switcher was one form with a submit button per language, posting a
single `next="{{ request.path }}"`. That breaks under the prefix, and it breaks
*silently*: `set_language` translates `next` with `translate_url`, which resolves
the path under the active language — and the POST lands on the unprefixed
`/i18n/setlang/`, where the active language is whatever was negotiated. Resolving
`/fr/faq/` under English fails, `translate_url` returns the path unchanged, and
the view redirects the user straight back to the page they were trying to leave.
French → English did nothing at all.

The footer now renders **one small form per language**, each carrying that
language's already-resolved path as `next`. The destination is computed by the
same helper that feeds the `hreflang` tags, so there is one source of truth for
"where does this page live in language X". `set_language` still runs its own
`translate_url` over the value, which is either a no-op or a failure that leaves
the correct URL intact — so the switcher no longer depends on that call
succeeding.

Keeping the POST (rather than making the switcher a plain link to the alternate
URL) preserves the language cookie, so a returning visitor keeps their choice.

## Consequences

- French is addressable, linkable and indexable for the first time.
- `$current_url` becomes `/fr/…` for French traffic. Funnels keyed on an exact
  URL need updating — instrumentation and funnels are a coupled pair.
- Steps 3–7 of ADR 0024 are unblocked. Markdown routes can now follow the same
  shape (`/faq.md`, `/fr/faq.md`).
- **New public pages get two URLs, not one.** Anything added to the sitemap or to
  `llms.txt` should be considered in both languages.
- `AdminHostMiddleware` swaps `request.urlconf` per request, and Django requires
  `i18n_patterns` to live in the root URLconf. Each swapped-in module is a root
  URLconf for its own request, so this holds — covered by tests rather than
  assumed.

## Alternatives rejected

- **`prefix_default_language=True`** (`/en/…` and `/fr/…`) — symmetrical, but
  moves every existing URL for no gain in discoverability.
- **Splitting language on the Markdown layer only** — leaves the HTML site, which
  is what search engines actually read, with the original ambiguity.
- **Adding `hreflang` without distinct URLs** — incoherent: `hreflang` names the
  URL of each variant, so it requires the variants to have URLs.
