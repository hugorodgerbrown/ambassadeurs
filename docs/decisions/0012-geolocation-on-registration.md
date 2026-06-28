# ADR 0012 — Geolocation metadata on Registration

**Date:** 2026-06-28  
**Status:** Accepted  
**Ticket:** VERB-49

---

## Context

Program staff want visibility into where registrations are coming from. A
country-and-region breakdown helps administrators understand the geographic
reach of the programme and spot anomalous patterns (e.g. a surge of
registrations from an unexpected country, which may indicate a data quality
issue or an off-target promotion).

The information needed is coarse: country name and region/subdivision name
(e.g. "Switzerland" / "Valais"). No precise location or street address is
required, and the raw IP address must not be retained (Swiss data protection,
CLAUDE.md data minimisation note).

---

## Decision

### What is captured

Two free-text fields are added to `Registration`:

- `registration_country` (e.g. `"Switzerland"`)
- `registration_region` (e.g. `"Valais"`)

Both are derived from the request's client IP **in memory only** at
registration time, using a locally-installed MaxMind GeoLite2-City database.
The raw IP address is discarded immediately after the lookup — it is never
assigned to a variable that outlives the resolution call, and it is never
written to the database, logs, or any other store.

### Where resolution happens

The geo lookup is performed once in the `register` view (POST path), before
the authenticated / anonymous branch, and the resulting strings are passed as
keyword arguments to `register_participant`. The service is kept entirely free
of `HttpRequest` so it remains unit-testable without a request object.

### Database choice

MaxMind GeoLite2-City was chosen because:

- It is free for self-hosting (CC BY-SA 4.0 licence with a MaxMind account).
- A local `.mmdb` file avoids a network call on every registration (no API
  latency, no external dependency at runtime).
- Country + city-level subdivision resolution is sufficient for the programme's
  analytical needs.

The `.mmdb` file is downloaded during the Render build step (`build.sh`) when
`MAXMIND_LICENSE_KEY` is set. When the key is absent (local development, CI
without credentials), the download step is skipped and geolocation degrades
gracefully: both fields are stored as empty strings and a `logger.warning` is
emitted.

### Degradation

All failure modes (missing database, private/RFC 1918 address such as
`127.0.0.1` in local development, any other lookup error) return `("", "")`
and never raise. Registration continues in all cases. This ensures the geo
feature does not gate the critical registration path.

### Admin visibility

Both fields appear in `RegistrationAdmin` as read-only columns (list view +
detail view). They are never exposed in participant-facing templates.

### No IP storage — invariant

The raw client IP is resolved only in the `register` view's local scope and
is not returned by `geolocate`. The service layer receives only the derived
strings. This is the data minimisation invariant for this feature: geo metadata
is operational, not personally identifying, and the IP itself is the more
sensitive artefact.

---

## Consequences

- `Registration` gains two nullable-blank `CharField` fields with empty-string
  defaults, covered by migration `0008_verb49_add_geo_fields`.
- `register_participant` gains two keyword arguments (`registration_country`,
  `registration_region`) with empty-string defaults; all existing callers
  continue to work without changes.
- A new helper module `core/geo.py` provides `get_client_ip` and `geolocate`;
  both are independently tested with mocked `geoip2.database.Reader` so no
  real `.mmdb` is required in CI.
- `build.sh` gains a guarded download step; `geoip/` is added to `.gitignore`.
- MaxMind credentials (`MAXMIND_LICENSE_KEY`) are documented in `.env.example`
  but never committed to source.
