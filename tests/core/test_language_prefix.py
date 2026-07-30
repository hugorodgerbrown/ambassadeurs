# Tests for the i18n_patterns language prefix (SKI-153, ADR 0025).
#
# Covers the URL split (which routes carry a language prefix and which do not),
# the hreflang alternates emitted into every page head, and the internationalised
# sitemap. The core guarantee under test: the URL is authoritative for language,
# so /faq/ is English even when the browser asks for French.

import re
from unittest.mock import patch

import pytest
from django.conf import settings
from django.core import mail
from django.test import Client, override_settings
from django.urls import reverse
from django.utils import translation

from core.i18n import language_alternate_paths
from matching.side_effects import _email_proposal
from tests.matching.factories import MatchFactory, RegistrationFactory

_ADMIN_HOST = "admin.example.test"
_PUBLIC_HOST = "public.example.test"
_BOTH_HOSTS = [_ADMIN_HOST, _PUBLIC_HOST]

# Routes that must never carry a language prefix: machine-facing documents, the
# liveness probe, the language switcher, and the Stripe webhook.
UNPREFIXED_PATHS = ["/robots.txt", "/llms.txt", "/sitemap.xml", "/healthz/"]

_HREFLANG_TAG = re.compile(
    r'<link\s+rel="alternate"\s+hreflang="([^"]+)"\s+href="([^"]+)"', re.DOTALL
)


def _hreflang_pairs(body: str) -> dict[str, str]:
    """Return {hreflang: href} for every alternate link tag in a page body."""
    return dict(_HREFLANG_TAG.findall(body))


_SWITCHER_LINK = re.compile(r'<a\s+href="([^"]+)"\s+hreflang="([^"]+)"', re.DOTALL)


def _switcher_targets(body: str) -> dict[str, str]:
    """Return {language: href} for each footer language-switcher link."""
    return {lang: href for href, lang in _SWITCHER_LINK.findall(body)}


@pytest.mark.django_db
@pytest.mark.parametrize(
    "path",
    ["/", "/faq/", "/how-it-works/", "/about/", "/colophon/", "/legal/privacy/"],
)
def test_english_keeps_the_unprefixed_url(path: str) -> None:
    """prefix_default_language=False leaves every English URL exactly as it was."""
    response = Client().get(path)
    assert response.status_code == 200
    assert 'lang="en"' in response.content.decode()


@pytest.mark.django_db
@pytest.mark.parametrize(
    "path",
    ["/", "/faq/", "/how-it-works/", "/about/", "/colophon/", "/legal/privacy/"],
)
def test_french_is_served_under_the_fr_prefix(path: str) -> None:
    """Every English page has a /fr/ counterpart that renders in French."""
    response = Client().get(f"/fr{path}")
    assert response.status_code == 200
    assert 'lang="fr"' in response.content.decode()


@pytest.mark.django_db
def test_url_beats_accept_language() -> None:
    """The URL is authoritative: /faq/ is English even when the browser wants fr.

    This is the property that makes the French site indexable. Before the
    prefix, /faq/ served whichever language the headers negotiated, so a crawler
    had no stable URL for either variant.
    """
    response = Client().get("/faq/", headers={"accept-language": "fr"})
    assert response.status_code == 200
    assert 'lang="en"' in response.content.decode()


@pytest.mark.django_db
def test_fr_prefix_beats_accept_language() -> None:
    """/fr/faq/ is French even when the browser asks for English."""
    response = Client().get("/fr/faq/", headers={"accept-language": "en"})
    assert response.status_code == 200
    assert 'lang="fr"' in response.content.decode()


@pytest.mark.django_db
@pytest.mark.parametrize("path", UNPREFIXED_PATHS)
def test_machine_facing_paths_stay_unprefixed(path: str) -> None:
    """robots.txt, llms.txt, sitemap.xml and healthz keep their fixed paths."""
    assert Client().get(path).status_code == 200


