# ADR 0026 — Markdown representations of content pages

**Status:** Accepted
**Date:** 2026-07-30

---

## Context

[ADR 0024](0024-llm-visibility-content-signal-and-llms-txt.md) shipped the two
LLM-visibility pieces that stand alone and deferred the rest behind two
questions. [ADR 0025](0025-language-prefixed-urls.md) answered the second (French
now has its own URL). This decision answers the first — where the Markdown comes
from — and implements steps 3 to 6 of that audit.

Those four steps are one unit. The `.md` routes are the substance; the `<link>`
tag, the HTTP `Link` header, the hidden pointer and `Accept` negotiation exist
only to tell clients the routes are there. Shipping any of them without the
routes would advertise nothing.

## Decision

### Convert the rendered HTML; do not keep Markdown files

The content exists only as Django templates full of `{% blocktranslate %}`
blocks. Two ways to serve Markdown:

1. **Hand-written `.md` files.** Exact control over the output, but a second
   content store and a second translation surface. Every copy edit becomes two
   edits in two formats, and the French catalogue would need to cover both.
2. **Convert the rendered page at request time.** The template stays the single
   source of truth and a copy edit reaches both representations at once. Costs a
   dependency, and the output quality depends on the content markup.

Chose conversion. Drift between two copies of the same prose is the failure this
project avoids everywhere else, and it is the failure nobody notices — a stale
`.md` file looks fine until someone reads it against the page.

The `.md` view runs the page's **real view** through the resolver rather than
re-rendering its template, so the Markdown reflects whatever context the view
builds (the homepage's queue snapshot, for instance) instead of quietly
diverging from the page it claims to represent.

### Three fixes on top of stock markdownify

Each was found by converting the real FAQ page, not by reading the markup:

- **`<summary>` becomes an h3.** The FAQ is built from `<details>`/`<summary>`
  accordions. Stock conversion renders each question as inline text, so every
  question ran into its own answer as one paragraph. The questions *are* the
  structure of an FAQ.
- **Fragment-only anchors are dropped.** Each question carries a hover permalink
  (`<a href="#anchor">#</a>`) that converted to a stray `[#](#anchor)`, and then —
  after a first fix that kept the link text — to a bare `#` on its own line. The
  rule distinguishes decorative anchors (text is just `#`) from real in-page
  links, whose words are kept.
- **Relative links become absolute.** A `.md` file is fetched and read on its
  own, often by an agent with no notion of the origin, so `/how-it-works/` has to
  become a full URL to stay followable.

`<main>` is converted, plus any element marked `id="hero"`. Nav, footer,
language switcher and notification strip are chrome: repeated on every page and
pure noise to a reader who asked for the content.

The `#hero` exception is not cosmetic. The homepage's hero sits outside `<main>`
so it can break out of the layout column, but it holds the page's `<h1>` and
opening pitch — the sentences an assistant needs to answer "what is this
service?". Converting `<main>` alone left `/index.md` headless, which was found
by reading the generated bundle rather than by any test. The id is now a
documented contract between the template and the converter; renaming it silently
truncates the homepage.

Links and image sources are absolutised in one pass over each fragment, so both
`/how-it-works/` and `/static/images/hero.jpg` resolve for a reader who has only
the file.

### One registry for the page list

Three consumers now need "which pages are public content": the sitemap, the
`.md` routes, and `llms.txt`. That earns a single definition
(`public/content.py`), which the sitemap and the `.md` routes read directly.

`llms.txt` stays **hand-written**. Its per-page annotations are editorial, and
the llmstxt.org format is a curated README rather than a generated index —
generating it would defeat the point. A test asserts it links exactly the
registry's pages, so the two cannot drift apart silently.

### Negotiation lives in middleware, on the response

`MarkdownRepresentationMiddleware` runs on the way out, which lets it convert
the HTML the view already produced rather than rendering the page a second time.

Django's `HttpRequest.accepts()` was not usable: it answers "is this type
acceptable at all" and ignores q-values, when the whole question here is which
of two acceptable types the client *prefers*. `core/negotiation.py` implements
the comparison, with three rules that each guard a specific failure:

- **Compare q-values, never substring-match.** `text/html, text/markdown;q=0.5`
  means the client would take Markdown but wants HTML. A substring check for
  `text/markdown` ships Markdown to a browser.
- **Ties go to Markdown — but only when the client named it.** Coding agents
  commonly send both at q=1, so a strict `>` sends them HTML. A browser's `*/*`
  also ties, via the wildcard, so `>=` alone would flip the entire site to
  Markdown. Both are needed: `>=` plus an explicitness guard.
- **Specificity beats header order.** An exact `text/markdown` outranks `text/*`,
  which outranks `*/*`, wherever they appear in the header.

A client that accepts neither type gets **406**, not a silent substitution of
something it said it did not want. The `.md` URLs skip that check: an explicit
URL overrides negotiation.

`Vary: Accept` is set on both representations so caches keep them apart.

**This is not cloaking.** Cloaking is serving crawlers different *content* from
users. This serves two representations of the same content, selected by a header
the client controls, with `Vary` declared — how HTTP has worked since 1997.

## Consequences

- Every content page is available as clean Markdown at a predictable URL, in
  both languages: `/faq.md` and `/fr/faq.md`.
- Adding a public content page means adding one registry entry; the sitemap and
  `.md` route follow, and the llms.txt test fails until the index is updated.
- Conversion happens per request. The pages are small and query-free (bar the
  homepage snapshot), so this is cheap, but it is not free — if it ever matters,
  cache on the response rather than reintroducing stored Markdown files.
- The output is only as good as the content markup. A template that abandons
  semantic elements for styled `<div>`s will convert badly, and the failure is
  silent. The conversion tests pin the FAQ's structure as the canary.
- `markdownify` and `beautifulsoup4` are now runtime dependencies.

## `/llms-full.txt`

Added in SKI-156 on the same conversion path: the eight pages concatenated, each
section preceded by an HTML comment naming its source URL so a quoted passage can
be traced back. Roughly 20KB — small enough to be genuine rather than
performative, which is the objection to bundling a large marketing site.

It sits **inside** the language prefix, unlike `llms.txt`. The line: machine-
facing metadata is unprefixed (`robots.txt`, `sitemap.xml`, `llms.txt` — indexes
of links); translated prose is prefixed. So `/llms-full.txt` is English on the
conventional root path and `/fr/llms-full.txt` is French, which is the point of
having given French a URL at all.

One request renders every content page — the most expensive endpoint on the
site. Crawlers fetch it rarely, so the cost is accepted rather than cached, but
it is the first thing to cache if it ever shows up as a hot path.
