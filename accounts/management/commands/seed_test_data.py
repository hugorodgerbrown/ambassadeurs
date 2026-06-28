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
# (sending email, deleting users on decline, re-queueing). Build rows via the
# Django ORM directly and set timestamps explicitly.

from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any

from allauth.account.models import EmailAddress
from django.conf import settings
from django.contrib.auth.models import User
from django.core.management.base import BaseCommand, CommandError, CommandParser
from django.db import transaction
from django.utils import timezone

from core.emails import normalise_email
from matching.models import Match, Registration, Resort

logger = logging.getLogger(__name__)

# All seeded users share this domain so the wipe step can identify them reliably.
SEED_EMAIL_DOMAIN = "seed.test"

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
    """Create a User with an unusable password.

    Args:
        email: Already-normalised email address.
        first_name: User's first name.
        last_name: User's last name.
        is_superuser: Whether to grant superuser privileges.
        is_staff: Whether to grant staff (admin) access.

    Returns:
        The newly created User instance.
    """
    if is_superuser:
        user = User.objects.create_superuser(
            username=email,
            email=email,
            password=None,
            first_name=first_name,
            last_name=last_name,
        )
    else:
        user = User.objects.create_user(
            username=email,
            email=email,
            password=None,
            first_name=first_name,
            last_name=last_name,
            is_staff=is_staff,
        )
    user.set_unusable_password()
    user.save(update_fields=["password"])
    return user


def _make_email_address(user: User, *, verified: bool) -> EmailAddress:
    """Create an allauth EmailAddress for ``user``.

    Args:
        user: The User to attach the email address to.
        verified: Whether the email address is verified.

    Returns:
        The newly created EmailAddress instance.
    """
    return EmailAddress.objects.create(
        user=user,
        email=user.email,
        primary=True,
        verified=verified,
    )


def _make_registration(
    user: User,
    *,
    role: str,
    prior_pass: str,
    status: str,
    phone: str,
    preferred_language: str,
    preferred_location: str,
) -> Registration:
    """Create a Registration row directly via the ORM (no service side-effects).

    Args:
        user: The owning User.
        role: Registration.Role value.
        prior_pass: Registration.PriorPass value.
        status: Registration.Status value.
        phone: Phone number string.
        preferred_language: ISO 639 language code.
        preferred_location: Resort choice value (may be empty string).

    Returns:
        The newly created Registration instance.
    """
    now = timezone.now()
    return Registration.objects.create(
        user=user,
        role=role,
        prior_pass=prior_pass,
        status=status,
        phone=phone,
        preferred_language=preferred_language,
        preferred_location=preferred_location,
        priority=0,
        flake_count=0,
        accepted_terms=list(_ACCEPTED_TERMS),
        terms_accepted_at=now,
    )


def _future_expires_at() -> Any:
    """Return a tz-aware expires_at suitable for an active (non-lapsed) match."""
    return timezone.now() + timedelta(hours=settings.CONTACT_WINDOW_HOURS)


def _past_expires_at() -> Any:
    """Return a tz-aware expires_at in the past (for terminal/historical matches)."""
    return timezone.now() - timedelta(hours=settings.CONTACT_WINDOW_HOURS)


