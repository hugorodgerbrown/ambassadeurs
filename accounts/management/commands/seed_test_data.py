# Management command: populate a fresh local database with deterministic test data.
#
# Creates a reloadable set of loginable users, registrations, and matches covering
# every Registration.Status and Match.Status so developers can explore all UI states
# without setting up fixtures by hand.
#
# Safety: refuses to run unless settings.DEBUG is True (or --force is passed).
# All writes happen inside a single transaction; a partial run cannot leave the
# database in a half-seeded state.
#
# Do NOT call matching.services mutation functions here — they have side effects
# (sending email, pausing registrations, re-queueing). Rows are built via the
# test factories (tests/*/factories.py) so the seed data and the test suite
# share one definition of a valid object graph; timestamps and statuses are set
# explicitly. The seeded historical pairs mirror what the real services would
# have left behind (ADR 0013): a decliner or non-responder is PAUSED, and a
# kept-faith party is re-queued to the front (priority=1).
#
# factory_boy is a dev-only dependency, so the factory imports live inside the
# helper functions rather than at module level — the module stays importable
# in production, where the dev dependency group is not installed.
#
# Shelf life: the seeded PROPOSED/PENDING matches lapse CONTACT_WINDOW_HOURS
# after seeding, and nothing runs the expire_matches sweep locally — after that
# the account page and the match page disagree about them. Re-run this command
# to refresh.
#
# allauth has been removed (VERB-46). Email-verified state is now derived from
# Registration.status (UNVERIFIED vs any other status). EmailAddress rows are
# no longer created here.

from __future__ import annotations

import logging
from datetime import datetime, timedelta

from django.conf import settings
from django.contrib.auth.models import User
from django.core.management.base import BaseCommand, CommandError, CommandParser
from django.db import transaction
from django.utils import timezone

from core.emails import normalise_email
from core.models import Notification
from matching.models import Match, Registration, Resort
from matching.pricing_config import fee_chf_for

logger = logging.getLogger(__name__)

# All seeded users share this domain so the wipe step can identify them reliably.
SEED_EMAIL_DOMAIN = "seed.test"

# Every seeded notification's content starts with this marker so the wipe step
# can identify and remove exactly the ones this command created (a developer's
# own hand-authored notifications are left untouched). The marker is visible in
# the rendered banner, which doubles as a "this is local seed data" cue.
SEED_NOTIFICATION_MARKER = "[seed]"

# Fixed consent text used for all seeded registrations (avoids translation lookup).
_ACCEPTED_TERMS = [
    "I confirm my eligibility for the 4 Vallées Ambassadors Programme.",
    "I have read and agree to the Terms of Use.",
]


def _seed_email(local_part: str) -> str:
    """Return a normalised seed email for ``local_part``."""
    return normalise_email(f"{local_part}@{SEED_EMAIL_DOMAIN}")


def _make_user(
    email: str,
    first_name: str,
    last_name: str,
    *,
    is_superuser: bool = False,
    is_staff: bool = False,
) -> User:
    """Create a User with an unusable password via UserFactory.

    Args:
        email: Already-normalised email address.
        first_name: User's first name.
        last_name: User's last name.
        is_superuser: Whether to grant superuser privileges.
        is_staff: Whether to grant staff (admin) access.

    Returns:
        The newly created User instance.
    """
    # Deferred import: factory_boy is a dev-only dependency (see module header).
    from tests.accounts.factories import UserFactory

    user = UserFactory.create(
        username=email,
        email=email,
        first_name=first_name,
        last_name=last_name,
        is_superuser=is_superuser,
        is_staff=is_staff,
    )
    user.set_unusable_password()
    user.save(update_fields=["password"])
    return user


