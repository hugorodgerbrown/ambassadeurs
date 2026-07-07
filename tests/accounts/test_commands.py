# Tests for the accounts management commands.
#
# Covers seed_test_data: idempotency, safety guard (DEBUG=False raises CommandError),
# --force bypass, correct User/Registration/Match counts, registration verified
# state (no longer derived from allauth EmailAddress — VERB-46), and match
# expires_at direction.

from io import StringIO

import pytest
from django.contrib.auth.models import User
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import override_settings
from django.utils import timezone

from accounts.management.commands.seed_test_data import (
    SEED_EMAIL_DOMAIN,
    SEED_NOTIFICATION_MARKER,
)
from core.models import Notification
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
    # + suspended + withdrawn + paused
    # + declined pair (2) + expired pair (2) + cancelled pair (2)
    # = 1 + 1 + 1 + 1 + 2 + 2 + 2 + 1 + 1 + 1 + 2 + 2 + 2 = 19
    assert count == 19


def test_expected_registration_count() -> None:
    """seed_test_data creates the expected number of seed Registrations."""
    _run()
    # All seed users except the superuser (admin) have a Registration = 18.
    reg_count = Registration.objects.filter(
        user__email__endswith=f"@{SEED_EMAIL_DOMAIN}"
    ).count()
    assert reg_count == 18


def test_expected_match_count() -> None:
    """seed_test_data creates the expected number of Match rows."""
    _run()
    # proposed + pending + accepted + declined + expired + cancelled = 6 matches.
    match_count = Match.objects.filter(
        ambassador_registration__user__email__endswith=f"@{SEED_EMAIL_DOMAIN}"
    ).count()
    assert match_count == 6


# ---------------------------------------------------------------------------
# Verified state (VERB-46: derived from Registration.status, not EmailAddress)
# ---------------------------------------------------------------------------


def test_verified_participants_have_verified_registration_status() -> None:
    """All seed participants except the UNVERIFIED one have a non-UNVERIFIED status."""
    _run()
    seed_registrations = Registration.objects.filter(
        user__email__endswith=f"@{SEED_EMAIL_DOMAIN}"
    ).exclude(user__email="unverified@seed.test")
    for reg in seed_registrations:
        assert reg.status != Registration.Status.UNVERIFIED, (
            f"Expected non-UNVERIFIED status for {reg.user.email}, got {reg.status}"
        )


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
    """Every Registration.Status value is represented in the seed data."""
    _run()
    seed_statuses = set(
        Registration.objects.filter(
            user__email__endswith=f"@{SEED_EMAIL_DOMAIN}"
        ).values_list("status", flat=True)
    )
    expected = {
        Registration.Status.UNVERIFIED,
        Registration.Status.VERIFIED,
        Registration.Status.PAUSED,
        Registration.Status.WITHDRAWN,
        Registration.Status.SUSPENDED,
    }
    assert expected.issubset(seed_statuses)


# ---------------------------------------------------------------------------
# Historical pairs mirror the real service outcomes (ADR 0013 / ADR 0007)
# ---------------------------------------------------------------------------


def test_declined_pair_mirrors_decline_flow() -> None:
    """The decliner is PAUSED; the kept-faith referee is re-queued to the front."""
    _run()
    decliner = Registration.objects.get(user__email="declined.amb@seed.test")
    kept_faith = Registration.objects.get(user__email="declined.ref@seed.test")
    assert decliner.status == Registration.Status.PAUSED
    assert kept_faith.status == Registration.Status.VERIFIED
    assert kept_faith.priority == 1
    declined = Match.objects.get(status=Match.Status.DECLINED)
    assert declined.referee_accepted_at is not None


def test_expired_pair_both_paused() -> None:
    """Both non-responders on the expired match are PAUSED."""
    _run()
    for email in ("expired.amb@seed.test", "expired.ref@seed.test"):
        reg = Registration.objects.get(user__email=email)
        assert reg.status == Registration.Status.PAUSED, email


def test_cancelled_pair_mirrors_no_show_report() -> None:
    """The reporting referee is re-queued to the front; the reported is SUSPENDED."""
    _run()
    reported = Registration.objects.get(user__email="cancelled.amb@seed.test")
    reporter = Registration.objects.get(user__email="cancelled.ref@seed.test")
    assert reported.status == Registration.Status.SUSPENDED
    assert reporter.status == Registration.Status.VERIFIED
    assert reporter.priority == 1


