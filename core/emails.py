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
#
# Delivery (SKI-178, ADR 0029) is split from rendering. The message is rendered
# in the caller's thread — so the active or overridden language and the context
# are exactly as before — and delivery is deferred to ``transaction.on_commit``
# so a rolled-back write never emails anyone. With
# ``settings.EMAIL_SEND_IN_BACKGROUND`` on (production), the SMTP send then
# runs on its own thread, so the request that triggered it returns without
# waiting 1.5–2 s on the mail server. The thread touches no database and no
# translation state; it is non-daemon so a graceful gunicorn restart lets an
# in-flight send finish, and ``EMAIL_TIMEOUT`` bounds how long that can take.

from __future__ import annotations

import functools
import logging
import threading

from django.conf import settings
from django.core.mail import EmailMultiAlternatives
from django.db import transaction
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

    Rendering happens now; delivery is deferred to ``transaction.on_commit``
    and, with ``settings.EMAIL_SEND_IN_BACKGROUND`` on, runs on a background
    thread (SKI-178, see ``_dispatch``). Outside an atomic block the callback
    runs at once. Tests that assert on ``mail.outbox`` from inside a test
    transaction therefore need ``django_capture_on_commit_callbacks``.

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
    transaction.on_commit(functools.partial(_dispatch, message, name))


def _dispatch(message: EmailMultiAlternatives, name: str) -> None:
    """Deliver a rendered message inline or on a background thread.

    Runs as the ``on_commit`` callback registered by ``send_templated_email``.
    With ``settings.EMAIL_SEND_IN_BACKGROUND`` off, the send happens here and
    any SMTP error propagates, as it did before SKI-178. With it on, the send
    is handed to a non-daemon thread and this returns at once; errors are then
    logged by ``_send_in_background``, since there is no caller to raise to.

    Args:
        message: The fully rendered message.
        name: The template name, for logging.
    """
    if settings.EMAIL_SEND_IN_BACKGROUND:
        threading.Thread(
            target=_send_in_background,
            args=(message, name),
            name=f"email-{name}",
            daemon=False,
        ).start()
    else:
        _send(message, name)


def _send(message: EmailMultiAlternatives, name: str) -> None:
    """Send a rendered message and log the template name and recipient count.

    Logs the recipient count only — never the address itself (email is
    sensitive, CLAUDE.md "Core domain"; other modules log pks, not addresses,
    for the same reason).

    Args:
        message: The fully rendered message.
        name: The template name, for logging.
    """
    message.send()
    logger.info("Sent templated email name=%s recipients=%s", name, len(message.to))


def _send_in_background(message: EmailMultiAlternatives, name: str) -> None:
    """Thread target: send a message, logging rather than raising on failure.

    An exception escaping a thread target is printed to stderr by
    ``threading.excepthook`` and otherwise lost, so it is caught and logged
    here for the log pipeline. Only the template name and the exception's
    class name are logged — not its message or traceback, because SMTP errors
    such as ``SMTPRecipientsRefused`` carry the recipient addresses in their
    text, and addresses never go to the logs.

    Args:
        message: The fully rendered message.
        name: The template name, for logging.
    """
    try:
        _send(message, name)
    except Exception as exc:
        logger.error(
            "Failed to send templated email name=%s error=%s",
            name,
            type(exc).__name__,
        )
