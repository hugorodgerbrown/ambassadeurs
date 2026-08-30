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
#                                      Writes to the real locale/ tree.
#
#   manage.py update_messages --check  Gate: runs the *same* makemessages
#                                      extraction as the rebuild, but against a
#                                      throwaway copy of locale/ (see
#                                      _shadow_locale_dir), counts
#                                      untranslated/fuzzy entries in that copy,
#                                      and exits non-zero when the total reaches
#                                      the threshold. It is read-only with
#                                      respect to the working tree — the real
#                                      locale/ is never written — but it is not
#                                      a passive read of the committed .po
#                                      files: it reports the backlog a rebuild
#                                      would actually produce, including copy
#                                      wrapped for translation but never yet
#                                      extracted (SKI-159). Used by the
#                                      code-review audit and the
#                                      update-messages Routine to decide
#                                      whether a rebuild is worth a dedicated
#                                      pass.
#
# The threshold defaults to settings.I18N_UPDATE_MESSAGES_THRESHOLD and can be
# overridden per-invocation with --threshold.
#
# Gotcha (SKI-159): redirecting settings.LOCALE_PATHS is not, on its own,
# enough to keep --check off the real locale/ tree. makemessages also
# unconditionally auto-discovers a cwd-relative ./locale (a "run inside an
# app dir" convenience) ahead of anything in LOCALE_PATHS, and this repo's own
# locale/ sits exactly there — so --check additionally shields it for the
# duration of the extraction; see _shielded_cwd_locale_dir.

from __future__ import annotations

import logging
import shutil
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
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


def _collect_stats(locale_dir: Path) -> dict[str, CatalogueStats]:
    """Return per-locale untranslated/fuzzy stats for catalogues under ``locale_dir``.

    Skips the source language. A locale with no ``.po`` yet reports all-zero.

    Args:
        locale_dir: The directory to read ``<code>/LC_MESSAGES/django.po``
            from — the real ``locale/`` tree for a rebuild's final report, or
            a shadow copy (see :func:`_shadow_locale_dir`) for ``--check``.

    Returns:
        A mapping of (non-source) language code to its :class:`CatalogueStats`.
    """
    return {
        code: count_untranslated_file(_catalogue_path(locale_dir, code))
        for code in _translation_codes()
    }


def _extract(locales: list[str]) -> None:
    """Run ``makemessages --no-location`` for ``locales`` against the active locale dir.

    The single call site for the extraction invocation, shared by
    :meth:`Command._run_rebuild` (against the real ``locale/`` tree) and
    :meth:`Command._run_check` (against a shadow copy, entered beforehand via
    :func:`_shadow_locale_dir`). Keeping one call site is deliberate: if the
    two modes ever ran ``makemessages`` with different arguments, ``--check``
    would stop predicting what a rebuild actually produces, which is the
    signal this command exists to provide (SKI-159).

    Args:
        locales: Language codes to extract (e.g. ``["en", "fr"]``).
    """
    call_command("makemessages", locale=locales, no_location=True)


@contextmanager
def _locale_paths(paths: list[Path]) -> Iterator[None]:
    """Temporarily replace ``settings.LOCALE_PATHS``, restoring it in a ``finally``.

    Deliberately not ``django.test.override_settings``: that would pull
    ``django.test`` onto a production code path, and the ``setting_changed``
    signal it fires (which resets translation caches) buys nothing here —
    ``makemessages`` shells out to ``xgettext`` rather than reading
    ``LOCALE_PATHS`` through Django's translation machinery. This is a
    single-threaded management command, so mutating the module-level setting
    for the duration of one call is safe.

    Args:
        paths: The ``LOCALE_PATHS`` value to use for the duration of the context.

    Yields:
        None.
    """
    original = settings.LOCALE_PATHS
    settings.LOCALE_PATHS = paths
    try:
        yield
    finally:
        settings.LOCALE_PATHS = original


