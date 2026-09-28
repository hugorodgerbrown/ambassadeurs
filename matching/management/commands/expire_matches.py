# Management command: expire contact-window-lapsed matches and re-queue.
#
# Intended to run on a schedule (hourly via Render cron) to sweep PROPOSED and
# PENDING matches whose expires_at has passed. Delegates all business logic to
# matching.services.expire_lapsed_matches.
#
# Follows the management-command design rules: runs with no arguments, is
# read-only unless --commit is passed (a bare run is a dry-run that reports what
# it would expire), and respects --verbosity. The guard matters more here than
# on the other sweeps: expiring a match is not the only effect. For each side
# that had already accepted, ``expire_match`` re-queues that party to the front
# and calls ``propose_match`` at once, so a single run on a backlog can pair
# people and email them in the same second. The dry-run names that side per
# match so an operator can see it before it happens.

from typing import Any

from django.core.management.base import BaseCommand
from django.utils import timezone

from matching.models import Match
from matching.services import expire_lapsed_matches


class Command(BaseCommand):
    """Expire all PROPOSED/PENDING matches whose contact window has lapsed.

    Transitions each lapsed match to EXPIRED. The kept-faith side (already
    accepted) is re-queued to the front of the pool and proposed a new match
    immediately; the non-responding side is PAUSED — out of the pool, but able
    to self-rejoin from their account page (VERB-74 / ADR 0013). Without
    --commit the command is read-only and lists what it would do.
    """

    help = "Expire contact-window-lapsed matches and re-queue (dry-run unless --commit)."

    def add_arguments(self, parser: Any) -> None:
        """Register the --commit flag (read-only by default)."""
        parser.add_argument(
            "--commit",
            action="store_true",
            help="Apply the sweep. Without this flag the command is a dry-run.",
        )

    def handle(self, *args: object, **options: object) -> None:
        """Run (or preview) the expiry sweep and write a summary to stdout.

        Reads "now" here (inversion of control, VERB-100) and passes it as the
        sweep's cutoff, keeping ``expire_lapsed_matches`` a pure function of
        its arguments.
        """
        commit = bool(options["commit"])
        verbosity_option = options.get("verbosity")
        verbosity = 1 if verbosity_option is None else int(str(verbosity_option))
        cutoff = timezone.now()

        if commit:
            count = expire_lapsed_matches(cutoff=cutoff)
            if verbosity >= 1:
                self.stdout.write(f"Expired {count} matches.")
            return

        lapsed = Match.objects.lapsed(cutoff=cutoff).select_related(
            "ambassador_registration", "referee_registration"
        )
        count = 0
        for match in lapsed:
            count += 1
            if verbosity >= 1:
                self.stdout.write(self._describe(match))
        if verbosity >= 1:
            self.stdout.write(
                f"Dry-run: would expire {count} match(es). "
                "Re-run with --commit to apply."
            )

    @staticmethod
    def _describe(match: Match) -> str:
        """Return one dry-run line for ``match``: pk, status, and each side's fate.

        A side that accepted is re-queued to the front and proposed a new
        match at once; a side that did not respond is paused.
        """
        assert match.ambassador_registration is not None
        assert match.referee_registration is not None
        sides = (
            ("ambassador", match.ambassador_registration, match.ambassador_accepted_at),
            ("referee", match.referee_registration, match.referee_accepted_at),
        )
        outcomes = ", ".join(
            f"{role} reg={reg.pk} "
            + ("re-queued to front and re-proposed" if accepted else "paused")
            for role, reg, accepted in sides
        )
        return (
            f"Match {match.pk} ({match.status}, expired {match.expires_at:%Y-%m-%d %H:%M}): "
            f"{outcomes}"
        )
