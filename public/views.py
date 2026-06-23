# Public-facing views: the landing page and the two role-specific
# registration flows.
#
# Registration is role-parameterised: one view serves both the ambassador
# (referrer) and referee paths, keyed on a URL slug. The form and the
# Account/User/Registration creation live in the matching app.

from __future__ import annotations

from django.http import Http404, HttpRequest, HttpResponse
from django.shortcuts import redirect, render

from matching.forms import RegistrationForm
from matching.models import Registration, Season
from matching.services import register_participant

# Map the public URL slug to the stored Role value. Defining the valid slugs
# here keeps unknown roles out of the view (404) and out of the templates.
ROLE_BY_SLUG = {
    "ambassador": Registration.Role.AMBASSADOR,
    "referee": Registration.Role.REFEREE,
}


# The legal documents, keyed by URL slug. Validating against this set keeps
# unknown pages out of the view (404) and out of template lookups.
LEGAL_PAGES = {"privacy", "cookies", "terms"}


def home(request: HttpRequest) -> HttpResponse:
    """Render the public landing page with the two role calls-to-action."""
    return render(
        request,
        "public/home.html",
        {"registration_open": Season.objects.active().exists()},
    )


def legal_page(request: HttpRequest, page: str) -> HttpResponse:
    """Render a static legal document (privacy / cookies / terms)."""
    if page not in LEGAL_PAGES:
        raise Http404("Unknown legal page.")
    return render(request, f"public/legal/{page}.html")


def register(request: HttpRequest, role: str) -> HttpResponse:
    """Render and process the registration form for one role.

    GET renders the role-specific form; POST validates it and, on success,
    creates the participant and redirects to the confirmation page. With no
    active season the page shows a closed-registration state.
    """
    role_value = ROLE_BY_SLUG.get(role)
    if role_value is None:
        raise Http404("Unknown registration role.")

    other_slug = "referee" if role == "ambassador" else "ambassador"
    season = Season.objects.active().first()

    if season is None:
        return render(
            request,
            "public/register_closed.html",
            {"role": role, "role_value": role_value, "other_slug": other_slug},
        )

    if request.method == "POST":
        form = RegistrationForm(role=role_value, season=season, data=request.POST)
        if form.is_valid():
            data = form.cleaned_data
            register_participant(
                season=season,
                role=role_value,
                first_name=data["first_name"],
                last_name=data["last_name"],
                email=data["email"],
                price_category=data["price_category"],
                preferred_location=data["preferred_location"],
                preferred_language=data["preferred_language"],
            )
            return redirect("public:register_done", role=role)
    else:
        form = RegistrationForm(role=role_value, season=season)

    return render(
        request,
        "public/register.html",
        {
            "form": form,
            "role": role,
            "role_value": role_value,
            "other_slug": other_slug,
            "season": season,
        },
    )


def register_done(request: HttpRequest, role: str) -> HttpResponse:
    """Render the post-registration "what happens next" confirmation page."""
    role_value = ROLE_BY_SLUG.get(role)
    if role_value is None:
        raise Http404("Unknown registration role.")
    return render(
        request,
        "public/register_done.html",
        {"role": role, "role_value": role_value},
    )
