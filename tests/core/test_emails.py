# Tests for core.emails: normalise_email and send_templated_email.
#
# send_templated_email tests assert structural facts only (recipient count,
# non-empty single-line subject, exactly one non-empty text/html alternative,
# escaping behaviour) — never translated string literals, because the test
# env compiles no .mo catalogues and gettext falls back to the English source.

import pytest
from django.core import mail

from core.emails import normalise_email, send_templated_email

pytestmark = pytest.mark.django_db


def test_normalise_email_lowercases() -> None:
    """normalise_email returns the address in lowercase."""
    assert normalise_email("Alice@Example.COM") == "alice@example.com"
    assert normalise_email("ALICE@EXAMPLE.COM") == "alice@example.com"


def test_normalise_email_strips_leading_trailing_whitespace() -> None:
    """normalise_email strips leading and trailing whitespace."""
    assert normalise_email("  alice@example.com  ") == "alice@example.com"
    assert normalise_email("\talice@example.com\t") == "alice@example.com"
    assert normalise_email("\nalice@example.com\n") == "alice@example.com"


def test_normalise_email_removes_null_byte() -> None:
    """normalise_email removes the NUL control character."""
    assert normalise_email("alice\x00@example.com") == "alice@example.com"


def test_normalise_email_removes_interior_control_chars() -> None:
    """normalise_email removes non-printable control characters anywhere."""
    # Interior tab (\t is not printable, so it is stripped out).
    assert normalise_email("alice\t@example.com") == "alice@example.com"
    # Embedded newline.
    assert normalise_email("alice\n@example.com") == "alice@example.com"
    # Carriage return.
    assert normalise_email("alice\r@example.com") == "alice@example.com"


def test_normalise_email_removes_non_printable_unicode() -> None:
    """normalise_email removes zero-width and non-printable Unicode code points."""
    # Zero-width space (U+200B) — not printable.
    assert normalise_email("alice​@example.com") == "alice@example.com"
    # Soft hyphen (U+00AD) — not printable.
    assert normalise_email("alice­@example.com") == "alice@example.com"


def test_normalise_email_leaves_clean_address_unchanged() -> None:
    """normalise_email is a no-op on an already-normalised address."""
    address = "alice@example.com"
    assert normalise_email(address) == address


def test_normalise_email_is_idempotent() -> None:
    """Applying normalise_email twice produces the same result as once."""
    inputs = [
        "  Alice@Example.COM  ",
        "alice\x00@example.com",
        "ALICE@EXAMPLE.COM",
        "alice@example.com",
        "\talice@example.com\n",
    ]
    for raw in inputs:
        once = normalise_email(raw)
        twice = normalise_email(once)
        assert once == twice, f"Not idempotent for {raw!r}"


# ---------------------------------------------------------------------------
# send_templated_email (VERB-108)
# ---------------------------------------------------------------------------

# The "login" template triple exists for real (accounts/services.py) and
# expects this context, so it doubles as a realistic fixture for these
# structural tests.
_LOGIN_CONTEXT = {
    "first_name": "Ada",
    "verify_url": "http://testserver/account/login/faketoken/",
    "expiry_hours": 1,
}


def test_send_templated_email_sends_one_message() -> None:
    """send_templated_email queues exactly one EmailMultiAlternatives."""
    mail.outbox.clear()

    send_templated_email("login", _LOGIN_CONTEXT, ["ada@example.com"])

    assert len(mail.outbox) == 1
    assert mail.outbox[0].to == ["ada@example.com"]


def test_send_templated_email_subject_is_non_empty_single_line() -> None:
    """The rendered subject is non-empty and collapsed to a single line."""
    mail.outbox.clear()

    send_templated_email("login", _LOGIN_CONTEXT, ["ada@example.com"])

    subject = mail.outbox[0].subject
    assert subject
    assert "\n" not in subject


def test_send_templated_email_attaches_one_non_empty_html_alternative() -> None:
    """Exactly one non-empty text/html alternative is attached."""
    mail.outbox.clear()

    send_templated_email("login", _LOGIN_CONTEXT, ["ada@example.com"])

    message = mail.outbox[0]
    html_alternatives = [
        content for content, mimetype in message.alternatives if mimetype == "text/html"
    ]
    assert len(html_alternatives) == 1
    assert html_alternatives[0].strip()
    # The text and HTML parts are not the same rendering.
    assert html_alternatives[0] != message.body


def test_send_templated_email_escapes_html_but_not_text() -> None:
    """User-supplied context is escaped in the HTML part, not in the text part.

    ``login/body.txt`` interpolates ``first_name`` directly and is wrapped in
    ``{% autoescape off %}`` (it is plain text, not HTML); ``login/body.html``
    keeps Django's default autoescaping (Invariant 4).
    """
    mail.outbox.clear()
    context = {**_LOGIN_CONTEXT, "first_name": "<script>alert(1)</script>"}

    send_templated_email("login", context, ["ada@example.com"])

    message = mail.outbox[0]
    html_body = next(
        content for content, mimetype in message.alternatives if mimetype == "text/html"
    )
    assert "<script>alert(1)</script>" not in html_body
    assert "&lt;script&gt;" in html_body
    assert "<script>alert(1)</script>" in message.body


def test_send_templated_email_accepts_language_argument() -> None:
    """send_templated_email accepts an explicit language without raising."""
    mail.outbox.clear()

    send_templated_email("login", _LOGIN_CONTEXT, ["ada@example.com"], language="fr")

    assert len(mail.outbox) == 1