def test_paused_user_seeded_with_no_match_history() -> None:
    """A standalone PAUSED registration is seeded (rejoin/cancel account actions)."""
    _run()
    reg = Registration.objects.get(user__email="paused@seed.test")
    assert reg.status == Registration.Status.PAUSED
    assert not Match.objects.filter(ambassador_registration=reg).exists()
    assert not Match.objects.filter(referee_registration=reg).exists()


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
# Notifications
# ---------------------------------------------------------------------------


def _seeded_notifications() -> list[Notification]:
    """Return all notifications created by the command, newest first."""
    return list(
        Notification.objects.filter(content__startswith=SEED_NOTIFICATION_MARKER)
    )


def test_expected_notification_count() -> None:
    """seed_test_data creates the sentinel set of notifications."""
    _run()
    assert len(_seeded_notifications()) == 11


def test_seeded_notifications_cover_every_design() -> None:
    """The sentinel set exercises all four seed designs."""
    _run()
    designs = {n.design for n in _seeded_notifications()}
    assert designs == {"INFO", "MUTED", "NOTICE", "URGENT"}


def test_seeded_notifications_include_a_disabled_kill_switch_example() -> None:
    """One sentinel is disabled and so is excluded from the shown set."""
    _run()
    seeded = _seeded_notifications()
    disabled = [n for n in seeded if not n.enabled]
    assert len(disabled) == 1
    # The disabled one is within its window (active) yet never shown, because
    # enabled().active() drops it.
    now = timezone.now()
    shown = set(Notification.objects.enabled().active(now))
    assert disabled[0] not in shown
    assert disabled[0].is_active is True


def test_seeded_notifications_cover_every_audience() -> None:
    """The sentinel set exercises all four audiences."""
    _run()
    audiences = {n.audience for n in _seeded_notifications()}
    assert audiences == {
        Notification.Audience.EVERYONE,
        Notification.Audience.ANONYMOUS,
        Notification.Audience.AUTHENTICATED,
        Notification.Audience.CUSTOM,
    }


def test_seeded_notifications_include_permanent_and_dismissible() -> None:
    """Both a permanent and a dismissible notification are seeded."""
    _run()
    flags = {n.is_dismissible for n in _seeded_notifications()}
    assert flags == {True, False}


def test_seeded_notifications_include_scheduled_and_expired_windows() -> None:
    """A future-window and a past-window notification are seeded (inactive now)."""
    _run()
    now = timezone.now()
    active_ids = {n.pk for n in Notification.objects.active(now)}
    seeded = _seeded_notifications()
    # Exactly two seeded notifications are outside the current window.
    inactive = [n for n in seeded if n.pk not in active_ids]
    assert len(inactive) == 2


def test_seeded_custom_group_keys_are_configured() -> None:
    """CUSTOM notifications name keys that exist in CUSTOM_NOTIFICATION_GROUPS."""
    _run()
    custom = [
        n for n in _seeded_notifications() if n.audience == Notification.Audience.CUSTOM
    ]
    assert {n.custom_group_key for n in custom} == {"ambassadors", "referees"}


def test_seeded_notification_html_is_sanitised() -> None:
    """A seeded <script> is stripped while a link survives (save() ran nh3)."""
    _run()
    combined = " ".join(n.content_sanitised for n in _seeded_notifications())
    assert "<script>" not in combined
    assert "alert(1)" not in combined
    # The how-it-works link body survives sanitisation.
    assert 'href="/how-it-works/"' in combined


def test_running_twice_yields_same_notification_count() -> None:
    """Re-running the command does not duplicate seeded notifications."""
    _run()
    first = len(_seeded_notifications())
    _run()
    assert len(_seeded_notifications()) == first


# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------


def test_output_contains_seed_domain() -> None:
    """Command stdout mentions the seed email domain."""
    output = _run()
    assert SEED_EMAIL_DOMAIN in output


def test_output_mentions_notifications() -> None:
    """Command stdout reports the seeded notifications."""
    output = _run()
    assert "Notifications seeded." in output
