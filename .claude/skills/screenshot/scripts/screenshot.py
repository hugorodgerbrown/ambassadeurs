"""Capture a PNG screenshot of a named page from the running Ambassadeurs app.

This is the deterministic engine behind the ``screenshot`` skill. It resolves a
Django URL name (or path / full URL) against the project's URL conf, optionally
establishes an authenticated session (admin or any user) or mints a signed
match-access token, drives a headless Chromium via Playwright, and writes a PNG.

The app's own dev server must already be reachable (default ``BASE_URL``,
http://localhost:8000). This script does not start runserver — it shares the
dev database and ``SECRET_KEY`` with it, which is what makes injected session
cookies and signed tokens valid in the running server.

Run it through uv so Playwright is available alongside the project's Django:

    uv run --with playwright python .claude/skills/screenshot/scripts/screenshot.py \
        --name public:register --out .claude/screenshots/register.png

The last stdout line is ``SAVED <path> <resolved-url>`` for the caller to parse.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

import django


def _setup_django() -> None:
    """Configure Django so URL reversing, sessions and tokens are available."""
    # The script lives at .claude/skills/screenshot/scripts/; Python puts that
    # directory on sys.path, not the repo root, so add the root to import config.
    repo_root = Path(__file__).resolve().parents[4]
    if str(repo_root) not in sys.path:
        sys.path.insert(0, str(repo_root))
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.development")
    django.setup()


def _parse_viewport(value: str) -> tuple[int, int]:
    """Translate a viewport keyword or ``WIDTHxHEIGHT`` string into pixels."""
    presets = {"desktop": (1280, 800), "mobile": (390, 844)}
    if value in presets:
        return presets[value]
    try:
        width, height = value.lower().split("x")
        return int(width), int(height)
    except ValueError as exc:  # pragma: no cover - argparse guards typical input
        raise SystemExit(
            f"Invalid --viewport {value!r}; use desktop, mobile or WxH"
        ) from exc


def _parse_kwargs(pairs: list[str]) -> dict[str, str]:
    """Turn repeated ``--arg key=value`` options into a reverse() kwargs dict."""
    kwargs: dict[str, str] = {}
    for pair in pairs:
        if "=" not in pair:
            raise SystemExit(f"Invalid --arg {pair!r}; expected key=value")
        key, value = pair.split("=", 1)
        kwargs[key] = value
    return kwargs


def _resolve_target(args: argparse.Namespace, base_url: str) -> str:
    """Resolve the requested page into an absolute URL to screenshot.

    Precedence: explicit --url, then --match (mints a signed token), then a
    named route (--name + --arg), then a raw --path.
    """
    from django.urls import reverse

    if args.url:
        return args.url

    if args.match:
        from accounts.tokens import make_match_access_token
        from matching.models import Match

        match_pk, _, registration_pk = args.match.partition(":")
        match = Match.objects.get(pk=int(match_pk))
        reg_pk = (
            int(registration_pk)
            if registration_pk
            else match.ambassador_registration_id
        )
        token = make_match_access_token(match.pk, reg_pk)
        return base_url.rstrip("/") + reverse("public:match", args=[token])

    if args.name:
        path = reverse(args.name, kwargs=_parse_kwargs(args.arg))
        return base_url.rstrip("/") + path

    if args.path:
        return base_url.rstrip("/") + "/" + args.path.lstrip("/")

    raise SystemExit("Specify one of --name, --path, --url or --match")


def _build_session_cookie(
    args: argparse.Namespace, base_url: str
) -> dict[str, str] | None:
    """Create a logged-in session for --admin / --login-email and return a cookie.

    The session row is written to the dev database the runserver also reads, so
    the returned ``sessionid`` cookie authenticates subsequent browser requests.
    Returns None when no authentication was requested.
    """
    if not args.admin and not args.login_email:
        return None

    from django.conf import settings
    from django.contrib.auth import (
        BACKEND_SESSION_KEY,
        HASH_SESSION_KEY,
        SESSION_KEY,
        get_user_model,
    )
    from django.contrib.sessions.backends.db import SessionStore

    user_model = get_user_model()
    if args.login_email:
        user = user_model.objects.get(email=args.login_email.lower())
    else:
        user = user_model.objects.filter(is_superuser=True).order_by("pk").first()
        if user is None:
            raise SystemExit(
                "No superuser exists. Create one with "
                "`uv run python manage.py createsuperuser`, or pass --login-email."
            )

    session = SessionStore()
    session[SESSION_KEY] = str(user.pk)
    session[BACKEND_SESSION_KEY] = settings.AUTHENTICATION_BACKENDS[0]
    session[HASH_SESSION_KEY] = user.get_session_auth_hash()
    session.create()
    return {
        "name": settings.SESSION_COOKIE_NAME,
        "value": session.session_key,
        "url": base_url,
    }


def _assert_reachable(base_url: str) -> None:
    """Fail early with actionable guidance if the dev server is not responding."""
    try:
        urllib.request.urlopen(base_url, timeout=5)  # noqa: S310 - localhost dev URL
    except urllib.error.HTTPError:
        return  # any HTTP status means the server answered
    except OSError as exc:
        raise SystemExit(
            f"{base_url} is not reachable ({exc}). Start the dev server first:\n"
            "  uv run python manage.py runserver"
        ) from exc


def _capture(
    url: str,
    out: Path,
    viewport: tuple[int, int],
    cookie: dict[str, str] | None,
    full_page: bool,
    wait_selector: str | None,
) -> None:
    """Render ``url`` in headless Chromium and write a PNG to ``out``."""
    from playwright.sync_api import sync_playwright

    out.parent.mkdir(parents=True, exist_ok=True)
    width, height = viewport

    with sync_playwright() as playwright:
        browser = _launch_chromium(playwright)
        context = browser.new_context(viewport={"width": width, "height": height})
        if cookie:
            context.add_cookies([cookie])
        page = context.new_page()
        page.goto(url, wait_until="networkidle")
        if wait_selector:
            page.wait_for_selector(wait_selector)
        page.screenshot(path=str(out), full_page=full_page)
        browser.close()


def _launch_chromium(playwright):  # type: ignore[no-untyped-def]
    """Launch Chromium, installing the browser binary on first use if missing."""
    try:
        return playwright.chromium.launch()
    except Exception:
        subprocess.run(
            [sys.executable, "-m", "playwright", "install", "chromium"], check=True
        )
        return playwright.chromium.launch()


def main() -> None:
    """Parse arguments, resolve the page, and capture the screenshot."""
    parser = argparse.ArgumentParser(description=__doc__)
    target = parser.add_argument_group("target (choose one)")
    target.add_argument("--name", help="Django URL name, e.g. public:register")
    target.add_argument(
        "--arg",
        action="append",
        default=[],
        help="reverse() kwarg as key=value (repeatable), e.g. --arg page=privacy",
    )
    target.add_argument("--path", help="Absolute path, e.g. /how-it-works/")
    target.add_argument("--url", help="Fully-qualified URL (overrides base URL)")
    target.add_argument(
        "--match",
        metavar="MATCH_PK[:REG_PK]",
        help="Mint a signed match-access token for this match (defaults to the "
        "ambassador side) and screenshot public:match",
    )

    auth = parser.add_argument_group("authentication (optional)")
    auth.add_argument(
        "--admin",
        action="store_true",
        help="Authenticate as the first superuser (for /admin/ pages)",
    )
    auth.add_argument("--login-email", help="Authenticate as the user with this email")

    parser.add_argument(
        "--viewport",
        default="desktop",
        help="desktop (1280x800), mobile (390x844), or WIDTHxHEIGHT",
    )
    parser.add_argument(
        "--full-page",
        action="store_true",
        help="Capture the full scrollable page, not just the viewport",
    )
    parser.add_argument(
        "--wait-selector", help="Wait for this CSS selector before capturing"
    )
    parser.add_argument("--base-url", help="Override settings.BASE_URL")
    parser.add_argument("--out", required=True, type=Path, help="Output PNG path")
    args = parser.parse_args()

    _setup_django()
    from django.conf import settings

    base_url = args.base_url or settings.BASE_URL
    url = _resolve_target(args, base_url)
    cookie = _build_session_cookie(args, base_url)
    _assert_reachable(base_url)
    _capture(
        url,
        args.out,
        _parse_viewport(args.viewport),
        cookie,
        args.full_page,
        args.wait_selector,
    )

    print(f"SAVED {args.out} {url}")


if __name__ == "__main__":
    main()
