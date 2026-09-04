# ADR 0027 — Runtime-editable CSP via django-csp-plus

**Status:** Accepted
**Date:** 2026-09-04
**Ticket:** SKI-170
**Supersedes:** the `django-csp` configuration introduced in VERB-71

---

## Context

The Content-Security-Policy was a Python dict (`CSP_DIRECTIVES`) read by
`django-csp`. Every policy change was therefore a code change, a review and a
deploy — including the changes that are not really decisions at all, like
"this third-party origin needs to be allowed".

That cost came due when the tip flow broke in production. `form-action 'self'`
blocked the payment hop, because both money flows POST to a local view which
302s to Stripe hosted Checkout, and Chrome and Safari re-check `form-action`
against the target of a redirect that follows a form POST. The spec says they
should not ([w3c/webappsec-csp#8]) and Firefox does not, but the browsers most
of our readers use do.

Two things made that expensive out of proportion to the fix:

1. **The violation was invisible until a user hit it.** Development and e2e run
   the policy report-only, but nothing collected the reports — they went to the
   browser console of whoever happened to be looking.
2. **The one-line fix needed a deploy.** The allowance is a fact about an
   external service, not a property of our code.

The browser also reports the *form's* action URL rather than the redirect
target, so the console message names a same-origin URL and reads as a
contradiction. Diagnosing it from the message alone is not straightforward,
which raises the value of collecting reports somewhere they can be compared.

## Decision

**Replace `django-csp` with [`django-csp-plus`]** (yunojuno), the same
MDN-derived middleware with two additions: a violation-report store surfaced in
the Django admin, and rules held in a `CspRule` model that **extend** the
settings baseline at runtime.

### The two layers

- **Baseline** — `config.settings.base.CSP_DEFAULTS`. The policy that ships:
  reviewed, in the diff, identical across environments. Everything the app
  itself needs is here, including `https://checkout.stripe.com` on
  `form-action`, so both money flows work on deploy rather than depending on
  someone remembering to add a row afterwards.
- **Runtime rules** — enabled `csp.CspRule` rows, added in the admin, merged
  into the baseline per directive (never replacing it). This is for the
  unknown-unknowns: an origin some third-party tool turns out to need.

A `CspReport` row can be converted into a rule with an admin action, which is
the workflow this swap exists to buy.

### Enforcement

`CSP_REPORT_ONLY` is true everywhere except `config.settings.production`. That
is unchanged in substance from the previous setup, and keeps the same
trade-off: no non-production lane enforces, so an enforcement-only break
(such as the one above) is not caught before production. Fixing that properly
means an enforcing e2e lane, which is out of scope here.

Both settings are read by the library **at import time**, so
`@override_settings` cannot move them in a test. Tests assert against the
configured environment rather than trying to flip the mode.

### Placeholders

`{nonce}` and `{report_uri}` in the baseline are substituted per request.
`request.csp_nonce` is unchanged from `django-csp`, so the nonce'd inline
scripts in `base.html` and friends needed no edit.

### Caching

The built policy is cached for `CSP_CACHE_TIMEOUT`. `CACHES` is a per-process
`LocMemCache` and Gunicorn runs several workers, so that timeout is also the
worst-case lag before an admin change is live on *every* worker. It is set to
**60 seconds**: rebuilding is one indexed query, and a rule that takes an hour
to appear would defeat the point of having runtime rules at all.

## Consequences

- **A blocked origin is an admin edit, not a deploy.** This is the whole point.
- **The policy is now partly database state.** It is no longer fully described
  by the repository, which is a real cost — the same one SKI-162 spent effort
  *avoiding* for season configuration. The trade is deliberate and the split is
  the mitigation: anything the app needs to function lives in the baseline, and
  the database layer only ever adds. A missing `CspRule` row can loosen nothing
  the app depends on.
- **The report endpoint is public and unauthenticated**, accepting JSON — the
  same shape of surface as the Stripe webhook, without a signature to check.
  `CSP_REPORT_SAMPLING` (how often the header asks for reports) and
  `CSP_REPORT_THROTTLING` (what fraction of inbound reports is discarded) are
  the levers if it is abused; both are left at their defaults until there is
  traffic to judge.
- **It is mounted on both URLconfs.** `csp.policy` builds the header by
  reversing `csp:report_uri`, so a URLconf without that namespace raises
  `NoReverseMatch` from the response middleware and every page on that host
  500s. `config/urls_admin.py` mounts it for exactly this reason, not only for
  completeness.
- **The 500 page lost its hashed inline `<style>` block.** Values are
  normalised through `CspRule.clean_value`, which lowercases them, and a base64
  `'sha256-…'` source does not survive that. Rather than relax `style-src` to
  `'unsafe-inline'`, the critical styles moved to `static/css/500.css`. It is
  deliberately a separate file from `output.css`: the block existed so the page
  reads correctly when the Tailwind build is what broke.
- **Value order within a directive is not stable** — the library dedupes
  through a set. Order is meaningless in CSP, but tests must not assert on it.
- **`csp.models` ships no `py.typed`.** django-stubs walks `INSTALLED_APPS` and
  imports each app's models, so `csp.*` needs an `ignore_missing_imports`
  override or the untyped import is reported against our own `apps.py` files.

## Alternatives considered

- **Add `https://checkout.stripe.com` to the existing dict and stop there.**
  Fixes the symptom in one line. Leaves every future origin as a deploy, and
  leaves violations uncollected — so the next one is found the same way this
  one was: by a user hitting it.
- **Relax `form-action` to `'self' https:`.** Cheap and materially weaker; it
  would permit form submission to any HTTPS origin, which is most of what the
  directive is for.
- **Keep `django-csp` and build a report endpoint locally.** The endpoint is
  the easy half. The admin workflow that turns a report into a rule is the part
  worth having, and it is what the library already provides.

[`django-csp-plus`]: https://github.com/yunojuno/django-csp-plus
[w3c/webappsec-csp#8]: https://github.com/w3c/webappsec-csp/issues/8
