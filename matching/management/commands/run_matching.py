# Management command: drain the waiting pool by proposing eligible matches.
#
# Intended to run at the open date to clear the queue that builds up while
# matching is gated (see matching.services.propose_match / VERB-83), and on a
# schedule thereafter (Render cron) to complement the rolling synchronous
# propose for late entrants. Delegates all business logic to
# matching.services.run_matching.
#
# Follows the management-command design rules: runs with no arguments, is
# read-only unless --commit is passed (a bare run is a dry-run that reports
# what it would propose), respects --verbosity, and exits non-zero when a
# proposal in the batch fails so cron/CI can detect a partial failure.

from typing import Any

from django.core.management.base import BaseCommand, CommandError

from matching.services import run_matching


class Command(BaseCommand):
    """Propose eligible matches for the whole waiting pool until none remain.

    Walks the eligible pool in the engine's priority-then-FIFO order and
    proposes a match for each pairable ambassador. Without --commit the command
    is read-only and only reports how many matches it would propose; with
    --commit it creates them. Exits non-zero if any proposal fails.
    """

    help = "Propose eligible matches for the waiting pool (dry-run unless --commit)."

    def add_arguments(self, parser: Any) -> None:
        """Register the --commit flag (read-only by default)."""
        parser.add_argument(
            "--commit",
            action="store_true",
            help="Create the matches. Without this flag the command is a dry-run.",
        )

    def handle(self, *args: object, **options: object) -> None:
        """Run the drain and write a summary; exit non-zero on partial failure."""
        commit = bool(options["commit"])
        # ``verbosity`` is always present (Django adds it); default to 1 only if
        # it is None. Do not use ``or 1`` — that would turn a genuine 0 into 1.
        verbosity_option = options.get("verbosity")
        verbosity = 1 if verbosity_option is None else int(str(verbosity_option))

        proposed, failed = run_matching(commit=commit)

        if commit:
            summary = f"Proposed {proposed} match(es); {failed} failure(s)."
        else:
            summary = (
                f"Dry-run: would propose {proposed} match(es). "
                "Re-run with --commit to apply."
            )

        if verbosity >= 1:
            self.stdout.write(summary)

        # Exit non-zero on a partially failed batch so cron/CI can detect it.
        if failed > 0:
            raise CommandError(f"{failed} proposal(s) failed during the run.")