@pytest.mark.django_db
@pytest.mark.parametrize("path", UNPREFIXED_PATHS)
def test_machine_facing_paths_have_no_fr_variant(path: str) -> None:
    """The unprefixed routes sit outside i18n_patterns, so /fr/... 404s."""
    assert Client().get(f"/fr{path}").status_code == 404


@pytest.mark.django_db
def test_reverse_follows_the_active_language() -> None:
    """reverse() yields the prefixed path under an active non-default language.

    Every link in a template follows from this, since templates use {% url %}
    throughout. It is *not* enough for emailed links: those are built by the
    caller before send_templated_email applies its override, so they need an
    explicit one — see TestEmailedLinksCarryTheRecipientLanguage.
    """
    with translation.override("en"):
        assert reverse("public:faq") == "/faq/"
    with translation.override("fr"):
        assert reverse("public:faq") == "/fr/faq/"


class TestHreflangAlternates:
    """The hreflang link tags emitted into every page head."""

    @pytest.mark.django_db
    def test_page_emits_an_alternate_per_language_plus_x_default(self) -> None:
        """A content page advertises en, fr and x-default."""
        body = Client().get("/faq/").content.decode()
        assert 'hreflang="en"' in body
        assert 'hreflang="fr"' in body
        assert 'hreflang="x-default"' in body

    @pytest.mark.django_db
    def test_alternates_point_at_the_right_urls(self) -> None:
        """Each alternate href is the absolute URL of that language's variant."""
        pairs = _hreflang_pairs(Client().get("/faq/").content.decode())
        assert pairs == {
            "en": "http://testserver/faq/",
            "fr": "http://testserver/fr/faq/",
            "x-default": "http://testserver/faq/",
        }

    @pytest.mark.django_db
    def test_french_page_advertises_the_same_alternate_set(self) -> None:
        """The alternates are symmetric — /fr/faq/ advertises the same set."""
        english = _hreflang_pairs(Client().get("/faq/").content.decode())
        french = _hreflang_pairs(Client().get("/fr/faq/").content.decode())
        assert french == english


class TestLanguageAlternatePaths:
    """core.i18n.language_alternate_paths, the helper behind the template tag."""

    def test_returns_one_entry_per_language_plus_x_default(self) -> None:
        """Two configured languages yield three alternates."""
        alternates = language_alternate_paths("/faq/")
        assert [a.hreflang for a in alternates] == ["en", "fr", "x-default"]

    def test_maps_an_english_path_to_both_variants(self) -> None:
        """The default-language path expands to the full set."""
        by_lang = {a.hreflang: a.path for a in language_alternate_paths("/faq/")}
        assert by_lang["en"] == "/faq/"
        assert by_lang["fr"] == "/fr/faq/"

    def test_maps_a_french_path_to_both_variants(self) -> None:
        """A prefixed path expands to the same set — the mapping is symmetric."""
        by_lang = {a.hreflang: a.path for a in language_alternate_paths("/fr/faq/")}
        assert by_lang["en"] == "/faq/"
        assert by_lang["fr"] == "/fr/faq/"

    def test_x_default_points_at_the_default_language(self) -> None:
        """x-default resolves to the unprefixed (English) URL."""
        by_lang = {a.hreflang: a.path for a in language_alternate_paths("/fr/faq/")}
        assert by_lang["x-default"] == by_lang["en"]

    def test_unresolvable_path_is_returned_unchanged(self) -> None:
        """translate_url leaves a non-matching path alone rather than raising."""
        alternates = language_alternate_paths("/robots.txt")
        assert {a.path for a in alternates} == {"/robots.txt"}