def _make_registration(
    user: User,
    *,
    role: str,
    prior_pass: str,
    status: str,
    phone: str,
    preferred_language: str,
    preferred_location: str,
    priority: int = 0,
) -> Registration:
    """Create a Registration row via RegistrationFactory (no service side-effects).

    Args:
        user: The owning User.
        role: Registration.Role value.
        prior_pass: Registration.PriorPass value.
        status: Registration.Status value.
        phone: Phone number string.
        preferred_language: ISO 639 language code.
        preferred_location: Resort choice value (may be empty string).
        priority: Queue priority; 1 marks a kept-faith party re-queued to the
            front after a decline / no-show report (ADR 0013).

    Returns:
        The newly created Registration instance.
    """
    # Deferred import: factory_boy is a dev-only dependency (see module header).
    from tests.matching.factories import RegistrationFactory

    return RegistrationFactory.create(
        user=user,
        role=role,
        prior_pass=prior_pass,
        status=status,
        phone=phone,
        preferred_language=preferred_language,
        preferred_location=preferred_location,
        priority=priority,
        fee_chf=fee_chf_for(timezone.localdate()),
        accepted_terms=list(_ACCEPTED_TERMS),
        terms_accepted_at=timezone.now(),
    )


def _make_notification(
    content: str,
    *,
    audience: str,
    priority: int = Notification.Priority.NORMAL,
    enabled: bool = True,
    is_dismissible: bool = True,
    starts_at: datetime | None = None,
    ends_at: datetime | None = None,
    custom_group_key: str = "",
) -> Notification:
    """Create a seeded Notification via NotificationFactory (save() runs nh3).

    The content is prefixed with ``SEED_NOTIFICATION_MARKER`` by the caller so
    the wipe step can find it again. ``save()`` populates ``content_sanitised``
    from ``content``, so a seeded notification exercises the same sanitisation
    path as one authored in Django admin.

    Args:
        content: Raw HTML/plain text (already marker-prefixed by the caller).
        audience: Notification.Audience value.
        priority: Notification.Priority value (drives the strip colour).
        enabled: Whether the kill switch is on; a disabled notification never
            shows regardless of its window.
        is_dismissible: Whether the banner shows a dismiss control.
        starts_at: Optional tz-aware window start (None means "always" on that
            side).
        ends_at: Optional tz-aware window end (None means "always" on that
            side).
        custom_group_key: Key into settings.CUSTOM_NOTIFICATION_GROUPS; only
            meaningful when audience is CUSTOM.

    Returns:
        The newly created Notification instance.
    """
    # Deferred import: factory_boy is a dev-only dependency (see module header).
    from tests.core.factories import NotificationFactory

    return NotificationFactory.create(
        content=content,
        audience=audience,
        priority=priority,
        enabled=enabled,
        is_dismissible=is_dismissible,
        starts_at=starts_at,
        ends_at=ends_at,
        custom_group_key=custom_group_key,
    )


def _future_expires_at() -> datetime:
    """Return a tz-aware expires_at suitable for an active (non-lapsed) match."""
    return timezone.now() + timedelta(hours=settings.CONTACT_WINDOW_HOURS)


def _past_expires_at() -> datetime:
    """Return a tz-aware expires_at in the past (for terminal/historical matches)."""
    return timezone.now() - timedelta(hours=settings.CONTACT_WINDOW_HOURS)