class Command(BaseCommand):
    """Populate the local database with a deterministic set of loginable test users.

    Creates users, registrations, and matches covering every Registration.Status
    and Match.Status so all UI states can be explored locally. All seeded rows
    share the ``seed.test`` email domain for easy identification and cleanup.

    Refuses to run unless settings.DEBUG is True (or --force is passed). Running
    the command a second time wipes the previous seed data and rebuilds it from
    scratch (idempotent / deterministic). Pass --keep to skip the wipe.
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

        self._print_summary(rows)

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
           Registration via OneToOneField, and to allauth EmailAddress).
        """
        seed_registrations = Registration.objects.filter(
            user__email__endswith=f"@{SEED_EMAIL_DOMAIN}"
        )
        Match.objects.filter(ambassador_registration__in=seed_registrations).delete()
        Match.objects.filter(referee_registration__in=seed_registrations).delete()
        User.objects.filter(email__endswith=f"@{SEED_EMAIL_DOMAIN}").delete()

    # ------------------------------------------------------------------
    # Create
    # ------------------------------------------------------------------

    def _create_seed_data(self) -> list[dict[str, str]]:
        """Create all seed rows and return a list of summary dicts.

        Returns:
            List of dicts with keys ``email``, ``role``, ``reg_status``,
            ``match_status`` for printing.
        """
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
        _make_email_address(admin_user, verified=True)
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
        _make_email_address(unverified_user, verified=False)
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
        _make_email_address(amb_queue_user, verified=True)
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
        _make_email_address(ref_queue_user, verified=True)
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
        _make_email_address(proposed_amb_user, verified=True)
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
        _make_email_address(proposed_ref_user, verified=True)
        proposed_ref_reg = _make_registration(
            proposed_ref_user,
            role=Registration.Role.REFEREE,
            prior_pass=Registration.PriorPass.NONE,
            status=Registration.Status.VERIFIED,
            phone="+41790000005",
            preferred_language="fr",
            preferred_location=Resort.NENDAZ,
        )

        Match.objects.create(
            ambassador_registration=proposed_amb_reg,
            referee_registration=proposed_ref_reg,
            status=Match.Status.PROPOSED,
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
        _make_email_address(pending_amb_user, verified=True)
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
        _make_email_address(pending_ref_user, verified=True)
        pending_ref_reg = _make_registration(
            pending_ref_user,
            role=Registration.Role.REFEREE,
            prior_pass=Registration.PriorPass.NONE,
            status=Registration.Status.VERIFIED,
            phone="+41790000007",
            preferred_language="en",
            preferred_location=Resort.VEYSONNAZ,
        )

        Match.objects.create(
            ambassador_registration=pending_amb_reg,
            referee_registration=pending_ref_reg,
            status=Match.Status.PENDING,
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
        _make_email_address(accepted_amb_user, verified=True)
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
        _make_email_address(accepted_ref_user, verified=True)
        accepted_ref_reg = _make_registration(
            accepted_ref_user,
            role=Registration.Role.REFEREE,
            prior_pass=Registration.PriorPass.NONE,
            status=Registration.Status.VERIFIED,
            phone="+41790000009",
            preferred_language="fr",
            preferred_location=Resort.LA_TZOUMAZ,
        )

        Match.objects.create(
            ambassador_registration=accepted_amb_reg,
            referee_registration=accepted_ref_reg,
            status=Match.Status.ACCEPTED,
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
        _make_email_address(suspended_user, verified=True)
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
        _make_email_address(withdrawn_user, verified=True)
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

        # --- Historical DECLINED match ------------------------------------
        declined_amb_user = _make_user(
            _seed_email("declined.amb"),
            first_name="Denis",
            last_name="Crettaz",
        )
        _make_email_address(declined_amb_user, verified=True)
        declined_amb_reg = _make_registration(
            declined_amb_user,
            role=Registration.Role.AMBASSADOR,
            prior_pass=Registration.PriorPass.ANNUAL,
            status=Registration.Status.VERIFIED,
            phone="+41790000012",
            preferred_language="fr",
            preferred_location=Resort.VERBIER,
        )

        declined_ref_user = _make_user(
            _seed_email("declined.ref"),
            first_name="Diane",
            last_name="Copt",
        )
        _make_email_address(declined_ref_user, verified=True)
        declined_ref_reg = _make_registration(
            declined_ref_user,
            role=Registration.Role.REFEREE,
            prior_pass=Registration.PriorPass.NONE,
            status=Registration.Status.VERIFIED,
            phone="+41790000013",
            preferred_language="fr",
            preferred_location=Resort.VERBIER,
        )

        Match.objects.create(
            ambassador_registration=declined_amb_reg,
            referee_registration=declined_ref_reg,
            status=Match.Status.DECLINED,
            expires_at=_past_expires_at(),
            declined_by=Match.Side.AMBASSADOR,
            declined_at=now - timedelta(hours=24),
        )
        rows.append(
            {
                "email": declined_amb_user.email,
                "role": "AMBASSADOR",
                "reg_status": "VERIFIED",
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
        expired_amb_user = _make_user(
            _seed_email("expired.amb"),
            first_name="Etienne",
            last_name="Dayer",
        )
        _make_email_address(expired_amb_user, verified=True)
        expired_amb_reg = _make_registration(
            expired_amb_user,
            role=Registration.Role.AMBASSADOR,
            prior_pass=Registration.PriorPass.SEASONAL,
            status=Registration.Status.VERIFIED,
            phone="+41790000014",
            preferred_language="en",
            preferred_location=Resort.THYON,
        )

        expired_ref_user = _make_user(
            _seed_email("expired.ref"),
            first_name="Eva",
            last_name="Dorsaz",
        )
        _make_email_address(expired_ref_user, verified=True)
        expired_ref_reg = _make_registration(
            expired_ref_user,
            role=Registration.Role.REFEREE,
            prior_pass=Registration.PriorPass.NONE,
            status=Registration.Status.VERIFIED,
            phone="+41790000015",
            preferred_language="en",
            preferred_location=Resort.THYON,
        )

        Match.objects.create(
            ambassador_registration=expired_amb_reg,
            referee_registration=expired_ref_reg,
            status=Match.Status.EXPIRED,
            expires_at=_past_expires_at(),
        )
        rows.append(
            {
                "email": expired_amb_user.email,
                "role": "AMBASSADOR",
                "reg_status": "VERIFIED",
                "match_status": "EXPIRED (historical)",
            }
        )
        rows.append(
            {
                "email": expired_ref_user.email,
                "role": "REFEREE",
                "reg_status": "VERIFIED",
                "match_status": "EXPIRED (historical)",
            }
        )

        # --- Historical CANCELLED match -----------------------------------
        cancelled_amb_user = _make_user(
            _seed_email("cancelled.amb"),
            first_name="Christophe",
            last_name="Luyet",
        )
        _make_email_address(cancelled_amb_user, verified=True)
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
        _make_email_address(cancelled_ref_user, verified=True)
        cancelled_ref_reg = _make_registration(
            cancelled_ref_user,
            role=Registration.Role.REFEREE,
            prior_pass=Registration.PriorPass.NONE,
            status=Registration.Status.VERIFIED,
            phone="+41790000017",
            preferred_language="fr",
            preferred_location=Resort.BRUSON,
        )

        Match.objects.create(
            ambassador_registration=cancelled_amb_reg,
            referee_registration=cancelled_ref_reg,
            status=Match.Status.CANCELLED,
            expires_at=_future_expires_at(),
            ambassador_accepted_at=now - timedelta(hours=48),
            referee_accepted_at=now - timedelta(hours=47),
            no_show_reported_by=Match.Side.REFEREE,
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
        self.stdout.write("Login via /accounts/login/ using any email above.")
        self.stdout.write("")