class TestLanguageSwitcher:
    """The footer switcher is a link to the other language's URL.

    Once the URL carries the language, switching *is* navigation. The switcher
    was a POST to set_language until the cookie was shown to be inert (see
    TestLanguageCookieIsInert), at which point the form was machinery around a
    value nothing reads.
    """

    @pytest.mark.django_db
    @pytest.mark.parametrize("page", ["/faq/", "/fr/faq/"])
    def test_footer_links_to_every_language_variant(self, page: str) -> None:
        """Both variants are linked from either one, so the set is symmetric."""
        body = Client().get(page).content.decode()
        assert _switcher_targets(body) == {"en": "/faq/", "fr": "/fr/faq/"}

    @pytest.mark.django_db
    @pytest.mark.parametrize(
        ("start", "follow", "expected_lang"),
        [
            ("/faq/", "fr", "fr"),
            ("/fr/faq/", "en", "en"),
        ],
    )
    def test_following_the_link_switches_language(
        self, start: str, follow: str, expected_lang: str
    ) -> None:
        """Following the switcher link renders the other language.

        Both directions: French to English was the one that silently failed
        under the POST implementation.
        """
        client = Client()
        targets = _switcher_targets(client.get(start).content.decode())

        response = client.get(targets[follow])

        assert response.status_code == 200
        assert f'lang="{expected_lang}"' in response.content.decode()

    @pytest.mark.django_db
    def test_switcher_uses_links_not_a_form(self) -> None:
        """No CSRF-bearing form is rendered for what is a navigation action."""
        body = Client().get("/faq/").content.decode()
        assert "set_language" not in body
        assert "/i18n/setlang/" not in body


class TestLanguageCookieIsInert:
    """The django_language cookie no longer selects anything.

    Under ``prefix_default_language=False`` an unprefixed path is pinned to the
    default language, so the cookie cannot override it — which is what retired
    the POST switcher. Asserted rather than assumed, because the switcher's
    design depends on it and because the opposite was believed at first.
    """

    @pytest.mark.django_db
    @pytest.mark.parametrize("path", ["/", "/how-it-works/", "/faq/"])
    def test_cookie_does_not_override_an_unprefixed_url(self, path: str) -> None:
        """A French cookie still gets English on an unprefixed path."""
        client = Client()
        client.cookies[settings.LANGUAGE_COOKIE_NAME] = "fr"
        assert 'lang="en"' in client.get(path).content.decode()

    @pytest.mark.django_db
    def test_prefix_wins_over_a_conflicting_cookie(self) -> None:
        """An English cookie still gets French under /fr/."""
        client = Client()
        client.cookies[settings.LANGUAGE_COOKIE_NAME] = "en"
        assert 'lang="fr"' in client.get("/fr/faq/").content.decode()


class TestPageviewLanguageProperty:
    """The $pageview language property is driven by the URL prefix (SKI-154).

    PostHogPageviewMiddleware is production-only, so it is installed explicitly
    here. That is the point of the test: the unit test in test_middleware.py
    sets the language directly, while this one proves the whole chain —
    URL prefix, LocaleMiddleware, active language, event property.
    """

    @pytest.mark.django_db
    @pytest.mark.parametrize(
        ("path", "expected"), [("/faq/", "en"), ("/fr/faq/", "fr")]
    )
    def test_language_property_follows_the_url(self, path: str, expected: str) -> None:
        """A request to each variant tags the event with that variant's language."""
        middleware = [*settings.MIDDLEWARE, "core.middleware.PostHogPageviewMiddleware"]
        with override_settings(MIDDLEWARE=middleware):
            with patch("core.middleware.capture_event") as mock_capture:
                assert Client().get(path).status_code == 200

        properties = mock_capture.call_args[0][2]
        assert properties["language"] == expected
        assert properties["$current_url"].endswith(path)


