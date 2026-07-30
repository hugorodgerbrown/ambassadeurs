# HTTP content negotiation for the Markdown representations (SKI-155, ADR 0026).
#
# Django ships `HttpRequest.accepts()`, but it only answers "is this type
# acceptable at all" — it ignores q-values. That is not enough here: the whole
# question is which of two acceptable types the client *prefers*, and getting it
# wrong means serving Markdown to a browser.
#
# The rules this implements, and why each one matters:
#
#   1. Compare q-values; never substring-match the header. A browser sending
#      "text/html, text/markdown;q=0.5" is saying it would accept Markdown but
#      wants HTML. A substring check for "text/markdown" would ship it Markdown.
#
#   2. Resolve ties to Markdown, but only when the client named text/markdown
#      explicitly. Coding agents commonly send "text/markdown, text/html" with
#      both at q=1, and a strict `>` would send them HTML. But a browser's
#      "*/*" also ties, via the wildcard — so the tie-break needs an
#      explicitness guard, not just `>=`.
#
#   3. Specificity wins within the header: an exact "text/markdown" beats
#      "text/*", which beats "*/*", regardless of the order they appear in.
#
# A missing or empty Accept header means "anything", per RFC 9110 — treated as
# q=1 for every type, non-explicit, so it resolves to HTML.

from __future__ import annotations

from dataclasses import dataclass

HTML = "text/html"
MARKDOWN = "text/markdown"


@dataclass(frozen=True)
class _Preference:
    """How much a client wants one media type, and how directly it said so.

    Attributes:
        quality: The q-value, 0.0 (unacceptable) to 1.0.
        explicit: Whether the type was named exactly, rather than matched by a
            ``text/*`` or ``*/*`` wildcard.
    """

    quality: float
    explicit: bool


def _parse_quality(params: list[str]) -> float:
    """Return the q-value from a media range's parameters, defaulting to 1.0.

    A malformed q (``q=high``) is treated as the default rather than raising:
    a bad Accept header should not 500 a page request.
    """
    for param in params:
        name, _, value = param.partition("=")
        if name.strip().lower() != "q":
            continue
        try:
            return max(0.0, min(1.0, float(value.strip())))
        except ValueError:
            return 1.0
    return 1.0


def preference_for(accept_header: str, media_type: str) -> _Preference:
    """Return the client's preference for ``media_type``.

    Scans every media range in the header and keeps the most *specific* match:
    an exact type beats a subtype wildcard, which beats a full wildcard. Order
    within the header is irrelevant — specificity decides, as RFC 9110 requires.

    Args:
        accept_header: The raw ``Accept`` header value; may be empty.
        media_type: The type being tested, e.g. ``text/markdown``.

    Returns:
        A :class:`_Preference`. An absent header yields q=1.0, non-explicit.
    """
    if not accept_header.strip():
        return _Preference(quality=1.0, explicit=False)

    wanted_type, _, wanted_subtype = media_type.partition("/")
    best: _Preference | None = None
    best_specificity = -1

    for part in accept_header.split(","):
        tokens = part.split(";")
        range_ = tokens[0].strip().lower()
        if not range_:
            continue
        range_type, _, range_subtype = range_.partition("/")

        if range_type == wanted_type and range_subtype == wanted_subtype:
            specificity = 2
        elif range_type == wanted_type and range_subtype == "*":
            specificity = 1
        elif range_type == "*" and range_subtype == "*":
            specificity = 0
        else:
            continue

        if specificity > best_specificity:
            best_specificity = specificity
            best = _Preference(
                quality=_parse_quality(tokens[1:]),
                explicit=specificity == 2,
            )

    return best or _Preference(quality=0.0, explicit=False)


def prefers_markdown(accept_header: str) -> bool:
    """Return whether the client would rather have Markdown than HTML.

    True only when Markdown is at least as welcome as HTML *and* the client
    named ``text/markdown`` itself. The explicitness guard is what stops a
    browser's ``*/*`` — which ties with HTML at q=1 — from flipping the whole
    site to Markdown.

    Args:
        accept_header: The raw ``Accept`` header value.

    Returns:
        True to serve Markdown, False to serve HTML.
    """
    markdown = preference_for(accept_header, MARKDOWN)
    if not markdown.explicit or markdown.quality == 0.0:
        return False
    return markdown.quality >= preference_for(accept_header, HTML).quality


def accepts_nothing_we_serve(accept_header: str) -> bool:
    """Return whether neither HTML nor Markdown is acceptable to the client.

    The trigger for a 406. Silently substituting a type the client said it did
    not want hides the bug; a 406 says so.

    Args:
        accept_header: The raw ``Accept`` header value.

    Returns:
        True when both representations have q=0 (or are absent from a header
        that lists other types).
    """
    return (
        preference_for(accept_header, HTML).quality == 0.0
        and preference_for(accept_header, MARKDOWN).quality == 0.0
    )
