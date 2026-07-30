# Tests for the Markdown representations (SKI-155, ADR 0026).
#
# Covers the .md routes, the conversion fixes that stock markdownify does not
# make, the Link/<link> advertising on both representations, and same-URL
# content negotiation end to end through the middleware.

import re

import pytest
from django.test import Client

from public.content import CONTENT_PAGES, CONTENT_PAGES_BY_SLUG

# Every curated page, as (slug, html_path) — the pairing the .md routes promise.
PAGE_SLUGS = [page.slug for page in CONTENT_PAGES]


class TestMarkdownRoutes:
    """A .md representation exists for each curated page, and only those."""

    @pytest.mark.django_db
    @pytest.mark.parametrize("slug", PAGE_SLUGS)
    def test_every_content_page_has_a_markdown_route(self, slug: str) -> None:
        """Each registry entry is served as text/markdown."""
        response = Client().get(f"/{slug}.md")
        assert response.status_code == 200
        assert response["Content-Type"].startswith("text/markdown")
        assert response.content.strip()

    @pytest.mark.django_db
    @pytest.mark.parametrize("slug", PAGE_SLUGS)
    def test_french_markdown_lives_under_the_language_prefix(self, slug: str) -> None:
        """The .md routes sit inside i18n_patterns, so /fr/faq.md works too."""
        response = Client().get(f"/fr/{slug}.md")
        assert response.status_code == 200
        assert response["Content-Type"].startswith("text/markdown")

    @pytest.mark.django_db
    @pytest.mark.parametrize(
        "slug", ["register", "register/role", "account", "match/abc", "tip", "nonsense"]
    )
    def test_uncurated_paths_have_no_markdown(self, slug: str) -> None:
        """Transactional and unknown routes 404 rather than converting anything."""
        assert Client().get(f"/{slug}.md").status_code == 404

    @pytest.mark.django_db
    def test_markdown_route_rejects_post(self) -> None:
        """The .md view is safe-methods only."""
        assert Client().post("/faq.md").status_code == 405

    @pytest.mark.django_db
    def test_french_markdown_differs_from_english(self) -> None:
        """The two language variants really do render in different languages."""
        english = Client().get("/how-it-works.md").content.decode()
        french = Client().get("/fr/how-it-works.md").content.decode()
        assert english != french


class TestConversionQuality:
    """The three fixes stock markdownify does not make on this site's markup."""

    @pytest.mark.django_db
    def test_faq_questions_become_headings(self) -> None:
        """<summary> is promoted to h3 so the FAQ keeps its structure.

        Without this every question collapses into a run-on paragraph with its
        own answer, which is the structure an FAQ exists to have.
        """
        body = Client().get("/faq.md").content.decode()
        assert "### What is the 4 Vallées Ambassador Offer?" in body

    @pytest.mark.django_db
    def test_permalink_anchors_are_dropped(self) -> None:
        """The decorative '#' hover links leave nothing behind."""
        body = Client().get("/faq.md").content.decode()
        assert "](#" not in body
        assert not re.search(r"^#$", body, re.MULTILINE)

    @pytest.mark.django_db
    def test_links_are_absolute(self) -> None:
        """A .md file is read standalone, so relative links would be unfollowable."""
        body = Client().get("/faq.md").content.decode()
        targets = re.findall(r"\]\(([^)]+)\)", body)
        assert targets
        assert all(target.startswith("http") for target in targets)

    @pytest.mark.django_db
    def test_chrome_is_excluded(self) -> None:
        """Only <main> is converted — no nav, footer, or language switcher."""
        body = Client().get("/faq.md").content.decode()
        assert "Skip to main content" not in body
        assert "Independent project" not in body

    @pytest.mark.django_db
    def test_starts_with_the_page_heading(self) -> None:
        """The document opens with the page's h1, not stray whitespace."""
        body = Client().get("/faq.md").content.decode()
        assert body.startswith("# ")