class TestAdminHostUrlconfSwap:
    """i18n_patterns coexists with the per-request URLconf swap (ADR 0022).

    Django's docs say i18n_patterns must live in the *root* URLconf.
    AdminHostMiddleware sets request.urlconf per request, so each of
    config.urls_public and config.urls_admin is a root URLconf for its own
    request. That should hold — these tests are here so it is checked rather
    than assumed.
    """

    @pytest.mark.django_db
    @override_settings(ADMIN_HOST=_ADMIN_HOST, ALLOWED_HOSTS=_BOTH_HOSTS)
    def test_public_host_still_serves_both_languages(self) -> None:
        """The swapped-in public URLconf keeps its language prefix working."""
        client = Client()
        assert client.get("/faq/", headers={"host": _PUBLIC_HOST}).status_code == 200
        assert client.get("/fr/faq/", headers={"host": _PUBLIC_HOST}).status_code == 200

    @pytest.mark.django_db
    @override_settings(ADMIN_HOST=_ADMIN_HOST, ALLOWED_HOSTS=_BOTH_HOSTS)
    def test_admin_host_does_not_serve_the_public_french_site(self) -> None:
        """The admin URLconf carries no i18n_patterns, so /fr/ is not the FR home.

        On the admin host the admin is mounted at the root, so /fr/ falls to its
        catch-all and redirects an anonymous visitor to the admin login. What
        matters is that it never renders the public French home page — the
        language prefix does not leak across the URLconf swap.
        """
        response = Client().get("/fr/", headers={"host": _ADMIN_HOST})
        assert response.status_code == 302
        assert response["Location"].startswith("/login/")

    @pytest.mark.django_db
    @override_settings(ADMIN_HOST=_ADMIN_HOST, ALLOWED_HOSTS=_BOTH_HOSTS)
    def test_healthz_reachable_on_both_hosts(self) -> None:
        """The unprefixed liveness probe survives the swap on either host."""
        client = Client()
        for host in _BOTH_HOSTS:
            assert client.get("/healthz/", headers={"host": host}).status_code == 200


class TestEmailedLinksCarryTheRecipientLanguage:
    """Links in notification emails are built under the recipient's language.

    Once the URL carries the language, an unprefixed link *pins* the reader to
    English — so a French registration must receive a /fr/ link. The language
    override inside ``send_templated_email`` does not cover this: it wraps only
    the template render, while the URL is built by the caller beforehand.
    """

    @pytest.mark.django_db
    def test_french_registration_gets_a_prefixed_match_link(self) -> None:
        """A match proposal to a French registration links to /fr/match/...."""
        registration = RegistrationFactory.create(preferred_language="fr")
        match = MatchFactory.create(referee_registration=registration)

        _email_proposal(registration, match)

        assert len(mail.outbox) == 1
        assert f"{settings.BASE_URL}/fr/match/" in mail.outbox[0].body

    @pytest.mark.django_db
    def test_english_registration_gets_an_unprefixed_match_link(self) -> None:
        """An English registration keeps the bare /match/... link."""
        registration = RegistrationFactory.create(preferred_language="en")
        match = MatchFactory.create(referee_registration=registration)

        _email_proposal(registration, match)

        assert len(mail.outbox) == 1
        assert f"{settings.BASE_URL}/match/" in mail.outbox[0].body
        assert "/fr/match/" not in mail.outbox[0].body

    @pytest.mark.django_db
    def test_link_language_ignores_the_ambient_active_language(self) -> None:
        """The recipient's language wins over whatever is active on the thread.

        These handlers run from the *other* party's request, or from the
        expire-matches cron with no request at all, so the ambient language says
        nothing about the recipient.
        """
        registration = RegistrationFactory.create(preferred_language="fr")
        match = MatchFactory.create(referee_registration=registration)

        with translation.override("en"):
            _email_proposal(registration, match)

        assert f"{settings.BASE_URL}/fr/match/" in mail.outbox[0].body


class TestInternationalisedSitemap:
    """The sitemap emits both language variants with alternates."""

    @pytest.mark.django_db
    def test_lists_both_language_variants(self) -> None:
        """Each page appears once per language."""
        body = Client().get("/sitemap.xml").content.decode()
        assert "<loc>https://testserver/faq/</loc>" in body
        assert "<loc>https://testserver/fr/faq/</loc>" in body

    @pytest.mark.django_db
    def test_emits_hreflang_alternates(self) -> None:
        """Entries carry xhtml:link alternates, including x-default."""
        body = Client().get("/sitemap.xml").content.decode()
        assert 'rel="alternate"' in body
        assert 'hreflang="fr"' in body
        assert 'hreflang="x-default"' in body
