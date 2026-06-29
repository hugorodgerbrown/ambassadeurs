# Lighthouse audits

The `Lighthouse` GitHub Actions workflow
([`.github/workflows/lighthouse.yml`](../.github/workflows/lighthouse.yml)) runs
[Lighthouse CI](https://github.com/GoogleChrome/lighthouse-ci) against the key
public pages on every push to `main` and every pull request that touches Python,
templates, CSS, static assets, or the Lighthouse config. It guards against
regressions in performance, accessibility, best-practices, and SEO.

## How it runs

The job builds the Tailwind CSS, applies migrations against SQLite, starts the
Django development server, and runs `lhci autorun`. Configuration —
audited URLs, number of runs, and pass/fail thresholds — lives in
[`lighthouserc.json`](../lighthouserc.json). The server runs under
`config.settings.development` (the `manage.py` default), so no database service
or secrets are needed.

Pages audited (3 runs each, mobile emulation — Lighthouse's default, matching
the Facebook-sourced mobile audience):

- `/` — home
- `/how-it-works/`
- `/faq/`
- `/register/`
- `/account/login/`
- `/legal/privacy/`

Authenticated and token-gated pages (account area, match accept/decline) are not
audited — they need a logged-in session or a signed token.

## Thresholds

Each category has a per-page minimum, asserted as an error (a lower score fails
the build). The floors sit below the current mobile medians so they catch
regressions without tripping on mobile-emulation noise. See the
`assert.assertions` block in `lighthouserc.json`.

| Category | Floor |
|----------|-------|
| Performance | 0.75 |
| Accessibility | 0.80 |
| Best-practices | 0.95 |
| SEO | 0.85 |

Performance is the noisy category under mobile emulation (CPU/network
throttling), so it carries the widest margin. Accessibility, best-practices, and
SEO are deterministic.

## Running locally

```bash
npm install                 # one-time: installs @lhci/cli
npm run css:build           # build static/css/output.css
uv run python manage.py migrate
npm run lighthouse          # = lhci autorun
```

`lhci` starts and stops its own server (port 8765) and writes full HTML/JSON
reports to `lighthouse-reports/` (gitignored). It needs a local Chrome
installation.

Use **Node 24** to match CI (`.github/workflows/lighthouse.yml`); GitHub has
deprecated Node 20 on its runners.

## Baseline

Captured 2026-06-29 (commit on branch `claude/stoic-bell-5411c0`), mobile
emulation, median of 3 runs:

| Page | Perf | A11y | Best-pr | SEO | LCP (s) | CLS |
|------|------|------|---------|-----|---------|-----|
| `/` | 0.83 | 0.94 | 1.00 | 0.91 | 4.20 | 0.000 |
| `/account/login/` | 0.95 | 0.84 | 1.00 | 0.90 | 2.34 | 0.033 |
| `/faq/` | 0.95 | 0.90 | 1.00 | 0.90 | 2.34 | 0.000 |
| `/how-it-works/` | 0.98 | 0.90 | 1.00 | 0.90 | 2.14 | 0.000 |
| `/legal/privacy/` | 0.96 | 0.90 | 1.00 | 0.90 | 2.18 | 0.000 |
| `/register/` | 0.95 | 0.93 | 1.00 | 0.90 | 2.35 | 0.000 |

The home page is the weakest on performance (0.83): its photographic hero is the
LCP element at ~4.2s under throttled mobile. Accessibility bottoms out at 0.84 on
the login page — mobile audits add tap-target sizing that desktop skips. Scores
measured on a CI runner may differ slightly from a local machine.

The home page performance and login accessibility are candidates for follow-up
work (hero image optimisation, tap-target spacing); the thresholds above are set
to hold the current line, not to force those fixes now.
