# Public-facing views.
#
# Placeholder home page for the scaffold; the real homepage and the two
# role-specific registration flows land in VERB-1..3.

from django.http import HttpRequest, HttpResponse
from django.shortcuts import render


def home(request: HttpRequest) -> HttpResponse:
    """Render the public landing page."""
    return render(request, "public/home.html")
