# The canonical list of public content pages (SKI-155).
#
# One definition, three consumers:
#
#   - public/sitemaps.py — search-engine index, one entry per language.
#   - public/views/markdown.py — which pages get a .md representation.
#   - templates/llms.txt — the curated index for LLMs.
#
# llms.txt is deliberately NOT generated from this list: its per-page
# annotations are editorial copy, and the llmstxt.org format is a README rather
# than a machine index. Instead, a test asserts that llms.txt links exactly the
# pages named here, so the two cannot drift apart silently.
#
# A page belongs here only if it is public, indexable, and made of prose.
# Transactional routes (register, match, account, tip, pay) are excluded for the
# same reason robots.txt disallows them: they have no standalone value to a
# reader who is not mid-journey, and several leak journey state into a URL.

from dataclasses import dataclass, field

from django.urls import reverse


@dataclass(frozen=True)
class ContentPage:
    """A public, indexable, prose page of the site.

    Attributes:
        url_name: The namespaced URL pattern name, e.g. ``public:faq``.
        kwargs: URL keyword arguments, for parameterised routes (legal pages).
        slug: The path segment used for this page's ``.md`` representation.
            ``/faq/`` becomes ``/faq.md``; the home page uses ``index``.
        priority: Sitemap priority.
        changefreq: Sitemap change frequency.
    """

    url_name: str
    slug: str
    kwargs: dict[str, str] = field(default_factory=dict)
    priority: float = 0.5
    changefreq: str = "monthly"

    @property
    def path(self) -> str:
        """The page's root-relative URL under the active language."""
        return reverse(self.url_name, kwargs=self.kwargs)


# Ordered as they appear in llms.txt: the pages worth reading first, then the
# project background, then the legal set.
CONTENT_PAGES: tuple[ContentPage, ...] = (
    ContentPage(url_name="public:home", slug="index", priority=1.0),
    ContentPage(url_name="public:how_it_works", slug="how-it-works", priority=0.8),
    ContentPage(url_name="public:faq", slug="faq", priority=0.8),
    ContentPage(url_name="public:about", slug="about", priority=0.7),
    ContentPage(url_name="public:colophon", slug="colophon", priority=0.5),
    ContentPage(
        url_name="public:legal",
        slug="legal/privacy",
        kwargs={"page": "privacy"},
        changefreq="yearly",
    ),
    ContentPage(
        url_name="public:legal",
        slug="legal/cookies",
        kwargs={"page": "cookies"},
        changefreq="yearly",
    ),
    ContentPage(
        url_name="public:legal",
        slug="legal/terms",
        kwargs={"page": "terms"},
        changefreq="yearly",
    ),
)

# Slug -> page, for resolving a .md request back to the page it represents.
CONTENT_PAGES_BY_SLUG: dict[str, ContentPage] = {
    page.slug: page for page in CONTENT_PAGES
}

# (view_name, sorted kwargs) -> page, for recognising a content page from an
# already-resolved request without reversing every URL on every response.
_CONTENT_PAGES_BY_MATCH: dict[tuple[str, tuple[tuple[str, str], ...]], ContentPage] = {
    (page.url_name, tuple(sorted(page.kwargs.items()))): page for page in CONTENT_PAGES
}


def page_for_view(view_name: str, kwargs: dict[str, str]) -> ContentPage | None:
    """Return the content page a resolved request refers to, if any.

    Matches on the URL name *and* its arguments, so ``public:legal`` resolves to
    the right one of the three legal pages, and an unknown ``page`` kwarg
    matches nothing.

    Args:
        view_name: ``request.resolver_match.view_name``.
        kwargs: ``request.resolver_match.kwargs``.

    Returns:
        The matching :class:`ContentPage`, or None for any other route.
    """
    key = (view_name, tuple(sorted((k, str(v)) for k, v in kwargs.items())))
    return _CONTENT_PAGES_BY_MATCH.get(key)
