# Sitemap definitions for the public site.
#
# Exposes machine-readable sitemap.xml for search engine indexing. The page list
# lives in public.content.CONTENT_PAGES, shared with the .md routes (SKI-155) —
# transactional flows (register, match), authenticated routes, admin, and HTMX
# partials are excluded there and so never appear here.
#
# django.contrib.sitemaps works without the Sites framework: when the view
# receives an HttpRequest the host is taken from the request, so no SITE_ID
# or django.contrib.sites is needed.

import logging
from typing import TYPE_CHECKING

from django.contrib.sitemaps import Sitemap

from public.content import CONTENT_PAGES, ContentPage

logger = logging.getLogger(__name__)


if TYPE_CHECKING:
    _SitemapBase = Sitemap[ContentPage]
else:
    _SitemapBase = Sitemap


class StaticViewSitemap(_SitemapBase):
    """Sitemap covering the publicly indexable static pages of the site.

    Includes the home page, informational pages, and legal pages.
    Excludes transactional flows (registration, match), account/auth routes,
    admin, and HTMX partial endpoints.

    Internationalised (SKI-153): every item is emitted once per language in
    ``settings.LANGUAGES``, each carrying ``xhtml:link`` alternates for the
    other languages plus ``x-default``. Django activates the item's language
    around the ``location()`` call, so ``reverse()`` returns the prefixed path
    (``/fr/faq/``) without any change to the methods below.
    """

    protocol = "https"

    # Emit one entry per language, with hreflang alternates and an x-default
    # pointing at the default-language URL. x_default requires alternates.
    i18n = True
    alternates = True
    x_default = True

    def items(self) -> list[ContentPage]:
        """Return the list of items to include in the sitemap."""
        return list(CONTENT_PAGES)

    def location(self, item: ContentPage) -> str:
        """Return the URL path for a sitemap item."""
        return item.path

    def priority(self, item: ContentPage) -> float:
        """Return the priority for a sitemap item."""
        return item.priority

    def changefreq(self, item: ContentPage) -> str:
        """Return the change frequency for a sitemap item."""
        return item.changefreq
