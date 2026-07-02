# Management command: refund every still-HELD deposit at season end.
#
# Intended to run on a schedule (e.g. daily via Render cron) once the season is
# over. Refunds every HELD deposit whose registration never reached an ACCEPTED
# match and is not suspended (see matching.services.close_season / VERB-87).
# Delegates all business logic to that service.
#
# Follows the management-command design rules: runs with no arguments, is
# read-only unless --commit is passed (a bare run is a dry-run that reports what
# it would refund), respects --verbosity, and exits non-zero when a refund in
# the batch fails so cron/CI can detect a partial failure.

from typing import Any

from django.core.management.base import BaseCommand, CommandError

from matching.services import close_season


class Command(BaseCommand):
    """Refund every still-HELD deposit with no accepted match (dry-run unless --commit).

    Sweeps the HELD deposits whose registration never reached an ACCEPTED match
    and is not suspended, refunding each via a self-contained Stripe refund.
    Without --commit the command is read-only and only reports how many deposits
    it would refund; with --commit it issues the refunds. Exits non-zero if any
    refund fails.
    """

    help = "Refund still-HELD deposits with no accepted match (dry-run unless --commit)"

    def add_arguments(self, parser: Any) -> None:
        """Register the --commit flag (read-only by default)."""
        parser.add_argument(
            "--commit",
            action="store_true",
            help="Issue the refunds. Without this flag the command is a dry-run.",
        )

    def handle(self, *args: object, **options: object) -> None:
        """Run the sweep and write a summary; exit non-zero on partial failure."""
        commit = bool(options["commit"])
        # ``verbosity`` is always present (Django adds it); default to 1 only if
        # it is None. Do not use ``or 1`` — that would turn a genuine 0 into 1.
        verbosity_option = options.get("verbosity")
        verbosity = 1 if verbosity_option is None else int(str(verbosity_option))

        refunded, failed = close_season(commit=commit)

        if commit:
            summary = f"Refunded {refunded} deposit(s); {failed} failure(s)."
        else:
            summary = (
                f"Dry-run: would refund {refunded} deposit(s). "
                "Re-run with --commit to apply."
            )

        if verbosity >= 1:
            self.stdout.write(summary)

        # Exit non-zero on a partially failed batch so cron/CI can detect it.
        if failed > 0:
            raise CommandError(f"{failed} refund(s) failed during the sweep.")