class Command(BaseCommand):
    """Populate the local database with a deterministic set of loginable test users.

    Creates users, registrations, and matches covering every Registration.Status
    (including PAUSED) and Match.Status so all UI states can be explored locally.
    All seeded rows share the ``seed.test`` email domain for easy identification
    and cleanup.

    Refuses to run unless settings.DEBUG is True (or --force is passed). Running
    the command a second time wipes the previous seed data and rebuilds it from
    scratch (idempotent / deterministic). Pass --keep to skip the wipe.

    The seeded active matches lapse CONTACT_WINDOW_HOURS after seeding (see the
    module header); re-run the command to refresh them.
    """

    help = "Seed the local database with deterministic test data (DEBUG only)."

    def add_arguments(self, parser: CommandParser) -> None:
        """Register command-line arguments."""
        parser.add_argument(
            "--force",
            action="store_true",
            help="Run even when DEBUG is False (dangerous in production).",
        )
        parser.add_argument(
            "--keep",
            action="store_true",
            help="Skip the initial wipe; add seed data on top of existing rows.",
        )

    def handle(self, *args: object, **options: object) -> None:
        """Validate safety guard then delegate to _seed inside a transaction."""
        force: bool = bool(options["force"])
        keep: bool = bool(options["keep"])

        if not settings.DEBUG and not force:
            raise CommandError(
                "Refusing to seed data: DEBUG is False. "
                "Pass --force to override (dangerous in production)."
            )

        with transaction.atomic():
            if not keep:
                self._wipe_seed_data()
            rows = self._create_seed_data()
            notifications = self._create_notifications()

        self._print_summary(rows)
        self._print_notification_summary(notifications)

    # ------------------------------------------------------------------
    # Wipe
    # ------------------------------------------------------------------

    def _wipe_seed_data(self) -> None:
        """Delete all rows previously created by this command.

        Deletion order:
        1. Match rows that reference a seed Registration (FK is SET_NULL so we
           must do this before deleting Users, otherwise the FK becomes NULL and
           the Match row is orphaned with no way to identify it as seeded).
        2. User rows whose email ends with ``@seed.test`` (cascades to
           Registration via OneToOneField).
        """
        seed_registrations = Registration.objects.filter(
            user__email__endswith=f"@{SEED_EMAIL_DOMAIN}"
        )
        Match.objects.filter(ambassador_registration__in=seed_registrations).delete()
        Match.objects.filter(referee_registration__in=seed_registrations).delete()
        User.objects.filter(email__endswith=f"@{SEED_EMAIL_DOMAIN}").delete()
        Notification.objects.filter(
            content__startswith=SEED_NOTIFICATION_MARKER
        ).delete()

    # ------------------------------------------------------------------
    # Create
    # ------------------------------------------------------------------

    def _create_seed_data(self) -> list[dict[str, str]]:
        """Create all seed rows and return a list of summary dicts.

        Returns:
            List of dicts with keys ``email``, ``role``, ``reg_status``,
            ``match_status`` for printing.
        """
        # Deferred import: factory_boy is a dev-only dependency (module header).
        from tests.matching.factories import MatchFactory

        rows: list[dict[str, str]] = []
        now = timezone.now()

        # --- Superuser (no Registration) ----------------------------------
        admin_user = _make_user(
            _seed_email("admin"),
            first_name="Admin",
            last_name="Seed",
            is_superuser=True,
            is_staff=True,
        )
        rows.append(
            {
                "email": admin_user.email,
                "role": "superuser",
                "reg_status": "(no registration)",
                "match_status": "—",
            }
        )

        # --- UNVERIFIED registration (email not confirmed) ---------------
        unverified_user = _make_user(
            _seed_email("unverified"),
            first_name="Ursula",
            last_name="Noel",
        )
        _make_registration(
            unverified_user,
            role=Registration.Role.AMBASSADOR,
            prior_pass=Registration.PriorPass.SEASONAL,
            status=Registration.Status.UNVERIFIED,
            phone="+41790000001",
            preferred_language="en",
            preferred_location=Resort.VERBIER,
        )
        rows.append(
            {
                "email": unverified_user.email,
                "role": "AMBASSADOR",
                "reg_status": "UNVERIFIED",
                "match_status": "—",
            }
        )

        # --- VERIFIED ambassador in queue (no match) ----------------------
        amb_queue_user = _make_user(
            _seed_email("amb.queue"),
            first_name="Antoine",
            last_name="Bovard",
        )
        _make_registration(
            amb_queue_user,
            role=Registration.Role.AMBASSADOR,
            prior_pass=Registration.PriorPass.SEASONAL,
            status=Registration.Status.VERIFIED,
            phone="+41790000002",
            preferred_language="fr",
            preferred_location=Resort.VERBIER,
        )
        rows.append(
            {
                "email": amb_queue_user.email,
                "role": "AMBASSADOR",
                "reg_status": "VERIFIED",
                "match_status": "—",
            }
        )

        # --- VERIFIED referee in queue (no match) -------------------------
        ref_queue_user = _make_user(
            _seed_email("ref.queue"),
            first_name="Rita",
            last_name="Maret",
        )
        _make_registration(
            ref_queue_user,
            role=Registration.Role.REFEREE,
            prior_pass=Registration.PriorPass.NONE,
            status=Registration.Status.VERIFIED,
            phone="+41790000003",
            preferred_language="en",
            preferred_location=Resort.THYON,
        )
        rows.append(
            {
                "email": ref_queue_user.email,
                "role": "REFEREE",
                "reg_status": "VERIFIED",
                "match_status": "—",
            }
        )

        # --- PROPOSED pair (neither side has responded) -------------------
        proposed_amb_user = _make_user(
            _seed_email("proposed.amb"),
            first_name="Pierre",
            last_name="Favre",
        )
        proposed_amb_reg = _make_registration(
            proposed_amb_user,
            role=Registration.Role.AMBASSADOR,
            prior_pass=Registration.PriorPass.ANNUAL,
            status=Registration.Status.VERIFIED,
            phone="+41790000004",
            preferred_language="fr",
            preferred_location=Resort.NENDAZ,
        )

        proposed_ref_user = _make_user(
            _seed_email("proposed.ref"),
            first_name="Pascale",
            last_name="Fellay",
        )
        proposed_ref_reg = _make_registration(
            proposed_ref_user,
            role=Registration.Role.REFEREE,
            prior_pass=Registration.PriorPass.NONE,
            status=Registration.Status.VERIFIED,
            phone="+41790000005",
            preferred_language="fr",
            preferred_location=Resort.NENDAZ,
        )

        MatchFactory.create(
            ambassador_registration=proposed_amb_reg,
            referee_registration=proposed_ref_reg,
            expires_at=_future_expires_at(),
        )
        rows.append(
            {
                "email": proposed_amb_user.email,
                "role": "AMBASSADOR",
                "reg_status": "VERIFIED",
                "match_status": "PROPOSED",
            }
        )
        rows.append(
            {
                "email": proposed_ref_user.email,
                "role": "REFEREE",
                "reg_status": "VERIFIED",
                "match_status": "PROPOSED",
            }
        )

        # --- PENDING pair (ambassador accepted, awaiting referee) ---------
        pending_amb_user = _make_user(
            _seed_email("pending.amb"),
            first_name="Marc",
            last_name="Luisier",
        )
        pending_amb_reg = _make_registration(
            pending_amb_user,
            role=Registration.Role.AMBASSADOR,
            prior_pass=Registration.PriorPass.MONT4,
            status=Registration.Status.VERIFIED,
            phone="+41790000006",
            preferred_language="en",
            preferred_location=Resort.VEYSONNAZ,
        )

        pending_ref_user = _make_user(
            _seed_email("pending.ref"),
            first_name="Marie",
            last_name="Nanchen",
        )
        pending_ref_reg = _make_registration(
            pending_ref_user,
            role=Registration.Role.REFEREE,
            prior_pass=Registration.PriorPass.NONE,
            status=Registration.Status.VERIFIED,
            phone="+41790000007",
            preferred_language="en",
            preferred_location=Resort.VEYSONNAZ,
        )

        MatchFactory.create(
            pending=True,
            ambassador_registration=pending_amb_reg,
            referee_registration=pending_ref_reg,
            expires_at=_future_expires_at(),
            ambassador_accepted_at=now,
        )
        rows.append(
            {
                "email": pending_amb_user.email,
                "role": "AMBASSADOR",
                "reg_status": "VERIFIED",
                "match_status": "PENDING",
            }
        )
        rows.append(
            {
                "email": pending_ref_user.email,
                "role": "REFEREE",
                "reg_status": "VERIFIED",
                "match_status": "PENDING",
            }
        )

        # --- ACCEPTED pair (both sides accepted, contact PII revealed) ----
        accepted_amb_user = _make_user(
            _seed_email("accepted.amb"),
            first_name="Bernard",
            last_name="Germanier",
        )
        accepted_amb_reg = _make_registration(
            accepted_amb_user,
            role=Registration.Role.AMBASSADOR,
            prior_pass=Registration.PriorPass.SEASONAL,
            status=Registration.Status.VERIFIED,
            phone="+41790000008",
            preferred_language="fr",
            preferred_location=Resort.LA_TZOUMAZ,
        )

        accepted_ref_user = _make_user(
            _seed_email("accepted.ref"),
            first_name="Brigitte",
            last_name="Gaillard",
        )
        accepted_ref_reg = _make_registration(
            accepted_ref_user,
            role=Registration.Role.REFEREE,
            prior_pass=Registration.PriorPass.NONE,
            status=Registration.Status.VERIFIED,
            phone="+41790000009",
            preferred_language="fr",
            preferred_location=Resort.LA_TZOUMAZ,
        )

        MatchFactory.create(
            accepted=True,
            ambassador_registration=accepted_amb_reg,
            referee_registration=accepted_ref_reg,
            expires_at=_future_expires_at(),
            ambassador_accepted_at=now - timedelta(hours=2),
            referee_accepted_at=now - timedelta(hours=1),
        )
        rows.append(
            {
                "email": accepted_amb_user.email,
                "role": "AMBASSADOR",
                "reg_status": "VERIFIED",
                "match_status": "ACCEPTED",
            }
        )
        rows.append(
            {
                "email": accepted_ref_user.email,
                "role": "REFEREE",
                "reg_status": "VERIFIED",
                "match_status": "ACCEPTED",
            }
        )

        # --- SUSPENDED registration ----------------------------------------
        suspended_user = _make_user(
            _seed_email("suspended"),
            first_name="Samuel",
            last_name="Carron",
        )
        _make_registration(
            suspended_user,
            role=Registration.Role.AMBASSADOR,
            prior_pass=Registration.PriorPass.SEASONAL,
            status=Registration.Status.SUSPENDED,
            phone="+41790000010",
            preferred_language="en",
            preferred_location=Resort.BRUSON,
        )
        rows.append(
            {
                "email": suspended_user.email,
                "role": "AMBASSADOR",
                "reg_status": "SUSPENDED",
                "match_status": "—",
            }
        )

        # --- WITHDRAWN registration ----------------------------------------
        withdrawn_user = _make_user(
            _seed_email("withdrawn"),
            first_name="Wendy",
            last_name="Theytaz",
        )
        _make_registration(
            withdrawn_user,
            role=Registration.Role.REFEREE,
            prior_pass=Registration.PriorPass.NONE,
            status=Registration.Status.WITHDRAWN,
            phone="+41790000011",
            preferred_language="fr",
            preferred_location=Resort.VERBIER,
        )
        rows.append(
            {
                "email": withdrawn_user.email,
                "role": "REFEREE",
                "reg_status": "WITHDRAWN",
                "match_status": "—",
            }
        )

        # --- PAUSED registration (out of pool, self-recoverable) -----------
        # Shows the "Rejoin the queue" and "Cancel & refund" account actions.
        paused_user = _make_user(
            _seed_email("paused"),
            first_name="Patricia",
            last_name="Vaudan",
        )
        _make_registration(
            paused_user,
            role=Registration.Role.REFEREE,
            prior_pass=Registration.PriorPass.NONE,
            status=Registration.Status.PAUSED,
            phone="+41790000018",
            preferred_language="en",
            preferred_location=Resort.VERBIER,
        )
        rows.append(
            {
                "email": paused_user.email,
                "role": "REFEREE",
                "reg_status": "PAUSED",
                "match_status": "—",
            }
        )

        # --- Historical DECLINED match ------------------------------------
        # Mirrors the real decline flow (ADR 0013): the referee had accepted
        # (match was PENDING), then the ambassador declined. The decliner is
        # PAUSED (out of pool, self-recoverable); the kept-faith referee is
        # re-queued to the front (priority=1).
        declined_amb_user = _make_user(
            _seed_email("declined.amb"),
            first_name="Denis",
            last_name="Crettaz",
        )
        declined_amb_reg = _make_registration(
            declined_amb_user,
            role=Registration.Role.AMBASSADOR,
            prior_pass=Registration.PriorPass.ANNUAL,
            status=Registration.Status.PAUSED,
            phone="+41790000012",
            preferred_language="fr",
            preferred_location=Resort.VERBIER,
        )

        declined_ref_user = _make_user(
            _seed_email("declined.ref"),
            first_name="Diane",
            last_name="Copt",
        )
        declined_ref_reg = _make_registration(
            declined_ref_user,
            role=Registration.Role.REFEREE,
            prior_pass=Registration.PriorPass.NONE,
            status=Registration.Status.VERIFIED,
            phone="+41790000013",
            preferred_language="fr",
            preferred_location=Resort.VERBIER,
            priority=1,
        )

        MatchFactory.create(
            declined=True,
            ambassador_registration=declined_amb_reg,
            referee_registration=declined_ref_reg,
            expires_at=_past_expires_at(),
            referee_accepted_at=now - timedelta(hours=30),
            declined_at=now - timedelta(hours=24),
        )
        rows.append(
            {
                "email": declined_amb_user.email,
                "role": "AMBASSADOR",
                "reg_status": "PAUSED",
                "match_status": "DECLINED (historical)",
            }
        )
        rows.append(
            {
                "email": declined_ref_user.email,
                "role": "REFEREE",
                "reg_status": "VERIFIED",
                "match_status": "DECLINED (historical)",
            }
        )

        # --- Historical EXPIRED match -------------------------------------
        # Mirrors the expiry sweep (ADR 0013): neither side responded within
        # the contact window, so both non-responders are PAUSED.
        expired_amb_user = _make_user(
            _seed_email("expired.amb"),
            first_name="Etienne",
            last_name="Dayer",
        )
        expired_amb_reg = _make_registration(
            expired_amb_user,
            role=Registration.Role.AMBASSADOR,
            prior_pass=Registration.PriorPass.SEASONAL,
            status=Registration.Status.PAUSED,
            phone="+41790000014",
            preferred_language="en",
            preferred_location=Resort.THYON,
        )

        expired_ref_user = _make_user(
            _seed_email("expired.ref"),
            first_name="Eva",
            last_name="Dorsaz",
        )
        expired_ref_reg = _make_registration(
            expired_ref_user,
            role=Registration.Role.REFEREE,
            prior_pass=Registration.PriorPass.NONE,
            status=Registration.Status.PAUSED,
            phone="+41790000015",
            preferred_language="en",
            preferred_location=Resort.THYON,
        )

        MatchFactory.create(
            ambassador_registration=expired_amb_reg,
            referee_registration=expired_ref_reg,
            status=Match.Status.EXPIRED,
            expires_at=_past_expires_at(),
        )
        rows.append(
            {
                "email": expired_amb_user.email,
                "role": "AMBASSADOR",
                "reg_status": "PAUSED",
                "match_status": "EXPIRED (historical)",
            }
        )
        rows.append(
            {
                "email": expired_ref_user.email,
                "role": "REFEREE",
                "reg_status": "PAUSED",
                "match_status": "EXPIRED (historical)",
            }
        )

        # --- Historical CANCELLED match -----------------------------------
        cancelled_amb_user = _make_user(
            _seed_email("cancelled.amb"),
            first_name="Christophe",
            last_name="Luyet",
        )
        cancelled_amb_reg = _make_registration(
            cancelled_amb_user,
            role=Registration.Role.AMBASSADOR,
            prior_pass=Registration.PriorPass.SEASONAL,
            status=Registration.Status.SUSPENDED,
            phone="+41790000016",
            preferred_language="fr",
            preferred_location=Resort.BRUSON,
        )

        cancelled_ref_user = _make_user(
            _seed_email("cancelled.ref"),
            first_name="Claire",
            last_name="Michelet",
        )
        # The reporting referee is re-queued to the front (priority=1); the
        # reported ambassador is SUSPENDED (ADR 0007).
        cancelled_ref_reg = _make_registration(
            cancelled_ref_user,
            role=Registration.Role.REFEREE,
            prior_pass=Registration.PriorPass.NONE,
            status=Registration.Status.VERIFIED,
            phone="+41790000017",
            preferred_language="fr",
            preferred_location=Resort.BRUSON,
            priority=1,
        )

        MatchFactory.create(
            cancelled=True,
            ambassador_registration=cancelled_amb_reg,
            referee_registration=cancelled_ref_reg,
            expires_at=_future_expires_at(),
            ambassador_accepted_at=now - timedelta(hours=48),
            referee_accepted_at=now - timedelta(hours=47),
            no_show_reported_at=now - timedelta(hours=4),
        )
        rows.append(
            {
                "email": cancelled_amb_user.email,
                "role": "AMBASSADOR",
                "reg_status": "SUSPENDED",
                "match_status": "CANCELLED (historical)",
            }
        )
        rows.append(
            {
                "email": cancelled_ref_user.email,
                "role": "REFEREE",
                "reg_status": "VERIFIED",
                "match_status": "CANCELLED (historical)",
            }
        )

        return rows

    # ------------------------------------------------------------------
    # Notifications
    # ------------------------------------------------------------------

    def _create_notifications(self) -> list[dict[str, str]]:
        """Create a sentinel set of notifications covering every variant.

        One notification per axis a developer needs to eyeball: audience
        (everyone / anonymous / authenticated / custom groups), every priority
        colour (neutral / low / normal / high), dismissible vs permanent, the
        ``enabled`` kill switch, an HTML/link body, a body carrying a
        ``<script>`` to show the sanitiser strips it, and future/past display
        windows (present in admin but not currently rendered). Every content
        string is prefixed with ``SEED_NOTIFICATION_MARKER`` so the wipe step
        can reclaim them.

        Returns:
            List of dicts with keys ``audience``, ``priority``,
            ``dismissible``, ``state``, ``content`` for printing.
        """
        now = timezone.now()
        m = SEED_NOTIFICATION_MARKER
        prio = Notification.Priority

        # (notification, human "state" label for the summary table).
        specs: list[tuple[Notification, str]] = [
            (
                _make_notification(
                    f"{m} Last day to register — closes tonight.",
                    audience=Notification.Audience.EVERYONE,
                    priority=prio.HIGH,
                ),
                "active",
            ),
            (
                _make_notification(
                    f"{m} Registration is open — welcome to Ski Parrainage.",
                    audience=Notification.Audience.EVERYONE,
                    is_dismissible=False,
                ),
                "active",
            ),
            (
                _make_notification(
                    f"{m} Register before <strong>31 October</strong> for free — "
                    '<a href="/how-it-works/">read more</a>.',
                    audience=Notification.Audience.EVERYONE,
                    priority=prio.LOW,
                ),
                "active",
            ),
            (
                _make_notification(
                    f"{m} Sanitiser check: <em>this stays</em>, "
                    "<script>alert(1)</script> is stripped.",
                    audience=Notification.Audience.EVERYONE,
                    priority=prio.NEUTRAL,
                ),
                "active",
            ),
            (
                _make_notification(
                    f"{m} You are browsing as a guest — sign in to manage your "
                    "registration.",
                    audience=Notification.Audience.ANONYMOUS,
                    priority=prio.LOW,
                ),
                "active",
            ),
            (
                _make_notification(
                    f"{m} Signed in: your account page shows your match status.",
                    audience=Notification.Audience.AUTHENTICATED,
                    priority=prio.LOW,
                ),
                "active",
            ),
            (
                _make_notification(
                    f"{m} Ambassadors — thank you for volunteering this season.",
                    audience=Notification.Audience.CUSTOM,
                    custom_group_key="ambassadors",
                ),
                "active",
            ),
            (
                _make_notification(
                    f"{m} Referees — your ambassador will be in touch once matched.",
                    audience=Notification.Audience.CUSTOM,
                    custom_group_key="referees",
                ),
                "active",
            ),
            (
                _make_notification(
                    f"{m} Scheduled — starts in a week (not yet shown).",
                    audience=Notification.Audience.EVERYONE,
                    starts_at=now + timedelta(days=7),
                ),
                "scheduled (inactive)",
            ),
            (
                _make_notification(
                    f"{m} Last season's notice — window closed (not shown).",
                    audience=Notification.Audience.EVERYONE,
                    ends_at=now - timedelta(days=1),
                ),
                "expired (inactive)",
            ),
            (
                _make_notification(
                    f"{m} Disabled example — hidden by the kill switch.",
                    audience=Notification.Audience.EVERYONE,
                    priority=prio.HIGH,
                    enabled=False,
                ),
                "disabled (kill switch)",
            ),
        ]

        return [
            {
                "audience": n.audience,
                "priority": n.get_priority_display(),
                "dismissible": "yes" if n.is_dismissible else "no",
                "state": state,
                "content": n.content,
            }
            for n, state in specs
        ]

    # ------------------------------------------------------------------
    # Output
    # ------------------------------------------------------------------

    def _print_summary(self, rows: list[dict[str, str]]) -> None:
        """Write a human-readable summary table to stdout.

        Args:
            rows: List of dicts from _create_seed_data with keys
                  ``email``, ``role``, ``reg_status``, ``match_status``.
        """
        self.stdout.write("")
        self.stdout.write(self.style.SUCCESS("Seed data created successfully."))
        self.stdout.write("")

        # Column widths.
        col_email = max(len(r["email"]) for r in rows)
        col_role = max(len(r["role"]) for r in rows)
        col_reg = max(len(r["reg_status"]) for r in rows)
        col_match = max(len(r["match_status"]) for r in rows)

        header = (
            f"{'Email':<{col_email}}  "
            f"{'Role':<{col_role}}  "
            f"{'Reg status':<{col_reg}}  "
            f"{'Match status':<{col_match}}"
        )
        divider = "-" * len(header)

        self.stdout.write(header)
        self.stdout.write(divider)

        for row in rows:
            self.stdout.write(
                f"{row['email']:<{col_email}}  "
                f"{row['role']:<{col_role}}  "
                f"{row['reg_status']:<{col_reg}}  "
                f"{row['match_status']:<{col_match}}"
            )

        self.stdout.write(divider)
        self.stdout.write(f"Total: {len(rows)} entries created.")
        self.stdout.write("")
        self.stdout.write("Login via /account/login/ using any email above.")
        self.stdout.write(
            f"Note: the PROPOSED/PENDING matches lapse in "
            f"{settings.CONTACT_WINDOW_HOURS} hours; re-run this command to "
            f"refresh them."
        )
        self.stdout.write("")

    def _print_notification_summary(self, rows: list[dict[str, str]]) -> None:
        """Write a human-readable summary of the seeded notifications.

        Args:
            rows: List of dicts from _create_notifications with keys
                  ``audience``, ``priority``, ``dismissible``, ``state``,
                  ``content``.
        """
        self.stdout.write(self.style.SUCCESS("Notifications seeded."))
        self.stdout.write("")

        preview_width = 44
        previews = [
            (r["content"][:preview_width] + "…")
            if len(r["content"]) > preview_width
            else r["content"]
            for r in rows
        ]

        col_aud = max(len("Audience"), *(len(r["audience"]) for r in rows))
        col_prio = max(len("Priority"), *(len(r["priority"]) for r in rows))
        col_dis = len("Dismissible")
        col_state = max(len("State"), *(len(r["state"]) for r in rows))
        col_prev = max(len("Content"), *(len(p) for p in previews))

        header = (
            f"{'Audience':<{col_aud}}  "
            f"{'Priority':<{col_prio}}  "
            f"{'Dismissible':<{col_dis}}  "
            f"{'State':<{col_state}}  "
            f"{'Content':<{col_prev}}"
        )
        divider = "-" * len(header)

        self.stdout.write(header)
        self.stdout.write(divider)
        for row, preview in zip(rows, previews, strict=True):
            self.stdout.write(
                f"{row['audience']:<{col_aud}}  "
                f"{row['priority']:<{col_prio}}  "
                f"{row['dismissible']:<{col_dis}}  "
                f"{row['state']:<{col_state}}  "
                f"{preview:<{col_prev}}"
            )
        self.stdout.write(divider)
        self.stdout.write(f"Total: {len(rows)} notifications created.")
        self.stdout.write("")
