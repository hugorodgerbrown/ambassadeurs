# Tests for the accounts management commands.
#
# Covers seed_test_data: idempotency, safety guard (DEBUG=False raises CommandError),
# --force bypass, correct User/Registration/Match counts, EmailAddress verification
# state, and match expires_at direction.

from io import StringIO

import pytest
from allauth.account.models import EmailAddress
from django.contrib.auth.models import User
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import override_settings
from django.utils import timezone

from accounts.management.commands.seed_test_data import SEED_EMAIL_DOMAIN
from matching.models import Match, Registration

pytestmark = pytest.mark.django_db


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _run(**kwargs: object) -> str:
    """Run seed_test_data with DEBUG=True and return stdout."""
    stdout = StringIO()
    with override_settings(DEBUG=True):
        call_command("seed_test_data", stdout=stdout, **kwargs)
    return stdout.getvalue()


# ---------------------------------------------------------------------------
# Safety guard
# ---------------------------------------------------------------------------


def test_raises_command_error_when_debug_false() -> None:
    """Command raises CommandError when DEBUG=False and --force is not passed."""
    with override_settings(DEBUG=False):
        with pytest.raises(CommandError, match="DEBUG is False"):
            call_command("seed_test_data")


def test_force_flag_runs_when_debug_false() -> None:
    """--force allows the command to run even when DEBUG=False."""
    stdout = StringIO()
    with override_settings(DEBUG=False):
        # Should not raise.
        call_command("seed_test_data", force=True, stdout=stdout)
    assert User.objects.filter(email__endswith=f"@{SEED_EMAIL_DOMAIN}").exists()


# ---------------------------------------------------------------------------
# Counts
# ---------------------------------------------------------------------------


def test_expected_user_count() -> None:
    """seed_test_data creates the expected number of seed Users."""
    _run()
    count = User.objects.filter(email__endswith=f"@{SEED_EMAIL_DOMAIN}").count()
    # admin + unverified + amb_queue + ref_queue
    # + proposed pair (2) + pending pair (2) + accepted pair (2)
    # + suspended + withdrawn
    # + declined pair (2) + expired pair (2) + cancelled pair (2)
    # = 1 + 1 + 1 + 1 + 2 + 2 + 2 + 1 + 1 + 2 + 2 + 2 = 18
    assert count == 18


def test_expected_registration_count() -> None:
    """seed_test_data creates the expected number of seed Registrations."""
    _run()
    # All seed users except the superuser (admin) have a Registration = 17.
    reg_count = Registration.objects.filter(
        user__email__endswith=f"@{SEED_EMAIL_DOMAIN}"
    ).count()
    assert reg_count == 17


def test_expected_match_count() -> None:
    """seed_test_data creates the expected number of Match rows."""
    _run()
    # proposed + pending + accepted + declined + expired + cancelled = 6 matches.
    match_count = Match.objects.filter(
        ambassador_registration__user__email__endswith=f"@{SEED_EMAIL_DOMAIN}"
    ).count()
    assert match_count == 6


# ---------------------------------------------------------------------------
# Email address verification
# ---------------------------------------------------------------------------


def test_verified_participants_have_verified_email_address() -> None:
    """All seed participants except the UNVERIFIED one have a verified EmailAddress."""
    _run()
    seed_users = User.objects.filter(email__endswith=f"@{SEED_EMAIL_DOMAIN}").exclude(
        email="unverified@seed.test"
    )
    for user in seed_users:
        ea = EmailAddress.objects.get(user=user, primary=True)
        assert ea.verified, f"Expected verified EmailAddress for {user.email}"


def test_unverified_participant_has_unverified_email_address() -> None:
    """The UNVERIFIED registration's EmailAddress has verified=False."""
    _run()
    user = User.objects.get(email="unverified@seed.test")
    ea = EmailAddress.objects.get(user=user, primary=True)
    assert not ea.verified


def test_unverified_registration_status() -> None:
    """The unverified user's Registration has status=UNVERIFIED."""
    _run()
    user = User.objects.get(email="unverified@seed.test")
    assert user.registration.status == Registration.Status.UNVERIFIED  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# Match expires_at direction
