# Tests for the core management commands.
#
# Covers update_messages in both modes (ADR 0015): the read-only --check gate
# (below threshold, at threshold, and --threshold override) and the rebuild mode
# (delegates to makemessages/compilemessages and reports the backlog).

from __future__ import annotations

from io import StringIO
from pathlib import Path
from unittest.mock import MagicMock

import pytest
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

# The English (source) catalogue: msgid IS the display text, so msgstr entries
# are empty by design. These must NOT be counted as untranslated.
EN_PO = 'msgid ""\nmsgstr ""\n\nmsgid "Hello"\nmsgstr ""\n\nmsgid "World"\nmsgstr ""\n'


def _write_catalogues(locale_dir: Path) -> None:
    """Write en (source, empty msgstrs — excluded) and fr (3 pending) catalogues."""
    for code, text in (("en", EN_PO), ("fr", FR_PO)):
        po_dir = locale_dir / code / "LC_MESSAGES"
        po_dir.mkdir(parents=True)
        (po_dir / "django.po").write_text(text, encoding="utf-8")


def _run(locale_dir: Path, **kwargs: object) -> str:
    """Run update_messages with LOCALE_PATHS pointed at ``locale_dir``."""
    stdout = StringIO()
    with override_settings(LOCALE_PATHS=[locale_dir]):
        call_command("update_messages", stdout=stdout, **kwargs)
    return stdout.getvalue()


# ---------------------------------------------------------------------------
# --check gate
# ---------------------------------------------------------------------------


def test_check_below_threshold_reports_and_succeeds(tmp_path: Path) -> None:
    """--check with total below the threshold reports counts and does not raise."""
    _write_catalogues(tmp_path)
    with override_settings(I18N_UPDATE_MESSAGES_THRESHOLD=10):
        out = _run(tmp_path, check=True)
    assert "fr: 2 untranslated, 1 fuzzy (3 total)" in out
    assert "below threshold 10" in out


def test_source_language_excluded_from_count(tmp_path: Path) -> None:
    """The en source catalogue's empty msgstrs are not counted or reported."""
    _write_catalogues(tmp_path)
    with override_settings(I18N_UPDATE_MESSAGES_THRESHOLD=100):
        out = _run(tmp_path, check=True)
    assert "en:" not in out
    assert "3 untranslated/fuzzy entries (below threshold 100)" in out


def test_check_at_threshold_raises(tmp_path: Path) -> None:
    """--check raises (non-zero exit) once the total reaches the threshold."""
    _write_catalogues(tmp_path)
    with override_settings(I18N_UPDATE_MESSAGES_THRESHOLD=3):
        with pytest.raises(CommandError, match="rebuild is due"):
            _run(tmp_path, check=True)


def test_check_threshold_override(tmp_path: Path) -> None:
    """--threshold overrides the setting for the gate."""
    _write_catalogues(tmp_path)
    with override_settings(I18N_UPDATE_MESSAGES_THRESHOLD=100):
        with pytest.raises(CommandError, match="threshold 2"):
            _run(tmp_path, check=True, threshold=2)


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
