# Tests for custom HTTP error page templates (VERB-64).
#
# Covers:
#   - templates/404.html renders correctly via Django's 404 handler
#   - templates/403.html renders correctly via render_to_string
#   - templates/500.html exists and is a self-contained HTML document
#
# Note: the test environment has no compiled .mo catalogues so gettext falls
# back to the English source strings. All assertions use English strings only.

import os

import pytest
from django.template.loader import render_to_string
from django.test import Client, override_settings
from django.urls import reverse

pytestmark = pytest.mark.django_db


class TestNotFoundTemplate:
    """The 404 template renders with the correct content."""

    @override_settings(DEBUG=False)
    def test_404_status_and_template(self) -> None:
        """A request to an unknown URL returns 404 with the custom template."""
        client = Client(raise_request_exception=False)
        response = client.get("/this-path-does-not-exist-at-all/")
        assert response.status_code == 404
        assert b"Page not found" in response.content

    @override_settings(DEBUG=False)
    def test_404_contains_home_link(self) -> None:
        """The 404 page includes a link back to the homepage."""
        client = Client(raise_request_exception=False)
        response = client.get("/this-path-does-not-exist-at-all/")
        assert response.status_code == 404
        home_url = reverse("public:home").encode()
        assert home_url in response.content

    @override_settings(DEBUG=False)
    def test_404_uses_custom_template(self) -> None:
        """The 404 response is served by templates/404.html."""
        client = Client(raise_request_exception=False)
        response = client.get("/this-path-does-not-exist-at-all/")
        template_names = [t.name for t in response.templates]
        assert "404.html" in template_names


class TestForbiddenTemplate:
    """The 403 template renders with the correct content."""

    def test_403_render_to_string(self) -> None:
        """403.html renders without error and contains expected copy."""
        output = render_to_string("403.html")
        assert "Access denied" in output

    def test_403_contains_home_link(self) -> None:
        """The 403 template includes a link back to the homepage."""
        output = render_to_string("403.html")
        home_url = reverse("public:home")
        assert home_url in output

    def test_403_contains_permission_copy(self) -> None:
        """The 403 template explains the access restriction."""
        output = render_to_string("403.html")
        assert "permission" in output.lower()


class TestServerErrorTemplate:
    """The 500 template is a valid standalone HTML document."""

    def test_500_template_exists(self) -> None:
        """templates/500.html is present on disk."""
        from django.template.loader import get_template

        tpl = get_template("500.html")
        assert tpl is not None

    def test_500_is_standalone_html(self) -> None:
        """500.html is a self-contained document (no Django template inheritance)."""
        template_path = os.path.join(
            os.path.dirname(__file__),
            "..",
            "..",
            "templates",
            "500.html",
        )
        with open(os.path.abspath(template_path)) as fh:
            source = fh.read()
        # Must not extend base.html — the 500 handler has no context processors.
        assert "{% extends" not in source
        # Must not use url tag — urlconf may be unavailable.
        assert "{% url" not in source
        # Must contain a hard link to / for return navigation.
        assert 'href="/"' in source

    def test_500_contains_bilingual_copy(self) -> None:
        """500.html has both English and French error copy hard-coded."""
        template_path = os.path.join(
            os.path.dirname(__file__),
            "..",
            "..",
            "templates",
            "500.html",
        )
        with open(os.path.abspath(template_path)) as fh:
            source = fh.read()
        assert "Something went wrong" in source
        assert "inattendue" in source
