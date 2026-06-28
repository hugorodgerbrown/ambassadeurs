# IP geolocation helper.
#
# Resolves a client IP address to a country name and region/subdivision name
# using a locally-installed MaxMind GeoLite2-City database. This is operational
# metadata captured at registration time and stored on Registration for admin
# visibility only — never shown to participants.
#
# The raw IP address is resolved in memory only and is NEVER returned or
# persisted by this module (Invariant — see CLAUDE.md data minimisation note).
# Only the derived country and region strings are exposed to callers.
#
# If the GeoLite2 database file is absent or misconfigured, both functions
# degrade gracefully: geolocate returns ("", "") and logs a warning, so
# registration continues unaffected.

import logging

import geoip2.database
import geoip2.errors
from django.conf import settings
from django.http import HttpRequest

logger = logging.getLogger(__name__)


def get_client_ip(request: HttpRequest) -> str | None:
    """Extract the client IP address from the request.

    Reads the leftmost address from the ``X-Forwarded-For`` header (which
    Render sets to the genuine client IP), falling back to ``REMOTE_ADDR``.
    Returns ``None`` if neither header yields a usable value.

    The returned IP is the least-trusted part of the chain when a proxy is
    involved, but because this data feeds only an analytics field (not a
    security boundary), a spoofed header causes at worst a wrong country label,
    not a vulnerability.

    Args:
        request: The incoming Django ``HttpRequest``.

    Returns:
        An IP address string, or ``None`` if nothing usable is present.
    """
    xff: str = request.META.get("HTTP_X_FORWARDED_FOR", "")
    if xff:
        # Take the leftmost address; strip surrounding whitespace.
        leftmost: str = xff.split(",")[0].strip()
        if leftmost:
            return leftmost
    addr: str = request.META.get("REMOTE_ADDR", "").strip()
    return addr if addr else None


def geolocate(ip: str) -> tuple[str, str]:
    """Resolve an IP address to a country name and region name.

    Opens the GeoLite2-City database at ``settings.GEOIP_DATABASE_PATH`` and
    performs a city-level lookup. Returns ``(country_name, region_name)`` on
    success. ``country_name`` is the English full name (e.g. ``"Switzerland"``);
    ``region_name`` is the most-specific subdivision name (e.g. ``"Valais"``).

    Degrades gracefully on any failure:
    - Missing or misconfigured database file → logs a warning, returns
      ``("", "")``.
    - RFC 1918 / loopback / unroutable address (``AddressNotFoundError``) →
      returns ``("", "")`` without logging (expected in local development).
    - Any other lookup error → logs a warning, returns ``("", "")``.

    The IP address itself is used only as a local variable and is NEVER
    returned to the caller or persisted.

    Args:
        ip: The IP address string to look up.

    Returns:
        A ``(country_name, region_name)`` tuple; both are empty strings on any
        failure.
    """
    db_path = str(settings.GEOIP_DATABASE_PATH)
    try:
        with geoip2.database.Reader(db_path) as reader:
            response = reader.city(ip)
            country = response.country.name or ""
            region = response.subdivisions.most_specific.name or ""
            return country, region
    except FileNotFoundError:
        logger.warning(
            "GeoLite2 database not found at %r; geolocation disabled. "
            "Set GEOIP_DATABASE_PATH or run the download step in build.sh.",
            db_path,
        )
        return "", ""
    except geoip2.errors.AddressNotFoundError:
        # Private / loopback / RFC 1918 addresses — expected in development.
        return "", ""
    except Exception:
        logger.warning(
            "Unexpected error during geolocation for IP (address not logged); "
            "returning empty geo fields.",
            exc_info=True,
        )
        return "", ""
