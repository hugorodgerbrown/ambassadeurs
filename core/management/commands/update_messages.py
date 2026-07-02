# Management command: the canonical entry point for translation-catalogue work.
#
# Two modes (see ADR 0016 — decoupled catalogue maintenance):
#
#   manage.py update_messages          Rebuild: run makemessages (--no-location)
#                                      for every configured locale, then
#                                      compilemessages, then report how many
#                                      entries are still untranslated/fuzzy so
#                                      the operator knows what French msgstrs to
#                                      fill in. This is the single-purpose task.
#
#   manage.py update_messages --check  Read-only gate: count untranslated/fuzzy
#                                      entries in the committed .po files (does
#                                      NOT run makemessages) and exit non-zero
#                                      when the total reaches the threshold. Used
#                                      by the code-review audit and the
#                                      update-messages Routine to decide whether
#                                      a rebuild is worth a dedicated pass.
#
# The threshold defaults to settings.I18N_UPDATE_MESSAGES_THRESHOLD and can be
# overridden per-invocation with --threshold.

from __future__ import annotations

import logging
from pathlib import Path

from django.conf import settings
from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError, CommandParser

from core.i18n import CatalogueStats, count_untranslated_file

logger = logging.getLogger(__name__)


def _language_codes() -> list[str]:
    """Return every configured language code (e.g. ``["en", "fr"]``)."""
    return [code for code, _name in settings.LANGUAGES]


def _translation_codes() -> list[str]:
    """Return the languages that actually need translating.

    The source language (``settings.LANGUAGE_CODE``, "en" here) is excluded: its
    ``msgid`` *is* the display text, so its catalogue's ``msgstr`` entries are
    empty by design and ``gettext`` falls back to the source. Counting them as
    "untranslated" would be nonsense.
    """
    return [code for code in _language_codes() if code != settings.LANGUAGE_CODE]


def _catalogue_path(locale_dir: Path, code: str) -> Path:
    """Return the ``django.po`` path for one locale under ``locale_dir``."""
    return locale_dir / code / "LC_MESSAGES" / "django.po"


def _collect_stats() -> dict[str, CatalogueStats]:
    """Return per-locale untranslated/fuzzy stats for the translation catalogues.

    Reads the first configured ``LOCALE_PATHS`` directory (the project's own
    ``locale/``), skipping the source language. A locale with no ``.po`` yet
    reports all-zero.

    Returns:
        A mapping of (non-source) language code to its :class:`CatalogueStats`.
    """
    locale_dir = Path(settings.LOCALE_PATHS[0])
    return {
        code: count_untranslated_file(_catalogue_path(locale_dir, code))
        for code in _translation_codes()
    }


class Command(BaseCommand):
    """Rebuild the translation catalogues, or count untranslated entries.

    See the module header and ADR 0016 for the two modes and the decoupled
    catalogue-maintenance policy they implement.
    """

    help = "Rebuild translation catalogues, or count untranslated entries (--check)."

    def add_arguments(self, parser: CommandParser) -> None:
        """Register the ``--check`` and ``--threshold`` options."""
        parser.add_argument(
            "--check",
            action="store_true",
            help=(
                "Read-only: count untranslated/fuzzy entries in the committed "
                ".po files and exit non-zero at/above the threshold. Does not "
                "run makemessages."
            ),
        )
        parser.add_argument(
            "--threshold",
            type=int,
            default=None,
            help=(
                "Override settings.I18N_UPDATE_MESSAGES_THRESHOLD for the --check gate."
            ),
        )

    def handle(self, *args: object, **options: object) -> None:
        """Run the requested mode (default rebuild, or ``--check`` gate)."""
        raw_threshold = options["threshold"]
        threshold: int = settings.I18N_UPDATE_MESSAGES_THRESHOLD
        if isinstance(raw_threshold, int):
            threshold = raw_threshold

        if bool(options["check"]):
            self._run_check(threshold)
        else:
            self._run_rebuild()

    def _report(self, stats: dict[str, CatalogueStats]) -> int:
        """Write per-locale stats to stdout and return the grand total.

        Args:
            stats: Per-locale untranslated/fuzzy counts.

        Returns:
            The total untranslated-plus-fuzzy count across all locales.
        """
        grand_total = 0
        for code, stat in stats.items():
            grand_total += stat.total
            self.stdout.write(
                f"{code}: {stat.untranslated} untranslated, "
                f"{stat.fuzzy} fuzzy ({stat.total} total)"
            )
        return grand_total

    def _run_check(self, threshold: int) -> None:
        """Count untranslated entries and fail when the threshold is reached.

        Args:
            threshold: The untranslated-count trigger.

        Raises:
            CommandError: When the total reaches the threshold (non-zero exit),
                signalling the review machinery to open an update-messages task.
        """
        total = self._report(_collect_stats())
        if total >= threshold:
            raise CommandError(
                f"{total} untranslated/fuzzy entries "
                f"(threshold {threshold}) — a catalogue rebuild is due."
            )
        self.stdout.write(
            f"{total} untranslated/fuzzy entries (below threshold {threshold})."
        )

    def _run_rebuild(self) -> None:
        """Extract and compile the catalogues, then report the backlog.

        Runs ``makemessages --no-location`` for every configured locale (the
        ``--no-location`` flag keeps the ``.po`` files free of churning
        ``#: file:line`` comments) and then ``compilemessages``. The compiled
        output for still-empty entries falls back to the source string; fill in
        the reported French ``msgstr`` values and re-run ``compilemessages``.
        """
        locales = _language_codes()
        self.stdout.write(f"Extracting messages for {', '.join(locales)} …")
        call_command("makemessages", locale=locales, no_location=True)
        call_command("compilemessages", locale=locales)
        total = self._report(_collect_stats())
        self.stdout.write(
            f"{total} entries still need translation — fill in the French "
            "msgstr values, then run `manage.py compilemessages`."
        )
