# Visitor dashboard

`visitor-dashboard.html` is the source of the "Ski Parrainage Visitors" artifact
published on claude.ai:

https://claude.ai/artifact/HPiC2LYuAXB866nwKThNYe

It is not served by the Django app. The page reads the site's web analytics from
[fivebar](https://fiveb.ar/skiparrainage.com) through the viewer's own claude.ai
fivebar connector, so each viewer needs that connector connected; anyone without
it sees the built-in Example data.

## What it shows

- Page speed (TTFB, FCP, LCP, INP, CLS, page load time) with a slow-pages
  drill-down: pages rated Needs work or Poor for the chosen measure, each
  expandable into a device › OS › country tree.
- Live visitors, totals, visitors over time, hours of the day.
- Sources, countries, pages (English and `/fr/` French versions merged into
  EN / FR columns) and devices.

## Updating it

The published artifact, not this file, is what viewers see. To change it, edit
this file and ask Claude to republish it to the URL above. The artifact declares
the `mcp` capability for the `fivebar` connector with the tools `list_sites`,
`get_live_visitors`, `get_site_summary`, `get_site_breakdown`, `get_site_hours`
and `get_site_speed`; a republish must keep that declaration.
