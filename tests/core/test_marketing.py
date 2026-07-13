# Tests for core.marketing — click-ID normalisation and the derived
# marketing-attribution properties bridged to PostHog (VERB-147, ADR 0023).

from django.contrib.sessions.backends.db import SessionStore
from utm_tracker.session import SESSION_KEY_UTM_PARAMS

from core.marketing import (
    CLICK_ID_SOURCES,
    marketing_event_properties,
    normalise_source_params,
)

# ---------------------------------------------------------------------------
# normalise_source_params
# ---------------------------------------------------------------------------


def test_normalise_fbclid_only_injects_facebook_social() -> None:
    """A bare fbclid (organic Facebook) gets utm_source=facebook, utm_medium=social."""
    result = normalise_source_params({"fbclid": "abc123"})

    assert result["utm_source"] == "facebook"
    assert result["utm_medium"] == "social"
    assert result["fbclid"] == "abc123"


def test_normalise_gclid_only_injects_google_cpc() -> None:
    """A bare gclid gets utm_source=google, utm_medium=cpc."""
    result = normalise_source_params({"gclid": "xyz"})

    assert result["utm_source"] == "google"
    assert result["utm_medium"] == "cpc"


def test_normalise_msclkid_only_injects_bing_cpc() -> None:
    """A bare msclkid gets utm_source=bing, utm_medium=cpc."""
    result = normalise_source_params({"msclkid": "xyz"})

    assert result["utm_source"] == "bing"
    assert result["utm_medium"] == "cpc"


def test_normalise_aclk_only_injects_bing_cpc() -> None:
    """A bare aclk (Bing ad click) gets utm_source=bing, utm_medium=cpc."""
    result = normalise_source_params({"aclk": "xyz"})

    assert result["utm_source"] == "bing"
    assert result["utm_medium"] == "cpc"


def test_normalise_twclid_only_injects_twitter_social() -> None:
    """A bare twclid gets utm_source=twitter, utm_medium=social."""
    result = normalise_source_params({"twclid": "xyz"})

    assert result["utm_source"] == "twitter"
    assert result["utm_medium"] == "social"


def test_normalise_leaves_explicit_utm_source_untouched() -> None:
    """A visit that already carries utm_source is never overwritten, even with
    a click ID present.
    """
    params = {"utm_source": "newsletter", "fbclid": "abc123"}

    result = normalise_source_params(params)

    assert result == params
    assert result["utm_source"] == "newsletter"
    assert "utm_medium" not in result


def test_normalise_empty_params_returns_empty() -> None:
    """No params in, no params out — nothing to normalise."""
    assert normalise_source_params({}) == {}


def test_normalise_no_click_id_no_utm_source_returns_unchanged() -> None:
    """Params with neither utm_source nor a known click ID pass through as-is."""
    params = {"utm_campaign": "spring"}
    assert normalise_source_params(params) == params


def test_normalise_does_not_mutate_input() -> None:
    """The input dict is never mutated — a copy is returned when injecting."""
    params = {"fbclid": "abc123"}
    normalise_source_params(params)
    assert params == {"fbclid": "abc123"}


def test_click_id_sources_covers_the_five_documented_platforms() -> None:
    """The map matches the plan's documented click-ID -> (source, medium) set."""
    assert CLICK_ID_SOURCES == {
        "fbclid": ("facebook", "social"),
        "gclid": ("google", "cpc"),
        "msclkid": ("bing", "cpc"),
        "aclk": ("bing", "cpc"),
        "twclid": ("twitter", "social"),
    }


# ---------------------------------------------------------------------------
# marketing_event_properties
# ---------------------------------------------------------------------------


def _session_with(params: dict[str, str]) -> SessionStore:
    """Build an in-memory session holding one stashed utm_params entry."""
    session = SessionStore()
    session[SESSION_KEY_UTM_PARAMS] = [params]
    return session


def test_marketing_event_properties_empty_session_returns_empty_dict() -> None:
    """A session with no stashed params returns {} — nothing to attribute."""
    assert marketing_event_properties(SessionStore()) == {}


def test_marketing_event_properties_derives_source_and_utm_fields() -> None:
    """Stashed utm_source/medium/campaign surface as source + utm_* properties."""
    session = _session_with(
        {"utm_source": "facebook", "utm_medium": "social", "utm_campaign": "verbier"}
    )

    properties = marketing_event_properties(session)

    assert properties["source"] == "facebook"
    assert properties["utm_source"] == "facebook"
    assert properties["utm_medium"] == "social"
    assert properties["utm_campaign"] == "verbier"


def test_marketing_event_properties_builds_set_once_first_touch() -> None:
    """A $set_once sub-dict carries the first-touch attribution fields."""
    session = _session_with(
        {"utm_source": "facebook", "utm_medium": "social", "utm_campaign": "verbier"}
    )

    properties = marketing_event_properties(session)

    assert properties["$set_once"] == {
        "initial_source": "facebook",
        "initial_utm_medium": "social",
        "initial_utm_campaign": "verbier",
    }


def test_marketing_event_properties_excludes_raw_click_ids() -> None:
    """Raw click IDs must never reach PostHog, even though they are stashed."""
    session = _session_with(
        {"utm_source": "facebook", "utm_medium": "social", "fbclid": "abc123"}
    )

    properties = marketing_event_properties(session)

    assert "fbclid" not in properties
    assert "fbclid" not in properties.get("$set_once", {})


def test_marketing_event_properties_uses_latest_stashed_entry() -> None:
    """When multiple visits stashed params, only the most recent one is used."""
    session = SessionStore()
    session[SESSION_KEY_UTM_PARAMS] = [
        {"utm_source": "google", "utm_medium": "cpc"},
        {"utm_source": "facebook", "utm_medium": "social"},
    ]

    properties = marketing_event_properties(session)

    assert properties["source"] == "facebook"


def test_marketing_event_properties_never_raises_on_malformed_session() -> None:
    """A session holding an unexpected shape under the key is handled, not raised."""
    session = SessionStore()
    session[SESSION_KEY_UTM_PARAMS] = "not-a-list"  # deliberately malformed

    assert marketing_event_properties(session) == {}
