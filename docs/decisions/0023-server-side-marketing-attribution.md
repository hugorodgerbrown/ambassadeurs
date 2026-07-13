# ADR 0023 — Server-side marketing-source attribution

**Status:** Accepted
**Date:** 2026-07-13

---

## Context

Product analytics (VERB-124) sends events to PostHog
**server-side and cookieless**: `core.observability.capture_event` wraps
`posthog.capture`, and page-views send only `$current_url`. We do not run the
PostHog JavaScript SDK, and the [Cookie Policy](../../templates/public/legal/cookies.html)
advertised that the site sets no analytics cookies.

The registration and end-to-end funnels (VERB-146) therefore could not break
down by **acquisition source**. PostHog's UTM/click-ID attribution is a feature
of the *client-side* JS SDK, which parses `utm_*` and click IDs (`fbclid`,
`gclid`, …) from the querystring into event and person properties. The Python
SDK does none of that — so on our stack the source was only ever a substring of
`$current_url`, not a queryable property. The programme is promoted mainly
through the "Verbier" Facebook community, so *where a registration came from* is
a question worth answering.

## Decision

Capture the source server-side with **django-utm-tracker** (our own library),
and bridge a **derived** source into the `registration` PostHog event.

### 1. Capture + persistence (django-utm-tracker)

`utm_tracker` is added to `THIRD_PARTY_APPS`. Its `LeadSourceMiddleware` persists
a durable per-user `LeadSource` row (first-party, in our own DB) once a visitor
authenticates. We do **not** use its `UtmSessionMiddleware` directly — see below.

### 2. Normalise click-ID-only visits

`LeadSource` creation requires `utm_source` **and** `utm_medium`. Organic
Facebook traffic — our main channel — arrives as a bare `?fbclid=…` with no
`utm_*` params, which the library silently drops. So
`core.middleware.MarketingSourceMiddleware` replaces `UtmSessionMiddleware`:
it **normalises then stashes**, synthesising `utm_source`/`utm_medium` from a
click ID when the visitor supplied no `utm_source`
(`core.marketing.CLICK_ID_SOURCES`: `fbclid → facebook/social`,
`gclid → google/cpc`, `msclkid`/`aclk → bing/cpc`, `twclid → twitter/social`).
Both the `LeadSource` row and the PostHog event then see a consistent source.

### 3. Derived-source-only to PostHog

Raw click IDs are opaque per-click identifiers an ad platform can tie back to a
person. They **must not** reach PostHog, a third-party processor.
`core.marketing.marketing_event_properties` reads the session's stashed params
and returns only the derived `source` + non-empty `utm_*`, plus a `$set_once`
first-touch sub-dict; it never emits `fbclid`/`gclid`/etc. Raw click IDs may
persist only in the first-party `LeadSource` row.

The bridge runs where both the session and the event origin exist:
`public.services.register_or_resend_participant` computes the properties and
passes them to `matching.services.register_participant` (which has no request
access) as `marketing_properties`, merged into the `registration` event. The
existing `alias_identities` call on the same path stitches the anonymous
pre-registration page-views onto the new user, so `$set_once` lands on the right
person.

### 4. Functional session cookie

Stashing params in `request.session` sets a first-party `sessionid` cookie for
anonymous visitors who arrive with tracking params (previously only
authenticated users got one). This is accepted as a strictly-functional
first-party cookie; the [Cookie Policy](../../templates/public/legal/cookies.html)
copy is updated to say so truthfully — the cookie may now be set on arrival, and
records the referring channel for first-party analysis only, with **no**
third-party, advertising, or cross-site tracking cookies.

## Consequences

- The funnels can break down by `source`; organic Facebook is captured in both
  the `LeadSource` table and PostHog.
- **Cross-device attribution is lost by design.** The magic-link login is
  deliberately cross-device (land on phone, confirm on desktop); the session —
  and the source it holds — does not travel, so those conversions attribute to
  nothing. Same-browser conversions attribute correctly.
- `core.observability.scrub_pii` recursively scrubs the whole `properties` dict,
  including the nested `$set_once`; a long numeric `utm_term`/`utm_campaign`
  could be mangled by the broad phone regex. Accepted as belt-and-braces.
- The Cookie Policy wording is legally sensitive and flagged for review; the
  copy may change without affecting the code.
- We depend on stable `utm_tracker` internals (`parse_qs`, `stash_utm_params`,
  `SESSION_KEY_UTM_PARAMS`) — imported, not copied, so a breaking version bump
  surfaces at import time.