@contextmanager
def _shielded_cwd_locale_dir(real_locale_dir: Path) -> Iterator[None]:
    """Hide a cwd-relative ``locale/`` that collides with ``real_locale_dir``.

    Django's ``makemessages`` has a hard-coded convenience for "running
    inside an app dir": whenever the *current working directory* has a
    subdirectory literally named ``locale``, it is unconditionally added to
    the extraction's locale-path list — **before** ``--ignore`` patterns are
    even consulted, and regardless of ``settings.LOCALE_PATHS``
    (``django/core/management/commands/makemessages.py``, the
    ``# Allow to run makemessages inside an app dir`` block). This repo's own
    ``locale/`` sits directly in the directory ``manage.py`` runs from, so it
    is always rediscovered this way. Because every source file's directory is
    an ancestor of that real ``locale/`` but not of a shadow copy living
    outside the repo, ``makemessages`` then writes the extraction straight
    back into the real catalogues — verified empirically; this is exactly
    what ``--check`` must never do (SKI-159). Redirecting ``LOCALE_PATHS``
    alone does not stop it. Temporarily renaming the colliding directory out
    of the way (and back again in a ``finally``, even on error) is the only
    reliable way to stop the rediscovery.

    A no-op unless ``./locale`` (relative to the current working directory)
    resolves to the same directory as ``real_locale_dir`` — which keeps this
    safe to call unconditionally: tests that point ``LOCALE_PATHS`` at an
    unrelated temporary directory never touch the real ``locale/`` at all.

    Args:
        real_locale_dir: The directory ``--check`` must leave untouched.

    Yields:
        None.
    """
    cwd_locale_dir = Path("locale")
    collides = (
        cwd_locale_dir.is_dir()
        and cwd_locale_dir.resolve() == real_locale_dir.resolve()
    )
    if not collides:
        yield
        return
    hidden_dir = cwd_locale_dir.with_name(".locale-hidden-for-update-messages-check")
    cwd_locale_dir.rename(hidden_dir)
    try:
        yield
    finally:
        hidden_dir.rename(cwd_locale_dir)


@contextmanager
def _shadow_locale_dir() -> Iterator[Path]:
    """Yield a throwaway copy of ``locale/`` with ``LOCALE_PATHS`` redirected to it.

    Copies the real ``locale/`` tree into a ``tempfile.TemporaryDirectory()``
    *outside* the repo — ``makemessages`` scans the current working
    directory for source strings but writes its output to
    ``settings.LOCALE_PATHS[0]``, so redirecting that setting to a copy left
    inside the repo would still leave the copy sitting in the scanned tree.
    Also shields a colliding cwd-relative ``locale/`` for the duration (see
    :func:`_shielded_cwd_locale_dir`) — without that, ``makemessages``
    rediscovers the real directory regardless of ``LOCALE_PATHS``. A repo
    with no ``locale/`` yet (a fresh checkout) gets an empty directory
    instead of raising, so ``--check`` reports all-zero rather than erroring.

    Yields:
        The path to the shadow locale directory, with ``LOCALE_PATHS``
        already pointed at it.
    """
    real_locale_dir = Path(settings.LOCALE_PATHS[0])
    with tempfile.TemporaryDirectory(prefix="update_messages_shadow_") as tmp_dir:
        shadow_dir = Path(tmp_dir) / "locale"
        if real_locale_dir.is_dir():
            shutil.copytree(real_locale_dir, shadow_dir)
        else:
            shadow_dir.mkdir(parents=True)
        with (
            _locale_paths([shadow_dir]),
            _shielded_cwd_locale_dir(real_locale_dir),
        ):
            yield shadow_dir


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
                "Extract into a throwaway copy of locale/ and count "
                "untranslated/fuzzy entries there, exiting non-zero at/above "
                "the threshold. Read-only with respect to the working tree — "
                "the real locale/ is never written."
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
        """Extract into a shadow copy of locale/, count entries, and gate on the total.

        Runs the same extraction :func:`_run_rebuild` would run, but against a
        throwaway copy of ``locale/`` (see :func:`_shadow_locale_dir`) so the
        count reflects what a rebuild would actually produce — including copy
        wrapped for translation but never yet extracted — while leaving the
        real ``locale/`` untouched.

        Args:
            threshold: The untranslated-count trigger.

        Raises:
            CommandError: When the total reaches the threshold (non-zero exit),
                signalling the review machinery to open an update-messages task.
        """
        locales = _language_codes()
        with _shadow_locale_dir() as shadow_dir:
            _extract(locales)
            total = self._report(_collect_stats(shadow_dir))
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
        _extract(locales)
        call_command("compilemessages", locale=locales)
        total = self._report(_collect_stats(Path(settings.LOCALE_PATHS[0])))
        self.stdout.write(
            f"{total} entries still need translation — fill in the French "
            "msgstr values, then run `manage.py compilemessages`."
        )
