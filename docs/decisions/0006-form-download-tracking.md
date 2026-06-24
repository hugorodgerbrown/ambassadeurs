# ADR 0006 — Form-download tracking via DB rows

**Status:** Accepted  
**Date:** 2026-06-24  
**Ticket:** VERB-15

---

## Context

The "How it works" page links to an application-form PDF. Programme staff
want to know how many visitors download the form — a leading indicator of
conversion before the off-app application step. The project has no analytics
stack (no Google Analytics, Plausible, or equivalent); adding one is out of
scope for launch.

Three options were considered:

1. **Static file access log** — read from WhiteNoise / web-server access logs.
   Works without code, but requires infrastructure access and produces noisy
   output (bots, assets, CDN checks). Not queryable from the Django admin.

2. **DB row per download** — a redirect view creates one `FormDownload` row
   per request; `created_at` is the only data point. Queryable from the
   Django admin by date, no extra infrastructure.

3. **Third-party analytics pixel** — adds an external dependency and
   complicates the cookie policy before launch.

---

## Decision

Use option 2: a `FormDownload(BaseModel)` model with no fields beyond the
`BaseModel` timestamps. The download view at `/application-form/` creates one
row then redirects to the application-form PDF, which is hosted off-app by the
4 Vallées and configured via the `APPLICATION_FORM_URL` setting.

### Key choices

**No PII.** No user FK, no IP address, no user-agent. The only data stored is
`created_at`. This avoids any additional GDPR / Swiss FADP data-minimisation
concern beyond what is already documented in the privacy policy.

**No bot filtering at launch.** Bot requests will inflate the count. The volume
is expected to be low (a community programme, not a public site), so raw counts
are useful enough without a filtering layer. A future ticket can add filtering
if the numbers become unreliable.

**`created_at` is the metric.** The admin `date_hierarchy` on `created_at`
lets staff browse download counts by day with no additional tooling.

**Redirect, not streaming.** The view redirects to the externally-hosted PDF
(`APPLICATION_FORM_URL`, kept in config so the URL can change without a deploy)
rather than streaming the file itself. This keeps the response fast, avoids
proxying the file, and means the row is created before the browser follows the
redirect — so even if the download is aborted, the intent is recorded.

---

## Consequences

- One new DB table (`public_formdownload`), three columns (`id`, `created_at`,
  `updated_at`). No migrations after the initial one unless the model changes.
- The count is a rough proxy: bots and repeated clicks are not filtered.
  Trend and order-of-magnitude are reliable; exact numbers are not.
- If a real analytics stack is added later, the `FormDownload` table can be
  retired or retained as a cross-check.
