# Email helpers: address normalisation and structured template dispatch.
#
# ``normalise_email`` is the canonical entry point for cleaning an email
# address before storage or lookup.  All entry points in the codebase (forms,
# services, views, hashing) route through here so that the stored value and
# every derived value (e.g. the blind-index hash) are always produced from the
# same normalised form (CLAUDE.md invariant 5).
#
# ``send_templated_email`` is the single shared helper for sending a
# multipart (text + HTML) email from a named template triple under
# ``templates/email/<name>/`` (VERB-108, ADR 0019). It replaces the two
# previous ad-hoc patterns — flat ``render_to_string`` + ``send_mail`` calls in
# ``accounts/services.py``, and inline ``gettext()`` strings in
# ``matching/side_effects.py`` — with one convention so every outgoing email is
# both translatable and has an HTML alternative.

from __future__ import annotations

import logging

from django.conf import settings
from django.core.mail import EmailMultiAlternatives
from django.template.loader import render_to_string
from django.utils import translation

logger = logging.getLogger(__name__)


def normalise_email(email: str) -> str:
    """Return a fully-normalised email address.

    The normalisation steps, in order:

    1. Remove any non-printable / control characters (U+0000–U+001F, DEL,
       and Unicode non-printable code points).  U+0020 SPACE is printable so
       interior spaces are preserved at this stage; they are removed by the
       subsequent strip.
    2. Strip leading and trailing whitespace.
    3. Lowercase.

    The function is idempotent: ``normalise_email(normalise_email(x)) ==
    normalise_email(x)`` for all inputs.

    Args:
        email: The raw email address to normalise.

    Returns:
        The normalised email address string.
    """
    printable = "".join(ch for ch in email if ch.isprintable())
    return printable.strip().lower()


def send_templated_email(
    name: str,
    context: dict[str, object],
    to: list[str],
    language: str | None = None,
) -> None:
    """Render and send a multipart (text + HTML) email from a named template.

    Renders three templates under ``templates/email/<name>/``:

    - ``subject.txt`` — collapsed to a single line (guards against a stray
      leading newline from ``{% load i18n %}``, and against header injection
      from a multi-line subject).
    - ``body.txt`` — the plain-text part, stripped of leading/trailing
      whitespace.
    - ``body.html`` — the HTML alternative part.

    None of the three are rendered with a ``request`` in context — email
    templates deliberately opt out of context processors (e.g. a stray
    ``RequestFactory`` request would otherwise trigger the debug-toolbar
    processor). If ``language`` is given, all three are rendered under
    ``translation.override(language)``; otherwise they render in whatever
    language is currently active (matching prior per-view behaviour for the
    accounts emails, which render in the request's active language).

    Args:
        name: The template directory name under ``templates/email/``, e.g.
            ``"login"`` or ``"match_proposed"``.
        context: The template context, shared across all three renders.
        to: The list of recipient email addresses.
        language: An optional language code to render under; ``None`` uses
            the currently active language.

    Returns:
        None.
    """

    def _render() -> tuple[str, str, str]:
        """Render the subject/text/HTML triple in the currently active language."""
        subject = " ".join(
            render_to_string(f"email/{name}/subject.txt", context).split()
        )
        body = render_to_string(f"email/{name}/body.txt", context).strip()
        html = render_to_string(f"email/{name}/body.html", context)
        return subject, body, html

    if language is not None:
        with translation.override(language):
            subject, body, html = _render()
    else:
        subject, body, html = _render()

    message = EmailMultiAlternatives(subject, body, settings.DEFAULT_FROM_EMAIL, to)
    message.attach_alternative(html, "text/html")
    message.send()
    logger.info("Sent templated email name=%s to=%s", name, to)
