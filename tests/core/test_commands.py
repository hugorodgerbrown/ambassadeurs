# Tests for the core management commands.
#
# Covers update_messages in both modes (ADR 0016). Since SKI-159, --check no
# longer reads the committed .po files verbatim: it extracts into a throwaway
# shadow copy of locale/ (the same makemessages invocation the rebuild uses)
# and counts that copy, so it catches copy wrapped for translation but never
# yet extracted — the exact condition that let a 49-entry backlog go
# unreported for seven weeks. Rebuild mode (delegates to
# makemessages/compilemessages against the real locale/ tree, reports the
# backlog) is unchanged.

from __future__ import annotations

import shutil
from io import StringIO
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from django.conf import settings
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import override_settings

# A French catalogue with 2 untranslated + 1 fuzzy entry (total 3).
FR_PO = """
msgid ""
msgstr ""
"Content-Type: text/plain; charset=UTF-8\\n"

msgid "Hello"
msgstr "Bonjour"

msgid "World"
msgstr ""

#, fuzzy
msgid "Goodbye"
msgstr "Au revoir"

msgid "Cat"
msgstr ""
"""

# A fully-translated French catalogue (0 untranslated, 0 fuzzy) — the
# baseline for the SKI-159 regression test below: on a naive read of the
# committed catalogue this reports "0 untranslated, 0 fuzzy", which is
# exactly what hid the real backlog for seven weeks.
FR_PO_TRANSLATED = """
msgid ""
msgstr ""
"Content-Type: text/plain; charset=UTF-8\\n"

msgid "Hello"
msgstr "Bonjour"
"""

# The English (source) catalogue: msgid IS the display text, so msgstr entries
# are empty by design. These must NOT be counted as untranslated.
EN_PO = 'msgid ""\nmsgstr ""\n\nmsgid "Hello"\nmsgstr ""\n\nmsgid "World"\nmsgstr ""\n'


def _write_catalogues(locale_dir: Path, fr_po: str = FR_PO) -> None:
    """Write en (source, empty msgstrs — excluded) and fr catalogues."""
    for code, text in (("en", EN_PO), ("fr", fr_po)):
        po_dir = locale_dir / code / "LC_MESSAGES"
        po_dir.mkdir(parents=True)
        (po_dir / "django.po").write_text(text, encoding="utf-8")


def _run(locale_dir: Path, **kwargs: object) -> str:
    """Run update_messages with LOCALE_PATHS pointed at ``locale_dir``."""
    stdout = StringIO()
    with override_settings(LOCALE_PATHS=[locale_dir]):
        call_command("update_messages", stdout=stdout, **kwargs)
    return stdout.getvalue()


def _extraction_double(added_msgid: str | None) -> MagicMock:
    """Return a ``call_command`` double standing in for makemessages/compilemessages.

    Faithfully mimics the one thing ``--check`` depends on real
    ``makemessages`` doing: writing into whatever ``settings.LOCALE_PATHS[0]``
    currently is at call time — the shadow copy while under ``--check``, the
    real tree during a rebuild. ``compilemessages`` calls are recorded but are
    otherwise no-ops, matching how the real command is never asked to compile
    under ``--check``.

    Args:
        added_msgid: The msgid to append as a new, untranslated ``fr`` entry
            on every ``makemessages`` call. ``None`` makes the double a pure
            no-op recorder, for tests that only care the calls happened with
            the right arguments.

    Returns:
        A ``MagicMock`` recording every call, wired to the write side effect.
    """

    def _side_effect(command: str, **kwargs: object) -> None:
        if command != "makemessages" or added_msgid is None:
            return
        po_path = Path(settings.LOCALE_PATHS[0]) / "fr" / "LC_MESSAGES" / "django.po"
        po_path.parent.mkdir(parents=True, exist_ok=True)
        with po_path.open("a", encoding="utf-8") as handle:
            handle.write(f'\nmsgid "{added_msgid}"\nmsgstr ""\n')

    return MagicMock(side_effect=_side_effect)


# ---------------------------------------------------------------------------
# --check gate
# ---------------------------------------------------------------------------


