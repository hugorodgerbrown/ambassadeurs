# Tests for the Invariant 10 guard on the runtime CSP layer (SKI-171).
#
# The guarantee is core.csp.reject_unsafe_csp_rule, a pre_save receiver that
# fails closed on every write path. The admin overrides in core.admin exist so
# the two paths a human actually takes explain themselves rather than 500ing.
# Both layers are tested: the backstop against direct ORM writes, and the admin
# for the message the operator sees.

import pytest
from csp.models import CspReport, CspRule
from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.test import Client
from django.urls import reverse

from core.csp import UNSAFE_SOURCES, unsafe_source
from tests.accounts.factories import UserFactory

pytestmark = pytest.mark.django_db


def _staff_user() -> User:
    """Create and return a superuser for admin access in tests."""
    user = UserFactory.create(
        username="csp_guard_admin", is_staff=True, is_superuser=True
    )
    user.set_password("password")
    user.save()
    return user


# ---------------------------------------------------------------------------
# unsafe_source — normalisation is the point
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw",
    [
        "'unsafe-inline'",
        "unsafe-inline",
        "inline",
        "INLINE",
        "'UNSAFE-INLINE'",
    ],
)
def test_unsafe_source_catches_every_spelling_of_unsafe_inline(raw: str) -> None:
    """A banned source is caught however it is typed.

    CspRule.clean_value turns a bare "inline" into 'unsafe-inline' and folds
    case, so a guard that matched on the raw input would miss most of the ways
    the value can arrive — including the one the report-conversion path uses.
    """
    assert unsafe_source(raw) == "'unsafe-inline'"


@pytest.mark.parametrize("raw", ["'unsafe-eval'", "unsafe-eval", "eval"])
def test_unsafe_source_catches_unsafe_eval(raw: str) -> None:
    """The same normalisation applies to the script-side value."""
    assert unsafe_source(raw) == "'unsafe-eval'"


@pytest.mark.parametrize(
    "raw",
    [
        "'self'",
        "https://checkout.stripe.com",
        "data:",
        "'none'",
        # Narrower loosenings the invariant does not name. If these are ever
        # banned, the invariant text moves first — see core.csp.UNSAFE_SOURCES.
        "'unsafe-hashes'",
        "'wasm-unsafe-eval'",
    ],
)
def test_unsafe_source_passes_permitted_values(raw: str) -> None:
    """Anything the invariant does not name is left alone."""
    assert unsafe_source(raw) is None


# ---------------------------------------------------------------------------
# The backstop — every write path, including ones we do not own
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("banned", sorted(UNSAFE_SOURCES))
def test_direct_orm_create_is_refused(banned: str) -> None:
    """A shell or third-party create() cannot introduce an unsafe source.

    This is the case the admin overrides cannot reach, and the reason the guard
    is a pre_save receiver rather than form validation.
    """
    with pytest.raises(ValidationError):
        CspRule.objects.create(directive="style-src", value=banned, enabled=True)

    assert not CspRule.objects.filter(value=banned).exists()


def test_bare_inline_via_orm_is_refused() -> None:
    """The normalised form is refused too, not just the quoted spelling.

    csp.models.convert_report writes CspRule.clean_value(report.blocked_uri),
    and a style-src-elem violation reports its blocked_uri as the bare string
    "inline" — so this is the exact value that path would try to save.
    """
    with pytest.raises(ValidationError):
        CspRule.objects.create(directive="style-src", value="inline", enabled=True)


def test_enabling_an_existing_safe_rule_still_works() -> None:
    """The guard runs on every save but only rejects the banned values."""
    rule = CspRule.objects.create(
        directive="img-src", value="https://cdn.example.test", enabled=False
    )

    rule.enabled = True
    rule.save()

    rule.refresh_from_db()
    assert rule.enabled is True


# ---------------------------------------------------------------------------
# The admin — the paths a human takes
# ---------------------------------------------------------------------------


def test_admin_add_form_rejects_unsafe_value_with_a_field_error(
    client: Client,
) -> None:
    """The create form shows an error rather than raising into a 500."""
    client.force_login(_staff_user())

    response = client.post(
        reverse("admin:csp_csprule_add"),
        {"directive": "style-src", "value": "'unsafe-inline'", "enabled": "on"},
    )

    assert response.status_code == 200  # re-rendered form, not a redirect
    assert b"Invariant 10" in response.content
    assert not CspRule.objects.exists()


def test_admin_add_form_accepts_a_permitted_value(client: Client) -> None:
    """The guard does not get in the way of the thing the admin is for."""
    client.force_login(_staff_user())

    response = client.post(
        reverse("admin:csp_csprule_add"),
        {"directive": "frame-src", "value": "https://js.stripe.com", "enabled": "on"},
    )

    assert response.status_code == 302
    assert CspRule.objects.get().value == "https://js.stripe.com"


def test_report_conversion_skips_an_unsafe_violation(client: Client) -> None:
    """The one-click "add rule from violation" route cannot add an unsafe source.

    A style-src-elem violation with blocked_uri "inline" is the commonest report
    there is, and converting it would grant 'unsafe-inline' from a screen that
    looks routine. It is skipped, the report survives, and the operator is told.
    """
    report = CspReport.objects.create(
        effective_directive="style-src", blocked_uri="inline"
    )
    client.force_login(_staff_user())

    response = client.post(
        reverse("admin:csp_cspreport_changelist"),
        {"action": "add_rule", "_selected_action": [str(report.pk)]},
        follow=True,
    )

    assert response.status_code == 200
    assert not CspRule.objects.exists()
    assert CspReport.objects.filter(pk=report.pk).exists()
    assert b"Invariant 10" in response.content


def test_report_conversion_still_converts_a_safe_violation(client: Client) -> None:
    """A safe report converts as before — the guard is not a blanket block."""
    report = CspReport.objects.create(
        effective_directive="img-src", blocked_uri="https://cdn.example.test"
    )
    client.force_login(_staff_user())

    client.post(
        reverse("admin:csp_cspreport_changelist"),
        {"action": "add_rule", "_selected_action": [str(report.pk)]},
        follow=True,
    )

    assert CspRule.objects.get().value == "https://cdn.example.test"
    assert not CspReport.objects.filter(pk=report.pk).exists()


def test_mixed_selection_converts_the_safe_and_skips_the_unsafe(
    client: Client,
) -> None:
    """A selection spanning both is partitioned, not rejected wholesale."""
    safe = CspReport.objects.create(
        effective_directive="img-src", blocked_uri="https://cdn.example.test"
    )
    unsafe = CspReport.objects.create(
        effective_directive="style-src", blocked_uri="inline"
    )
    client.force_login(_staff_user())

    client.post(
        reverse("admin:csp_cspreport_changelist"),
        {
            "action": "add_rule",
            "_selected_action": [str(safe.pk), str(unsafe.pk)],
        },
        follow=True,
    )

    assert CspRule.objects.get().value == "https://cdn.example.test"
    assert CspReport.objects.get().pk == unsafe.pk


# ---------------------------------------------------------------------------
# The baseline — the other half of Invariant 10
# ---------------------------------------------------------------------------


def test_settings_baseline_carries_no_unsafe_source() -> None:
    """CSP_DEFAULTS holds no banned source.

    The runtime layer is guarded at save time; the baseline is a settings edit,
    which no signal can intercept. This is what stops it arriving that way.
    """
    from django.conf import settings

    for directive, values in settings.CSP_DEFAULTS.items():
        for value in values:
            assert unsafe_source(value) is None, f"{directive} carries {value}"
