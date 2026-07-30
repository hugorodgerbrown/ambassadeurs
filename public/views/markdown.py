# Markdown representations of the public content pages (SKI-155, ADR 0026).
#
# Serves /faq.md alongside /faq/ (and /fr/faq.md alongside /fr/faq/), for the
# curated set in public.content.CONTENT_PAGES only.
#
# The page is produced by running its real view and converting the result, not
# by rendering a second template. That keeps one source of truth: whatever the
# HTML page says, the Markdown says. The alternative — a parallel .md template
# per page — would double both the content and the translation surface.

import logging

from django.http import Http404, HttpRequest, HttpResponse
from django.urls import Resolver404, resolve
from django.views.decorators.http import require_safe

from core.markdown import MARKDOWN_CONTENT_TYPE, html_to_markdown
from public.content import CONTENT_PAGES_BY_SLUG, ContentPage

logger = logging.getLogger(__name__)


def render_page_as_markdown(request: HttpRequest, page: ContentPage) -> str:
    """Run a content page's own view and convert its HTML to Markdown.

    Resolving and calling the view (rather than rendering its template
    directly) means the Markdown reflects whatever context the view builds —
    the homepage's queue snapshot, for instance — instead of silently diverging
    from the page it claims to represent.

    The view is handed the *current* request, whose path is the ``.md`` URL. The
    content views do not read ``request.path``, and only ``<main>`` survives
    conversion, so the head-level tags that do read it are discarded anyway.

    Args:
        request: The incoming request, used for the view call and for building
            absolute link URLs.
        page: The content page to render.

    Returns:
        The page's main content as Markdown.

    Raises:
        Http404: If the page's URL cannot be resolved, which would mean the
            registry references a route that no longer exists.
    """
    try:
        match = resolve(page.path)
    except Resolver404 as exc:
        logger.error("Content page %s does not resolve", page.url_name)
        raise Http404("Unknown content page.") from exc

    response = match.func(request, **match.kwargs)
    if hasattr(response, "render"):
        response.render()
    base_url = request.build_absolute_uri("/")
    return html_to_markdown(response.content.decode(), base_url)


@require_safe
def markdown_page(request: HttpRequest, slug: str) -> HttpResponse:
    """Serve the Markdown representation of one content page.

    Args:
        request: The incoming request.
        slug: The path segment identifying the page, e.g. ``faq`` or
            ``legal/privacy``. ``index`` is the home page.

    Returns:
        A ``text/markdown`` response carrying a ``Link`` header back to the HTML
        representation, and ``Vary: Accept`` so caches keep the two apart.

    Raises:
        Http404: If ``slug`` names no page in the registry. The set is curated:
            transactional routes deliberately have no Markdown form.
    """
    page = CONTENT_PAGES_BY_SLUG.get(slug)
    if page is None:
        raise Http404("No Markdown representation for this path.")

    markdown = render_page_as_markdown(request, page)
    response = HttpResponse(markdown, content_type=MARKDOWN_CONTENT_TYPE)
    # Point back at the HTML twin. A client that reached the .md form first can
    # find the canonical page; combined with the Link header on the HTML side,
    # either representation leads to the other.
    html_url = request.build_absolute_uri(page.path)
    response["Link"] = f'<{html_url}>; rel="alternate"; type="text/html"'
    response["Vary"] = "Accept"
    return response
