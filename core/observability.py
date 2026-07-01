# Server-side error monitoring (PostHog).
#
# Production-only, server-side exception capture (VERB-65). No client-side
# tracking script or cookies are used, so this stays consistent with the cookie
# policy's no-tracking-cookies stance; PostHog is a server-side processor only.
#
# Two coverage paths, both initialised from init_error_monitoring():
#   - Web requests: core.middleware.PostHogExceptionMiddleware calls
#     capture_exception() from process_exception (Django handles request
#     exceptions itself, so they never reach sys.excepthook).
#   - Management commands / crons (expire_matches, run_matching): an uncaught
#     exception propagates to the interpreter excepthook, which PostHog's
#     enable_exception_autocapture hooks.
#
# PII minimisation: email and phone values are redacted from every outbound
# event by the before_send hook, and local-variable capture is disabled so no
# stack-frame locals (which could hold PII) are ever sent.

from __future__ import annotations

import logging
import re
from typing import Any

import posthog
from decouple import config

logger = logging.getLogger(__name__)

# Redaction patterns applied to every string in an outbound event. The phone
# pattern is intentionally broad (a leading + or digit, then 8+ digit/separator
# characters) so partially-formatted numbers are still caught.
_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_PHONE_RE = re.compile(r"\+?\d[\d\s().\-]{7,}\d")

_EMAIL_PLACEHOLDER = "[email redacted]"
_PHONE_PLACEHOLDER = "[phone redacted]"


def _scrub(value: Any) -> Any:
    """Recursively redact email and phone values from an event payload.

    Walks dicts, lists and strings; leaves other scalar types untouched. Email
    redaction runs before phone redaction so an email is never partly rewritten
    by the (broader) phone pattern.
    """
    if isinstance(value, str):
        redacted = _EMAIL_RE.sub(_EMAIL_PLACEHOLDER, value)
        return _PHONE_RE.sub(_PHONE_PLACEHOLDER, redacted)
    if isinstance(value, dict):
        return {key: _scrub(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_scrub(item) for item in value]
    return value


def scrub_pii(event: Any) -> Any:
    """before_send hook: strip email/phone (Swiss data protection) from events.

    Returns the scrubbed event so PostHog still sends it; never returns None, so
    error monitoring is not silently disabled by a scrubbing edge case.
    """
    return _scrub(event)


def init_error_monitoring() -> bool:
    """Configure PostHog server-side exception capture. No-op without a key.

    Reads ``POSTHOG_API_KEY`` / ``POSTHOG_HOST`` via python-decouple. When the
    key is unset (local dev, CI, or a misconfigured deploy) the function returns
    False and PostHog stays disabled rather than erroring. Returns True when the
    client was configured.
    """
    api_key = config("POSTHOG_API_KEY", default="")
    if not api_key:
        return False

    posthog.api_key = api_key
    posthog.host = config("POSTHOG_HOST", default="https://eu.i.posthog.com")
    posthog.before_send = scrub_pii
    posthog.enable_exception_autocapture = True
    # Never capture stack-frame local variables — they can hold PII (email,
    # phone, names) and are not needed to triage an error (data minimisation).
    posthog.capture_exception_code_variables = False
    posthog.setup()
    logger.info("PostHog error monitoring initialised (host=%s)", posthog.host)
    return True


def capture_exception(exception: BaseException) -> None:
    """Send one exception to PostHog, anonymously (no PII distinct_id).

    Safe to call unconditionally: if monitoring was never initialised (no API
    key) PostHog drops the event, and any failure in the reporting path is
    swallowed so error monitoring never breaks the request it is reporting on.
    """
    try:
        posthog.capture_exception(exception)
    except Exception:  # noqa: BLE001 — monitoring must never raise into the caller.
        logger.warning("Failed to report exception to PostHog", exc_info=True)