class TestAlternateAdvertising:
    """Both representations point at each other, in headers and in markup."""

    @pytest.mark.django_db
    def test_html_carries_a_link_header_to_the_markdown(self) -> None:
        """Catches headless fetchers that never parse the body."""
        response = Client().get("/faq/")
        assert response["Link"] == (
            '<http://testserver/faq.md>; rel="alternate"; type="text/markdown"'
        )

    @pytest.mark.django_db
    def test_html_carries_a_link_tag_to_the_markdown(self) -> None:
        """Catches crawlers that parse the DOM but ignore headers."""
        body = Client().get("/faq/").content.decode()
        assert 'rel="alternate"' in body
        assert 'type="text/markdown"' in body
        assert "http://testserver/faq.md" in body

    @pytest.mark.django_db
    def test_markdown_carries_a_link_header_back_to_the_html(self) -> None:
        """A client that found the .md first can reach the canonical page."""
        response = Client().get("/faq.md")
        assert response["Link"] == (
            '<http://testserver/faq/>; rel="alternate"; type="text/html"'
        )

    @pytest.mark.django_db
    @pytest.mark.parametrize("path", ["/faq/", "/faq.md"])
    def test_both_representations_vary_on_accept(self, path: str) -> None:
        """Caches must keep the two apart."""
        assert "Accept" in Client().get(path)["Vary"]

    @pytest.mark.django_db
    def test_non_content_pages_advertise_nothing(self) -> None:
        """A transactional page has no Markdown twin, so it claims none."""
        response = Client().get("/register/role/")
        assert response.status_code == 200
        assert "Link" not in response
        assert 'type="text/markdown"' not in response.content.decode()

    @pytest.mark.django_db
    def test_visually_hidden_pointer_is_present_and_aria_hidden(self) -> None:
        """The 'pasted into ChatGPT' path — hidden from sight and from readers."""
        body = Client().get("/faq/").content.decode()
        pattern = r'<div class="sr-only" aria-hidden="true">(.*?)</div>'
        match = re.search(pattern, body, re.DOTALL)
        assert match
        assert "http://testserver/faq.md" in match.group(1)


class TestSameUrlNegotiation:
    """Accept-driven negotiation on the HTML URL, end to end."""

    @pytest.mark.django_db
    @pytest.mark.parametrize(
        ("accept", "expected_type"),
        [
            ("text/markdown", "text/markdown"),
            ("text/markdown, text/html", "text/markdown"),
            ("text/html, text/markdown;q=0.5", "text/html"),
            ("*/*", "text/html"),
            ("text/html,application/xhtml+xml,*/*;q=0.8", "text/html"),
            ("", "text/html"),
        ],
    )
    def test_representation_matches_the_accept_header(
        self, accept: str, expected_type: str
    ) -> None:
        """The same URL serves whichever representation the client prefers."""
        response = Client().get("/faq/", headers={"accept": accept})
        assert response.status_code == 200
        assert response["Content-Type"].startswith(expected_type)

    @pytest.mark.django_db
    def test_negotiated_markdown_matches_the_md_route(self) -> None:
        """Both paths to Markdown produce the same document."""
        negotiated = Client().get("/faq/", headers={"accept": "text/markdown"})
        direct = Client().get("/faq.md")
        assert negotiated.content == direct.content

    @pytest.mark.django_db
    def test_unacceptable_type_returns_406(self) -> None:
        """Silently substituting a refused type would hide the caller's bug."""
        response = Client().get("/faq/", headers={"accept": "application/json"})
        assert response.status_code == 406

    @pytest.mark.django_db
    def test_md_url_ignores_the_accept_header(self) -> None:
        """An explicit .md URL overrides negotiation, including the 406 check."""
        response = Client().get("/faq.md", headers={"accept": "application/json"})
        assert response.status_code == 200
        assert response["Content-Type"].startswith("text/markdown")

    @pytest.mark.django_db
    def test_non_content_pages_are_not_negotiated(self) -> None:
        """Asking for Markdown on a transactional page still returns HTML."""
        response = Client().get("/register/role/", headers={"accept": "text/markdown"})
        assert response.status_code == 200
        assert response["Content-Type"].startswith("text/html")