# ---------------------------------------------------------------------------


def test_proposed_match_expires_at_in_future() -> None:
    """PROPOSED matches have expires_at in the future."""
    _run()
    proposed = Match.objects.filter(status=Match.Status.PROPOSED).first()
    assert proposed is not None
    assert proposed.expires_at > timezone.now()


def test_pending_match_expires_at_in_future() -> None:
    """PENDING matches have expires_at in the future."""
    _run()
    pending = Match.objects.filter(status=Match.Status.PENDING).first()
    assert pending is not None
    assert pending.expires_at > timezone.now()


def test_expired_match_expires_at_in_past() -> None:
    """EXPIRED historical matches have expires_at in the past."""
    _run()
    expired = Match.objects.filter(status=Match.Status.EXPIRED).first()
    assert expired is not None
    assert expired.expires_at < timezone.now()


def test_declined_match_expires_at_in_past() -> None:
    """DECLINED historical matches have expires_at in the past."""
    _run()
    declined = Match.objects.filter(status=Match.Status.DECLINED).first()
    assert declined is not None
    assert declined.expires_at < timezone.now()


# ---------------------------------------------------------------------------
# Idempotency
# ---------------------------------------------------------------------------


def test_running_twice_yields_same_counts() -> None:
    """Running the command twice produces identical row counts (no duplication)."""
    _run()
    user_count_1 = User.objects.filter(email__endswith=f"@{SEED_EMAIL_DOMAIN}").count()
    reg_count_1 = Registration.objects.filter(
        user__email__endswith=f"@{SEED_EMAIL_DOMAIN}"
    ).count()
    match_count_1 = Match.objects.filter(
        ambassador_registration__user__email__endswith=f"@{SEED_EMAIL_DOMAIN}"
    ).count()

    _run()  # second run
    user_count_2 = User.objects.filter(email__endswith=f"@{SEED_EMAIL_DOMAIN}").count()
    reg_count_2 = Registration.objects.filter(
        user__email__endswith=f"@{SEED_EMAIL_DOMAIN}"
    ).count()
    match_count_2 = Match.objects.filter(
        ambassador_registration__user__email__endswith=f"@{SEED_EMAIL_DOMAIN}"
    ).count()

    assert user_count_2 == user_count_1
    assert reg_count_2 == reg_count_1
    assert match_count_2 == match_count_1


# ---------------------------------------------------------------------------
# Match statuses present
# ---------------------------------------------------------------------------


def test_all_match_statuses_present() -> None:
    """One match of every status is created by the command."""
    _run()
    statuses = set(Match.objects.values_list("status", flat=True))
    expected = {
        Match.Status.PROPOSED,
        Match.Status.PENDING,
        Match.Status.ACCEPTED,
        Match.Status.DECLINED,
        Match.Status.EXPIRED,
        Match.Status.CANCELLED,
    }
    assert expected.issubset(statuses)


def test_all_registration_statuses_present() -> None:
    """All four Registration.Status values are represented in the seed data."""
    _run()
    seed_statuses = set(
        Registration.objects.filter(
            user__email__endswith=f"@{SEED_EMAIL_DOMAIN}"
        ).values_list("status", flat=True)
    )
    expected = {
        Registration.Status.UNVERIFIED,
        Registration.Status.VERIFIED,
        Registration.Status.WITHDRAWN,
        Registration.Status.SUSPENDED,
    }
    assert expected.issubset(seed_statuses)


# ---------------------------------------------------------------------------
# Admin user
# ---------------------------------------------------------------------------


def test_admin_user_is_superuser_with_no_registration() -> None:
    """The seeded admin user is a superuser and has no Registration."""
    _run()
    admin = User.objects.get(email="admin@seed.test")
    assert admin.is_superuser
    assert admin.is_staff
    with pytest.raises(Registration.DoesNotExist):
        _ = admin.registration  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------


def test_output_contains_seed_domain() -> None:
    """Command stdout mentions the seed email domain."""
    output = _run()
    assert SEED_EMAIL_DOMAIN in output
