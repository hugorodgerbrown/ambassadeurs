# HTML -> Markdown conversion for the .md representations (SKI-155, ADR 0026).
#
# The site's content exists only as Django templates full of {% blocktranslate %}
# blocks. Rather than maintain a parallel set of Markdown files — a second
# content store and a second translation surface — the .md routes convert the
# rendered page at request time. The template stays the single source of truth,
# and a copy edit reaches both representations at once.
#
# Only the <main> element is converted. The nav, footer, language switcher and
# notification strip are chrome: repeated on every page, meaningless without
# their styling, and pure noise to a reader who asked for the content.
#
# Three fixes are applied on top of stock markdownify, each found by converting
# the real FAQ page rather than reasoning about the markup:
#
#   1. <summary> becomes an h3. The FAQ is built from <details>/<summary>
#      accordions, so every question would otherwise collapse into a run-on
#      paragraph with its answer. The questions ARE the structure of an FAQ,
#      and headings are what makes the document navigable.
#   2. Fragment-only anchors are dropped. Each question carries a hover
#      permalink (<a href="#anchor">#</a>) that converts to a stray "[#](#anchor)"
#      in the middle of the prose.
#   3. Relative links are made absolute. A .md file is fetched and read on its
#      own, often by an agent with no notion of the origin it came from, so
#      "/how-it-works/" has to become a full URL to remain followable.

import logging
import re
from urllib.parse import urljoin

from bs4 import BeautifulSoup, Tag
from markdownify import MarkdownConverter

logger = logging.getLogger(__name__)

# RFC 7763. Lives here rather than beside the view so core.middleware can use it
# without importing the public.views package (and, through its __init__, every
# view module in it) at middleware-construction time.
MARKDOWN_CONTENT_TYPE = "text/markdown; charset=utf-8"

# Three or more consecutive newlines collapse to a paragraph break. Nested
# block elements in the templates leave generous whitespace behind.
_BLANK_RUN = re.compile(r"\n{3,}")


class _ContentConverter(MarkdownConverter):
    """A markdownify converter tuned for this site's content templates.

    Carries the ``base_url`` needed to absolutise links; everything else is
    stock markdownify behaviour.
    """

    def __init__(self, base_url: str) -> None:
        """Store the origin used to absolutise relative links."""
        super().__init__(heading_style="ATX")
        self.base_url = base_url

    def convert_summary(self, el: Tag, text: str, parent_tags: object) -> str:
        """Render a <summary> as an h3 so FAQ questions keep their structure."""
        return f"\n\n### {text.strip()}\n\n"

    def convert_a(self, el: Tag, text: str, parent_tags: object) -> str:
        """Unwrap fragment-only anchors; absolutise everything else.

        A link whose href starts with ``#`` points within the page, and the
        anchors it targets do not survive conversion, so the link itself is
        meaningless in Markdown. Two cases, distinguished by the link text:

        - **Decorative** — the hover permalink beside each FAQ question, whose
          entire text is ``#``. Dropped, otherwise every question is trailed by
          a stray ``#`` on its own line.
        - **Real** — an in-page link with words in it. The words are kept and
          only the link is dropped, so no prose goes missing.
        """
        href = el.get("href", "")
        if isinstance(href, str) and href.startswith("#"):
            return "" if text.strip() in {"", "#"} else text
        if isinstance(href, str) and href:
            el["href"] = urljoin(self.base_url, href)
        # markdownify defines convert_a at runtime (verified against 1.2.3) but
        # does not declare it in its shipped types, so mypy cannot see it on the
        # base class. The call is correct; only the annotation is missing.
        return str(super().convert_a(el, text, parent_tags))  # type: ignore[misc]


def html_to_markdown(html: str, base_url: str) -> str:
    """Convert a rendered page's main content to Markdown.

    Args:
        html: The full rendered HTML of a page.
        base_url: Absolute origin (scheme and host) used to resolve relative
            links, e.g. ``https://skiparrainage.com``.

    Returns:
        The page's ``<main>`` content as Markdown, with blank runs collapsed and
        surrounding whitespace stripped. Returns an empty string if the document
        has no ``<main>`` element — every page template extends base.html, which
        has one, so that indicates a template that has broken the layout
        contract rather than a page legitimately without content.
    """
    soup = BeautifulSoup(html, "html.parser")
    main = soup.find("main")
    if main is None:
        logger.warning("No <main> element found; returning empty Markdown")
        return ""
    converter = _ContentConverter(base_url=base_url)
    markdown = converter.convert_soup(main)
    return _BLANK_RUN.sub("\n\n", markdown).strip()
