# Sitemap definitions for the public site.
#
# Exposes machine-readable sitemap.xml for search engine indexing. Only
# static, indexable public pages are listed — transactional flows (register,
# match), authenticated routes, admin, and HTMX partials are excluded.
#
# django.contrib.sitemaps works without the Sites framework: when the view
# receives an HttpRequest the host is taken from the request, so no SITE_ID
# or django.contrib.sites is needed.

import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from django.contrib.sitemaps import Sitemap
from django.urls import reverse

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class _StaticItem:
    """A single item in the static sitemap."""

    url_name: str
    kwargs: dict[str, str] = field(default_factory=dict)
    priority: float = 0.5
    changefreq: str = "monthly"


if TYPE_CHECKING:
    _SitemapBase = Sitemap[_StaticItem]
else:
    _SitemapBase = Sitemap


class StaticViewSitemap(_SitemapBase):
    """Sitemap covering the publicly indexable static pages of the site.

    Includes the home page, informational pages, and legal pages.
    Excludes transactional flows (registration, match), account/auth routes,
    admin, and HTMX partial endpoints.
    """

    protocol = "https"

    _LEGAL_PAGES = ("privacy", "cookies", "terms")

    def items(self) -> list[_StaticItem]:
        """Return the list of items to include in the sitemap."""
        entries: list[_StaticItem] = [
            _StaticItem(
                url_name="public:home",
                priority=1.0,
                changefreq="monthly",
            ),
            _StaticItem(
                url_name="public:how_it_works",
                priority=0.8,
                changefreq="monthly",
            ),
            _StaticItem(
                url_name="public:faq",
                priority=0.8,
                changefreq="monthly",
            ),
            _StaticItem(
                url_name="public:about",
                priority=0.7,
                changefreq="monthly",
            ),
        ]
        for page in self._LEGAL_PAGES:
            entries.append(
                _StaticItem(
                    url_name="public:legal",
                    kwargs={"page": page},
                    priority=0.5,
                    changefreq="yearly",
                )
            )
        return entries

    def location(self, item: _StaticItem) -> str:
        """Return the URL path for a sitemap item."""
        return reverse(item.url_name, kwargs=item.kwargs)

    def priority(self, item: _StaticItem) -> float:
        """Return the priority for a sitemap item."""
        return item.priority

    def changefreq(self, item: _StaticItem) -> str:
        """Return the change frequency for a sitemap item."""
        return item.changefreq
