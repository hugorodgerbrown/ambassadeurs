# ADR 0024 — LLM visibility: Content-Signal and llms.txt

**Status:** Accepted
**Date:** 2026-07-28

---

## Context

People increasingly find services by asking an assistant rather than a search
engine, and coding agents fetch URLs directly. The programme is promoted through
a Facebook community (ADR 0023), but "what is the 4 Vallées Ambassador Offer and
how do I find a partner?" is exactly the kind of question put to ChatGPT,
Claude, or Perplexity. If the answer omits us, the funnel never starts.

An audit against the emerging conventions for LLM-readable sites found the
baseline sound and the signalling absent:

- Pages are server-rendered Django templates. An agent that fetches a URL gets
  the prose, not a JavaScript bundle or an empty root — the common failure mode
  does not apply here.
- `robots.txt` (VERB-63) uses `User-agent: *` with path-scoped `Disallow` lines,
  so `GPTBot`, `ClaudeBot` and `PerplexityBot` are not blocked.
- No `llms.txt`, no Markdown representations, no `Link` alternates, no
  `Accept: text/markdown` negotiation.

None of these are committed standards. No provider has formally promised to read
`llms.txt`. They are implemented because the cost is near zero and the downside
of being unreadable is a lost registration.

## Decision

Ship the two pieces that stand alone; defer the five that depend on serving
Markdown.

### 1. `Content-Signal` in robots.txt

`core.views.robots_txt` emits, inside the `User-agent: *` group:

```
Content-Signal: search=yes, ai-input=yes, ai-train=no
```

The three signals are orthogonal. We permit **search** (appearing in search
results) and **ai-input** (being used as live context in an AI answer) — both
serve the purpose of the site, which is to be found by someone looking for a
partner. We refuse **ai-train**: there is no benefit to the programme in its
copy becoming model weights, and the site holds a Privacy Policy commitment to
data minimisation that sits awkwardly with an open training grant.

The directive is Cloudflare's (CC0) and is not in RFC 9309, so strict validators
warn about it. That is expected: RFC 9309 requires unknown lines to be ignored,
so the warning is cosmetic and the line is inert to crawlers that do not
implement it.

### 2. `/llms.txt`

`core.views.llms_txt` serves a curated Markdown index in the llmstxt.org format
— H1 site name, blockquote summary, prose context, then sectioned lists of
annotated links — as `text/markdown` (RFC 7763).

It is a README for AI-mediated conversations, **not** a second sitemap. It lists
the eight pages worth reading (home, how-it-works, FAQ, about, colophon, and the
three legal pages) and omits every transactional route, matching the exclusions
`robots.txt` already carries.

Three implementation choices worth recording:

- **Links are built with `{% url %}` and prefixed with the request's own scheme
  and host**, the same approach as the `Sitemap:` line. The file cannot drift
  out of step with the URLconf and needs no per-environment configuration.
- **Rendered via `render_to_string`, not `render(request, …)`.** The latter
  builds a `RequestContext` and runs every context processor, including the
  notifications one, which queries the database. A machine-facing document that
  touches no models should not carry a DB round-trip.
- **Not wrapped for translation.** Invariant 8 governs user-facing display
  copy. `robots.txt`, `sitemap.xml` and `llms.txt` are machine-facing documents,
  never rendered in the UI and never read by a human visitor, so they are
  authored in English like the rest of the codebase.

## Deferred

The remaining techniques all terminate in the same place — serving clean
Markdown per page — and are blocked on two unresolved questions:

1. **Markdown source of truth.** The content exists only as Tailwind-heavy
   Django templates with `{% blocktranslate %}` blocks. Exposing `/faq.md` means
   either converting the rendered HTML at request time (one source, costs a
   dependency) or maintaining parallel Markdown files (two content stores and
   two translation surfaces — the drift risk this project avoids elsewhere).
2. **Language-URL ambiguity.** There is no `i18n_patterns` and no `hreflang`.
   `/faq/` serves English or French depending on cookie and `Accept-Language`,
   and the French content has no distinct URL. A crawler receives one of the two
   with no signal about which, and no route to the other. Markdown
   representations would inherit the same ambiguity, so this is worth resolving
   first.

Deferred until both are answered: `.md` routes, `<link rel="alternate">` plus
the HTTP `Link` header, the visually-hidden Markdown pointer,
`Accept: text/markdown` negotiation (with q-value comparison, `Vary: Accept`,
and `406`), and `/llms-full.txt`.

## Rejected

Patterns that recur in blog posts and that no AI system reads. Do not
reintroduce them:

- `<meta name="ai-content-url">` / `<meta name="llms">` — no spec;
  `whatwg/html#11548` closed "not planned".
- `/.well-known/ai.txt`, `/ai.txt` — competing proposals, no adoption.
- HTML comments flagging an AI-readable version — parsers strip comments.
- Human/AI toggle buttons — agents do not click buttons.
- User-Agent sniffing to serve Markdown to bots — that is cloaking. Content
  negotiation on `Accept` is the legitimate mechanism.
- Schema.org / JSON-LD **added for LLM visibility** — controlled experiments
  show ChatGPT, Claude, Perplexity and Gemini ignore it. The site currently has
  none; this is not an instruction to remove structured data added for search.
- **DNS-AID agent-discovery records** (`_index._agents` / `_a2a._agents` SVCB) —
  see below. Reported as a finding by `isitagentready.com`; declined.

### DNS-AID, in full

The scanner reports "DNS for AI Discovery (DNS-AID) well-known entrypoint
records not found" and recommends publishing records of the form:

```
_a2a._agents.example.com. 3600 IN SVCB 1 agent.example.com. alpn="a2a" port=443
```

Declined, in order of weight:

1. **There is no agent to advertise.** That record means "connect here and
   speak A2A". The site hosts no A2A endpoint and no MCP server — it serves
   HTML and Markdown to humans and crawlers. Publishing the record would point
   a resolver-following agent at a connection that refuses. An absent record is
   honest; a dead one is a broken promise. Notably, the publication skill does
   not specify what must exist at the endpoint at all.
2. **The draft has no standing.** `draft-mozleywilliams-dnsop-dnsaid` is an
   individual submission, not adopted by dnsop or any working group, and the
   datatracker states it is "not endorsed by the IETF" with "no formal
   standing". It is at version 02, and was previously titled
   `draft-mozleywilliams-dnsop-**bandaid**`. RFC 9460 (SVCB/HTTPS) *is* a real
   standard, but that is the record **type**, not this use of it — citing the
   two together lends the draft borrowed credibility.
3. It is the same shape as everything else in this list: a proposal exists, a
   scanner checks for it, and no AI system reads it.

**Revisit if the project ever exposes a real agent surface** — an MCP server
over the matching queue, say. At that point the record would be true, and
publishing it would be worth doing.

One correction to the same scan: it also advised signing the zone with DNSSEC.
`skiparrainage.com` **is already signed** — there is a valid DS record
(`41374 8 2 01249D75…`) and RRSIGs are returned. That half of the finding is a
false negative; no action needed.

## Consequences

- The site states an explicit AI-usage policy rather than leaving it inferred
  from silence.
- An assistant asked about the Ambassador Offer has a single URL that explains
  what the service is and where the substance lives.
- `llms.txt` is one more artefact to keep current. It is generated from the
  URLconf, so route renames surface as test failures, but the annotations are
  hand-written and need review when a page's purpose changes.
- Any new public content page should be considered for inclusion, alongside the
  existing sitemap and PostHog journey updates.
