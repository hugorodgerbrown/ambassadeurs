# ADR 0022 — Serve the Django admin on its own subdomain

**Status:** Accepted
**Date:** 2026-07-08

---

## Context

The Django admin was path-mounted at `/admin/` on the single Render web service
(`config/urls.py`, `path("admin/", admin.site.urls)`), sharing a hostname with
the public registration/matching site. We want the staff admin surface served on
its own subdomain instead — e.g. `admin.skiparrainage.ch` — so it is separated
from the public site and `/admin/` is not part of the public URL space at all.

This is a low-risk move for this codebase:

- There is **no `django.contrib.sites` / `SITE_ID`** (removed in ADR 0012), so
  there is no `Site.domain` record to reconfigure.
- Every admin link is generated with `reverse("admin:…")`, which yields
  host-relative paths — they render correctly under whatever host serves the
  admin.
- Email links are built either from `request.build_absolute_uri()` (host taken
  from the request) or from `settings.BASE_URL` (background/cron emails). Both
  resolve to the **public** host, and every `BASE_URL`-built link is a public
  route (`public:match`, `accounts:detail`). Moving the admin does not touch
  them.

## Decision

**Keep the single Render web service and route by hostname in a small
middleware, serving the admin *only* on the admin subdomain.** No new
dependency, no extra service.

### URLconf split

The single root URLconf is split into three modules:

- **`config/urls_public.py`** — the whole public site (healthz, `account/`,
  i18n, sitemap, debug, robots, the Stripe webhook, and the `public.urls`
  catch-all), **excluding** the admin.
- **`config/urls_admin.py`** — the Django admin only, mounted at the **root**
  (`path("", admin.site.urls)`), so the index is `admin.<domain>/` rather than
  `admin.<domain>/admin/`. Plus `healthz/` (health checks) and `i18n/`.
- **`config/urls.py`** — the combined default `ROOT_URLCONF`: admin at
  `/admin/` **plus** the public patterns imported verbatim from
  `config.urls_public`, so the two never drift. This is today's single-host
  behaviour, preserved for local dev, tests, and any single-host deployment.

### Host routing

`core.middleware.AdminHostMiddleware` sets `request.urlconf` per request from a
single `settings.ADMIN_HOST` knob:

| `ADMIN_HOST` | Request host | URLconf used |
|---|---|---|
| unset (`""`) | any | `config.urls` (combined — admin at `/admin/`) |
| set | == `ADMIN_HOST` | `config.urls_admin` (admin only) |
| set | != `ADMIN_HOST` | `config.urls_public` (public only, no `/admin/`) |

The middleware reads `settings.ADMIN_HOST` on every request (not cached in
`__init__`, so `@override_settings` works in tests) and strips any `:port`
before comparing. It is registered early in `MIDDLEWARE` (right after
`SecurityMiddleware`) because `LocaleMiddleware` and `CommonMiddleware` read
`request.urlconf`, and URL resolution happens after all request-phase
middleware — so the URLconf must be chosen first.

`ADMIN_HOST` defaults to empty, which makes the middleware a **no-op**: local
development and the entire existing test suite keep the combined URLconf, so
`reverse("admin:…")` and every `tests/**/test_admin.py` assertion pass
unchanged. The split only activates in an environment that sets `ADMIN_HOST`.

### Deployment

`ADMIN_HOST` is a `sync: false` env var on the web service in `render.yaml`
(inherited by the crons for env parity, though crons never route HTTP). The
`ALLOWED_HOSTS` and `CSRF_TRUSTED_ORIGINS` values (already env-driven CSVs) must
gain the admin host / `https://` origin, and the admin subdomain is added as a
custom domain on the single web service in the Render dashboard (Render
auto-provisions TLS; `SECURE_HSTS_INCLUDE_SUBDOMAINS = True` already covers it).

## Alternatives considered

- **Second Render web service** running the same code with an admin-only
  URLconf — stronger process/network isolation (e.g. IP-allowlisting the admin
  service) at the cost of more infrastructure. Not needed for the current
  requirement; the single-service split can be promoted to this later without
  reworking the URLconf modules.
- **`django-hosts`** — declarative host→urlconf mapping, but more machinery than
  one subdomain warrants and against the project's "simple over complex" rule.

## Consequences

- **True isolation.** With `ADMIN_HOST` set, `skiparrainage.ch/admin/` 404s and
  the admin answers only on `admin.skiparrainage.ch/`. The public site never
  exposes the admin surface.
- **Sessions are naturally separate.** No `SESSION_COOKIE_DOMAIN` is set, so
  cookies are host-only — an admin-subdomain session is independent of any
  public-site session, which is the desirable isolation here. Cross-subdomain
  SSO is explicitly not wired up (it would require
  `SESSION_COOKIE_DOMAIN = ".skiparrainage.ch"`).
- **One place to keep in sync.** `config.urls` reuses `config.urls_public`'s
  patterns, so a new public route added to `urls_public` appears on both the
  combined default and the public-only URLconf automatically. Only the admin
  mount is duplicated (root in `urls_admin`, `/admin/` in `urls`).
- **`robots.txt` `Disallow: /admin/`** (`core/views.py`) becomes inaccurate but
  harmless once the admin is subdomain-only — `/admin/` no longer exists on the
  public host. Left in place as defence-in-depth for single-host deployments.
