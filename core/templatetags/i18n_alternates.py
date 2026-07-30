# Template tags for emitting hreflang link alternates (SKI-153, ADR 0025).
#
# The computation lives in ``core.i18n.language_alternate_paths`` so it is
# testable without rendering a template; this module only adapts it to the
# template layer and makes the URLs absolute.

import logging

from django import template
from django.template.context import Context

from core.i18n import LanguageAlternate, language_alternate_paths

logger = logging.getLogger(__name__)

register = template.Library()


@register.simple_tag(takes_context=True)
def language_alternates(context: Context) -> list[LanguageAlternate]:
    """Return the current page's ``hreflang`` alternates with absolute URLs.

    ``hreflang`` requires fully-qualified URLs, so each path is expanded against
    the request's own scheme and host — the same approach as the canonical and
    ``og:url`` tags, and correct in every deployment without configuration.

    Returns an empty list when there is no request in the context. That happens
    when a template is rendered outside the request/response cycle: the Django
    403/404 handlers before the context processor runs, and
    ``render_to_string`` in tests. Callers should treat an empty list as "emit
    nothing" rather than an error.

    Args:
        context: The template context, which should carry ``request``.

    Returns:
        One :class:`~core.i18n.LanguageAlternate` per configured language plus
        ``x-default``, each carrying both the root-relative ``path`` and the
        absolute ``url``; empty if there is no request.
    """
    request = context.get("request")
    if request is None:
        return []
    return [
        LanguageAlternate(
            hreflang=alternate.hreflang,
            path=alternate.path,
            url=request.build_absolute_uri(alternate.path),
        )
        for alternate in language_alternate_paths(request.path)
    ]
