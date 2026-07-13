# Marketing-source attribution (VERB-147, ADR 0023).
#
# Pure logic shared by core.middleware.MarketingSourceMiddleware (which
# normalises then stashes utm_*/click-id querystring params into the session)
# and the PostHog bridge in matching.services.register_participant (which
# reads the stashed params back out and turns them into event properties).
# Keeping the click-id -> (source, medium) map and both pure functions here
# means there is exactly one place that knows what a "derived source" is.
#
# Raw click IDs (fbclid, gclid, ...) are opaque per-click identifiers from a
# third-party ad platform and must never reach PostHog (a third-party
# processor) — only the *derived* source/medium/campaign go out. Click IDs
# may persist in the first-party utm_tracker.LeadSource row.

from __future__ import annotations

from typing import Any

from django.contrib.sessions.backends.base import SessionBase
from utm_tracker.session import SESSION_KEY_UTM_PARAMS

# Click-ID -> (utm_source, utm_medium), used only to fill in the two fields
# django-utm-tracker's LeadSourceMiddleware requires (utm_source + utm_medium)
# when a visitor arrives with a bare click ID and no utm_* params of their
# own — e.g. organic Facebook traffic sharing a post link with "?fbclid=...".
# Without this, such a visit would carry a click ID but no utm_source, so the
# library's dump_utm_params silently drops it (ValueError, swallowed) and it
# never becomes a LeadSource. Checked in this priority order; first match wins.
CLICK_ID_SOURCES: dict[str, tuple[str, str]] = {
    "fbclid": ("facebook", "social"),
    "gclid": ("google", "cpc"),
    "msclkid": ("bing", "cpc"),
    "aclk": ("bing", "cpc"),
    "twclid": ("twitter", "social"),
}


def normalise_source_params(params: dict[str, str]) -> dict[str, str]:
    """Inject a synthesised utm_source/utm_medium for a click-ID-only visit.

    Returns ``params`` unchanged when ``utm_source`` is already present, or
    when no known click-ID key is present. Otherwise returns a *copy* with
    ``utm_source``/``utm_medium`` filled in from ``CLICK_ID_SOURCES`` (first
    match by the map's iteration order). Pure — no I/O, never mutates the
    input dict.

    Args:
        params: The querystring-derived params dict (e.g. from
            ``utm_tracker.request.parse_qs``).

    Returns:
        The original dict, or a copy with the two fields injected.
    """
    if params.get("utm_source"):
        return params

    for click_id, (source, medium) in CLICK_ID_SOURCES.items():
        if params.get(click_id):
            normalised = dict(params)
            normalised["utm_source"] = source
            normalised["utm_medium"] = medium
            return normalised

    return params


def marketing_event_properties(session: SessionBase) -> dict[str, Any]:
    """Derive PostHog event properties from the session's stashed utm params.

    Reads the most recently stashed params dict (``utm_tracker.session.
    SESSION_KEY_UTM_PARAMS`` — a list, appended to by ``stash_utm_params``) and
    returns a properties dict carrying only the derived ``source`` and the
    non-empty ``utm_*`` fields, plus a ``$set_once`` sub-dict for first-touch
    person-property attribution. Raw click IDs (fbclid, gclid, ...) are
    deliberately excluded — they must never reach PostHog.

    Returns ``{}`` when the session holds no stashed params. Never raises:
    analytics must not break registration, so any unexpected session shape is
    treated as "nothing to report".

    Args:
        session: The current request's session (``request.session``).

    Returns:
        A properties dict suitable for merging into a ``capture_event`` call,
        or ``{}``.
    """
    try:
        stashed = session.get(SESSION_KEY_UTM_PARAMS) or []
        if not stashed:
            return {}
        latest = stashed[-1]

        source = latest.get("utm_source", "")
        medium = latest.get("utm_medium", "")
        campaign = latest.get("utm_campaign", "")

        properties: dict[str, Any] = {}
        if source:
            # "source" is the funnel-breakdown key; "utm_source" is kept
            # alongside it for raw-value analysis (they usually match, but
            # needn't — e.g. a hand-crafted utm_source that isn't a known
            # click-id source).
            properties["source"] = source
            properties["utm_source"] = source
        if medium:
            properties["utm_medium"] = medium
        if campaign:
            properties["utm_campaign"] = campaign

        set_once: dict[str, str] = {}
        if source:
            set_once["initial_source"] = source
        if medium:
            set_once["initial_utm_medium"] = medium
        if campaign:
            set_once["initial_utm_campaign"] = campaign
        if set_once:
            properties["$set_once"] = set_once

        return properties
    except Exception:  # noqa: BLE001 — analytics must never break registration.
        return {}