def test_check_counts_a_string_extracted_but_never_committed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """SKI-159 regression: --check must see copy wrapped but never extracted.

    A committed fr catalogue that is 100% translated reports 0 on a naive
    read of the .po files — this is exactly the "0 untranslated, 0 fuzzy"
    that hid a real 49-entry backlog for seven weeks. --check must instead
    run the extraction and count what it actually finds.
    """
    monkeypatch.setattr(
        "core.management.commands.update_messages.call_command",
        _extraction_double("A string that was wrapped but never extracted"),
    )
    _write_catalogues(tmp_path, fr_po=FR_PO_TRANSLATED)
    with override_settings(I18N_UPDATE_MESSAGES_THRESHOLD=10):
        out = _run(tmp_path, check=True)
    assert "fr: 1 untranslated, 0 fuzzy (1 total)" in out
    assert "1 untranslated/fuzzy entries (below threshold 10)" in out


def test_check_leaves_the_real_locale_tree_byte_identical(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """--check extracts into a shadow copy; the real locale/ tree never changes."""
    monkeypatch.setattr(
        "core.management.commands.update_messages.call_command",
        _extraction_double("Only ever written to the shadow copy"),
    )
    _write_catalogues(tmp_path)
    before = {path: path.read_bytes() for path in tmp_path.rglob("*.po")}

    with override_settings(I18N_UPDATE_MESSAGES_THRESHOLD=100):
        _run(tmp_path, check=True)

    after = {path: path.read_bytes() for path in tmp_path.rglob("*.po")}
    assert after == before


def test_check_below_threshold_reports_and_succeeds(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """--check with total below the threshold reports counts and does not raise."""
    monkeypatch.setattr(
        "core.management.commands.update_messages.call_command",
        _extraction_double(None),
    )
    _write_catalogues(tmp_path)
    with override_settings(I18N_UPDATE_MESSAGES_THRESHOLD=10):
        out = _run(tmp_path, check=True)
    assert "fr: 2 untranslated, 1 fuzzy (3 total)" in out
    assert "below threshold 10" in out


def test_source_language_excluded_from_count(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The en source catalogue's empty msgstrs are not counted or reported."""
    monkeypatch.setattr(
        "core.management.commands.update_messages.call_command",
        _extraction_double(None),
    )
    _write_catalogues(tmp_path)
    with override_settings(I18N_UPDATE_MESSAGES_THRESHOLD=100):
        out = _run(tmp_path, check=True)
    assert "en:" not in out
    assert "3 untranslated/fuzzy entries (below threshold 100)" in out


def test_check_at_threshold_raises(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """--check raises (non-zero exit) once the total reaches the threshold."""
    monkeypatch.setattr(
        "core.management.commands.update_messages.call_command",
        _extraction_double(None),
    )
    _write_catalogues(tmp_path)
    with override_settings(I18N_UPDATE_MESSAGES_THRESHOLD=3):
        with pytest.raises(CommandError, match="rebuild is due"):
            _run(tmp_path, check=True)


def test_check_threshold_override(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """--threshold overrides the setting for the gate."""
    monkeypatch.setattr(
        "core.management.commands.update_messages.call_command",
        _extraction_double(None),
    )
    _write_catalogues(tmp_path)
    with override_settings(I18N_UPDATE_MESSAGES_THRESHOLD=100):
        with pytest.raises(CommandError, match="threshold 2"):
            _run(tmp_path, check=True, threshold=2)


def test_check_missing_locale_dir_reports_all_zero(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A fresh checkout with no locale/ yet reports all-zero rather than raising."""
    monkeypatch.setattr(
        "core.management.commands.update_messages.call_command",
        _extraction_double(None),
    )
    missing = tmp_path / "does-not-exist"
    with override_settings(I18N_UPDATE_MESSAGES_THRESHOLD=10):
        out = _run(missing, check=True)
    assert "fr: 0 untranslated, 0 fuzzy (0 total)" in out
    assert "0 untranslated/fuzzy entries (below threshold 10)" in out
    assert not missing.exists()  # never created — --check writes no real path


def test_check_recovers_from_leftover_hidden_dir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A hidden dir left by a run killed mid-shield is restored, not lost.

    Simulates the crash window documented on _shielded_cwd_locale_dir: a
    prior --check was SIGKILLed after renaming the real locale/ away but
    before its `finally` could rename it back, leaving
    .locale-hidden-for-update-messages-check on disk and no locale/. The next
    --check must recover it *before* deciding whether there is a real
    locale/ to copy into the shadow — otherwise the pre-existing backlog
    (FR_PO's 3 entries) is silently lost in favour of an empty shadow, which
    is the exact bug this ticket exists to fix. The extraction double is a
    no-op here so the only way to see "3 total" reported is if the shadow was
    actually built from the recovered, pre-crash content.
    """
    monkeypatch.setattr(
        "core.management.commands.update_messages.call_command",
        _extraction_double(None),
    )
    monkeypatch.chdir(tmp_path)
    locale_dir = tmp_path / "locale"
    _write_catalogues(locale_dir, fr_po=FR_PO)

    # Simulate the kill: rename locale/ to the hidden sibling
    # _shielded_cwd_locale_dir uses, leaving no locale/ behind.
    hidden_dir = tmp_path / ".locale-hidden-for-update-messages-check"
    locale_dir.rename(hidden_dir)
    assert not locale_dir.exists()

    with override_settings(I18N_UPDATE_MESSAGES_THRESHOLD=10):
        out = _run(locale_dir, check=True)

    # Recovered rather than left hidden or treated as a fresh checkout.
    assert locale_dir.is_dir()
    assert not hidden_dir.exists()
    assert (locale_dir / "fr" / "LC_MESSAGES" / "django.po").read_text(
        encoding="utf-8"
    ) == FR_PO
    # The pre-existing backlog was found — not the silent "0 untranslated,
    # 0 fuzzy" an empty (un-recovered) shadow would otherwise produce.
    assert "fr: 2 untranslated, 1 fuzzy (3 total)" in out


# A minimal but well-formed catalogue — msgmerge needs a declared charset in
# the header to merge cleanly, which the deliberately-terse FR_PO/EN_PO
# fixtures above omit (they only ever go through the hand-rolled parser, not
# real gettext tooling).
_SEED_PO = (
    'msgid ""\n'
    'msgstr ""\n'
    '"Content-Type: text/plain; charset=UTF-8\\n"\n'
    '"Content-Transfer-Encoding: 8bit\\n"\n'
    "\n"
    'msgid "Hello"\n'
    'msgstr "{translation}"\n'
)


@pytest.mark.skipif(shutil.which("xgettext") is None, reason="gettext not installed")
def test_check_runs_real_makemessages_end_to_end(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Hermetic end-to-end: real makemessages finds a wrapped-but-unextracted string.

    Runs against a tiny temp source tree, never this repo — monkeypatch.chdir
    keeps makemessages' directory scan confined to tmp_path, so the test stays
    fast and does not depend on the state of the real locale/ tree. The
    catalogues are pre-seeded (mirroring a real repo, which always has prior
    extractions) so makemessages merges into them rather than generating a
    catalogue from scratch.
    """
    monkeypatch.chdir(tmp_path)
    (tmp_path / "sample.py").write_text(
        "from django.utils.translation import gettext\n"
        'gettext("A string that has never been extracted")\n',
        encoding="utf-8",
    )
    locale_dir = tmp_path / "locale"
    fr_seed = _SEED_PO.format(translation="Bonjour")
    for code, seed in (("en", _SEED_PO.format(translation="")), ("fr", fr_seed)):
        po_dir = locale_dir / code / "LC_MESSAGES"
        po_dir.mkdir(parents=True)
        (po_dir / "django.po").write_text(seed, encoding="utf-8")

    with override_settings(I18N_UPDATE_MESSAGES_THRESHOLD=10):
        out = _run(locale_dir, check=True)
    assert "fr: 1 untranslated, 0 fuzzy (1 total)" in out
    # the real (pre-seeded) catalogue is untouched by the shadow extraction.
    assert (locale_dir / "fr" / "LC_MESSAGES" / "django.po").read_text(
        encoding="utf-8"
    ) == fr_seed


# ---------------------------------------------------------------------------
# rebuild mode
# ---------------------------------------------------------------------------


def test_rebuild_invokes_makemessages_and_compilemessages(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Default mode extracts then compiles, then reports the backlog."""
    fake = MagicMock()
    monkeypatch.setattr("core.management.commands.update_messages.call_command", fake)
    _write_catalogues(tmp_path)
    out = _run(tmp_path)

    invoked = [c.args[0] for c in fake.call_args_list]
    assert invoked == ["makemessages", "compilemessages"]
    # makemessages is run with --no-location for both locales.
    make_kwargs = fake.call_args_list[0].kwargs
    assert make_kwargs["no_location"] is True
    assert make_kwargs["locale"] == ["en", "fr"]
    assert "3 entries still need translation" in out
