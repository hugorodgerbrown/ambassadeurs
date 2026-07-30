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
from public.content import CONTENT_PAGES, CONTENT_PAGES_BY_SLUG, ContentPage

logger = logging.getLogger(__name__)

# Preamble for the concatenated bundle. Machine-facing like llms.txt, and
# deliberately not translated: it frames the document for a reader that has
# just fetched a wall of text, and says where the pieces came from.
_LLMS_FULL_HEADER = """# Ski Parrainage — full site text

> Every public page of Ski Parrainage, concatenated. Ski Parrainage matches
> partners for the 4 Vallées Ambassador Offer: an ambassador who held a prior
> season pass is paired with a genuinely new holder so the two can apply for
> the referral discount together.

Each section below is preceded by an HTML comment naming the page it came
from, so any passage can be cited back to its source URL. The index version
of this document, with one line per page, is at /llms.txt.

---

"""


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


@require_safe
def llms_full_txt(request: HttpRequest) -> HttpResponse:
    """Serve every content page concatenated into one Markdown document.

    Lets an assistant ingest the whole site in a single fetch rather than eight.
    Empirically this endpoint draws several times the traffic of ``llms.txt``,
    which is an index of links rather than the prose itself.

    Each section is labelled with the canonical URL of the page it came from, so
    a reader quoting a passage can cite where it lives. Sections appear in
    registry order — the same order ``llms.txt`` lists them in.

    Unlike ``llms.txt``, this route sits **inside** the language prefix: it is
    translated prose, not machine-facing metadata, so ``/llms-full.txt`` is the
    English bundle and ``/fr/llms-full.txt`` the French one. English keeps the
    conventional root path because the prefix omits the default language.

    One request renders every content page. Crawlers fetch this rarely so the
    cost is accepted rather than cached, but it is the most expensive endpoint
    on the site and worth caching if it ever appears as a hot path.

    Args:
        request: The incoming request.

    Returns:
        A ``text/markdown`` response containing the full site text.
    """
    sections: list[str] = []
    for page in CONTENT_PAGES:
        url = request.build_absolute_uri(page.path)
        body = render_page_as_markdown(request, page)
        sections.append(f"<!-- source: {url} -->\n\n{body}")

    document = _LLMS_FULL_HEADER + "\n\n---\n\n".join(sections) + "\n"
    return HttpResponse(document, content_type=MARKDOWN_CONTENT_TYPE)
