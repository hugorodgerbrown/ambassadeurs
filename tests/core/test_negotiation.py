# Tests for core.negotiation — Accept-header parsing (SKI-155, ADR 0026).
#
# These are the rules that decide whether a caller gets HTML or Markdown, so
# each one is pinned individually. The failure modes they guard against are
# specific: substring-matching ships Markdown to browsers, a strict `>` ships
# HTML to coding agents, and a missing explicitness guard flips the whole site
# to Markdown for anyone sending */*.

import pytest

from core.negotiation import (
    accepts_nothing_we_serve,
    preference_for,
    prefers_markdown,
)


class TestPreferenceFor:
    """Parsing one media type's quality and explicitness out of an Accept header."""

    def test_exact_match_is_explicit(self) -> None:
        """A named type is explicit at its stated quality."""
        preference = preference_for("text/markdown", "text/markdown")
        assert preference.quality == 1.0
        assert preference.explicit is True

    def test_subtype_wildcard_matches_but_is_not_explicit(self) -> None:
        """text/* covers text/markdown without naming it."""
        preference = preference_for("text/*", "text/markdown")
        assert preference.quality == 1.0
        assert preference.explicit is False

    def test_full_wildcard_matches_but_is_not_explicit(self) -> None:
        """*/* covers everything without naming anything."""
        preference = preference_for("*/*", "text/markdown")
        assert preference.quality == 1.0
        assert preference.explicit is False

    def test_quality_is_parsed(self) -> None:
        """An explicit q-value is read rather than assumed."""
        assert preference_for("text/markdown;q=0.3", "text/markdown").quality == 0.3

    def test_specificity_beats_header_order(self) -> None:
        """An exact match wins over an earlier wildcard, whatever the order.

        RFC 9110 ranks by specificity, not position, so a header that lists
        */* first must not mask a later, more precise entry.
        """
        header = "*/*;q=0.1, text/markdown;q=0.9"
        assert preference_for(header, "text/markdown").quality == 0.9

    def test_unmatched_type_has_zero_quality(self) -> None:
        """A type absent from a non-wildcard header is not acceptable."""
        assert preference_for("application/json", "text/markdown").quality == 0.0

    def test_empty_header_accepts_anything(self) -> None:
        """No Accept header means no preference, per RFC 9110."""
        preference = preference_for("", "text/markdown")
        assert preference.quality == 1.0
        assert preference.explicit is False

    def test_malformed_quality_falls_back_to_default(self) -> None:
        """A bad q-value must not 500 a page request."""
        assert preference_for("text/markdown;q=high", "text/markdown").quality == 1.0


class TestPrefersMarkdown:
    """The decision itself: HTML or Markdown for this caller."""

    @pytest.mark.parametrize(
        ("accept", "expected"),
        [
            # Explicitly asked for Markdown only.
            ("text/markdown", True),
            # A coding agent sending both at q=1 — the tie must go to Markdown,
            # which is why the comparison is >= rather than >.
            ("text/markdown, text/html", True),
            ("text/html, text/markdown", True),
            # Markdown accepted but explicitly less wanted than HTML. A
            # substring check for "text/markdown" would get this wrong.
            ("text/html, text/markdown;q=0.5", False),
            # A browser. Ties with HTML via the wildcard, but never named
            # Markdown — the explicitness guard is what keeps this HTML.
            ("*/*", False),
            ("text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8", False),
            ("text/*", False),
            # No header at all.
            ("", False),
            # Named, but explicitly refused.
            ("text/html, text/markdown;q=0", False),
        ],
    )
    def test_decision(self, accept: str, expected: bool) -> None:
        """Each header resolves to the documented representation."""
        assert prefers_markdown(accept) is expected


class TestAcceptsNothingWeServe:
    """The 406 trigger."""

    def test_true_when_neither_type_is_acceptable(self) -> None:
        """A client asking only for JSON gets a 406, not a silent substitution."""
        assert accepts_nothing_we_serve("application/json") is True

    @pytest.mark.parametrize(
        "accept", ["text/html", "text/markdown", "*/*", "text/*", ""]
    )
    def test_false_when_either_type_is_acceptable(self, accept: str) -> None:
        """Anything that can take HTML or Markdown is served, not refused."""
        assert accepts_nothing_we_serve(accept) is False