class TestRegistryAgreement:
    """llms.txt is hand-written; a test stops it drifting from the registry."""

    @pytest.mark.django_db
    def test_llms_txt_links_exactly_the_registry_pages(self) -> None:
        """Every curated page is listed, and nothing else is claimed as content.

        llms.txt is deliberately not generated — its annotations are editorial —
        so this is what keeps the curated set and the published index honest.
        """
        body = Client().get("/llms.txt").content.decode()
        listed = {
            target.removeprefix("http://testserver")
            for target in re.findall(r"\]\((http://testserver[^)]*)\)", body)
        }
        expected = {CONTENT_PAGES_BY_SLUG[slug].path for slug in PAGE_SLUGS}
        assert expected, "the content registry is empty"
        assert expected <= listed, f"llms.txt is missing {expected - listed}"
        # Anything else it links must be a machine-facing document or a pointer
        # to another representation — never a content page missing from the
        # registry, which is the drift this test exists to catch.
        machine_facing = {
            "/robots.txt",
            "/sitemap.xml",
            "/llms-full.txt",
        }
        extra = {
            path
            for path in listed - expected
            if path not in machine_facing and not path.endswith(".md")
        }
        assert not extra, f"llms.txt links pages missing from the registry: {extra}"

    @pytest.mark.django_db
    def test_sitemap_lists_exactly_the_registry_pages(self) -> None:
        """The sitemap is generated from the registry, so it cannot drift."""
        body = Client().get("/sitemap.xml").content.decode()
        for page in CONTENT_PAGES:
            assert f"<loc>https://testserver{page.path}</loc>" in body


class TestLlmsFullTxt:
    """The whole site as one Markdown document (SKI-156)."""

    @pytest.mark.django_db
    def test_served_as_markdown(self) -> None:
        """The bundle is 200 text/markdown."""
        response = Client().get("/llms-full.txt")
        assert response.status_code == 200
        assert response["Content-Type"].startswith("text/markdown")

    @pytest.mark.django_db
    def test_contains_every_content_page(self) -> None:
        """Every registry page contributes a section, labelled by source URL."""
        body = Client().get("/llms-full.txt").content.decode()
        for page in CONTENT_PAGES:
            assert f"<!-- source: http://testserver{page.path} -->" in body

    @pytest.mark.django_db
    def test_sections_match_the_individual_md_routes(self) -> None:
        """One conversion path: the bundle says what the .md routes say."""
        bundle = Client().get("/llms-full.txt").content.decode()
        faq = Client().get("/faq.md").content.decode()
        assert faq in bundle

    @pytest.mark.django_db
    def test_french_edition_differs(self) -> None:
        """The bundle sits inside the language prefix, so /fr/ is French."""
        english = Client().get("/llms-full.txt").content.decode()
        french = Client().get("/fr/llms-full.txt").content.decode()
        assert french != english
        assert "<!-- source: http://testserver/fr/faq/ -->" in french

    @pytest.mark.django_db
    def test_rejects_post(self) -> None:
        """Safe methods only."""
        assert Client().post("/llms-full.txt").status_code == 405

    @pytest.mark.django_db
    def test_llms_txt_advertises_the_bundle(self) -> None:
        """The index points at the full-text document, or nobody finds it."""
        body = Client().get("/llms.txt").content.decode()
        assert "llms-full.txt" in body


class TestHomepageHero:
    """The homepage's hero sits outside <main> but is content, not chrome."""

    @pytest.mark.django_db
    def test_index_md_leads_with_the_page_heading(self) -> None:
        """/index.md carries the h1 that lives in the hero block.

        The hero is outside <main> so it can break out of the layout column.
        Converting <main> alone left the homepage headless — missing exactly the
        sentences an assistant needs to answer "what is this service?".
        """
        body = Client().get("/index.md").content.decode()
        assert "# Find your season-pass match" in body

    @pytest.mark.django_db
    def test_index_md_carries_the_opening_pitch(self) -> None:
        """The lead paragraph beneath the headline survives too."""
        body = Client().get("/index.md").content.decode()
        assert "Facebook group" in body

    @pytest.mark.django_db
    def test_image_sources_are_absolute(self) -> None:
        """Image references resolve standalone, like links."""
        body = Client().get("/index.md").content.decode()
        sources = re.findall(r"!\[[^\]]*\]\(([^)]+)\)", body)
        assert sources
        assert all(src.startswith("http") for src in sources)
