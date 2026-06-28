# Tests for core.geo — IP extraction and geolocation helpers.

from unittest.mock import MagicMock, patch

import pytest
from django.test import RequestFactory, override_settings

from core.geo import geolocate, get_client_ip

# No django_db marker: these tests use RequestFactory and mock geoip2 — they
# never touch the ORM, so they must not request a database fixture.


# ---------------------------------------------------------------------------
# get_client_ip
# ---------------------------------------------------------------------------


def test_get_client_ip_from_x_forwarded_for_single() -> None:
    """get_client_ip returns the single entry from X-Forwarded-For."""
    factory = RequestFactory()
    request = factory.get("/")
    request.META["HTTP_X_FORWARDED_FOR"] = "203.0.113.45"
    assert get_client_ip(request) == "203.0.113.45"


def test_get_client_ip_from_x_forwarded_for_multiple() -> None:
    """get_client_ip returns the leftmost XFF entry when multiple values are present."""
    factory = RequestFactory()
    request = factory.get("/")
    request.META["HTTP_X_FORWARDED_FOR"] = "203.0.113.45, 10.0.0.1, 172.16.0.2"
    assert get_client_ip(request) == "203.0.113.45"


def test_get_client_ip_from_x_forwarded_for_strips_whitespace() -> None:
    """get_client_ip strips surrounding whitespace from the leftmost XFF entry."""
    factory = RequestFactory()
    request = factory.get("/")
    request.META["HTTP_X_FORWARDED_FOR"] = "  203.0.113.45  , 10.0.0.1"
    assert get_client_ip(request) == "203.0.113.45"


def test_get_client_ip_falls_back_to_remote_addr() -> None:
    """get_client_ip falls back to REMOTE_ADDR when X-Forwarded-For is absent."""
    factory = RequestFactory()
    request = factory.get("/")
    # RequestFactory does not set HTTP_X_FORWARDED_FOR; REMOTE_ADDR is set by default.
    request.META.pop("HTTP_X_FORWARDED_FOR", None)
    request.META["REMOTE_ADDR"] = "198.51.100.7"
    assert get_client_ip(request) == "198.51.100.7"


def test_get_client_ip_returns_none_when_no_usable_value() -> None:
    """get_client_ip returns None when neither XFF nor REMOTE_ADDR yields an IP."""
    factory = RequestFactory()
    request = factory.get("/")
    request.META.pop("HTTP_X_FORWARDED_FOR", None)
    request.META.pop("REMOTE_ADDR", None)
    assert get_client_ip(request) is None


# ---------------------------------------------------------------------------
# geolocate
# ---------------------------------------------------------------------------


@override_settings(GEOIP_DATABASE_PATH="/fake/path/GeoLite2-City.mmdb")
def test_geolocate_returns_country_and_region_on_success() -> None:
    """geolocate returns (country_name, region_name) when the lookup succeeds."""
    mock_response = MagicMock()
    mock_response.country.name = "Switzerland"
    mock_subdivision = MagicMock()
    mock_subdivision.name = "Valais"
    mock_response.subdivisions.most_specific = mock_subdivision

    mock_reader = MagicMock()
    mock_reader.__enter__ = MagicMock(return_value=mock_reader)
    mock_reader.__exit__ = MagicMock(return_value=False)
    mock_reader.city.return_value = mock_response

    with patch("geoip2.database.Reader", return_value=mock_reader):
        country, region = geolocate("203.0.113.45")

    assert country == "Switzerland"
    assert region == "Valais"


@override_settings(GEOIP_DATABASE_PATH="/nonexistent/GeoLite2-City.mmdb")
def test_geolocate_returns_empty_strings_when_db_missing(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """geolocate returns ('', '') and emits a warning when the database is absent."""
    import logging

    with caplog.at_level(logging.WARNING, logger="core.geo"):
        with patch("geoip2.database.Reader", side_effect=FileNotFoundError):
            country, region = geolocate("203.0.113.45")

    assert country == ""
    assert region == ""
    assert any(
        "GeoLite2 database not found" in record.message for record in caplog.records
    )


@override_settings(GEOIP_DATABASE_PATH="/fake/path/GeoLite2-City.mmdb")
def test_geolocate_returns_empty_strings_for_private_ip(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """geolocate returns ('', '') silently for private/loopback addresses.

    "Silently" means no warning is logged — an unroutable address is an expected
    outcome, not an error. The caplog assertion guards against a regression that
    adds a log line in the AddressNotFoundError branch.
    """
    import logging

    import geoip2.errors

    mock_reader = MagicMock()
    mock_reader.__enter__ = MagicMock(return_value=mock_reader)
    mock_reader.__exit__ = MagicMock(return_value=False)
    mock_reader.city.side_effect = geoip2.errors.AddressNotFoundError("private")

    with caplog.at_level(logging.WARNING, logger="core.geo"):
        with patch("geoip2.database.Reader", return_value=mock_reader):
            country, region = geolocate("192.168.1.1")

    assert country == ""
    assert region == ""
    assert caplog.records == []


@override_settings(GEOIP_DATABASE_PATH="/fake/path/GeoLite2-City.mmdb")
def test_geolocate_returns_empty_strings_on_unexpected_error(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """geolocate returns ('', '') and logs a warning on unexpected lookup errors."""
    import logging

    mock_reader = MagicMock()
    mock_reader.__enter__ = MagicMock(return_value=mock_reader)
    mock_reader.__exit__ = MagicMock(return_value=False)
    mock_reader.city.side_effect = RuntimeError("unexpected")

    with caplog.at_level(logging.WARNING, logger="core.geo"):
        with patch("geoip2.database.Reader", return_value=mock_reader):
            country, region = geolocate("203.0.113.45")

    assert country == ""
    assert region == ""
    assert any("Unexpected error" in record.message for record in caplog.records)
