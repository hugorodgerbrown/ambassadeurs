# Tests for core.i18n — the translation-catalogue untranslated/fuzzy counter.
#
# Exercises the minimal PO parser against the shapes Django's makemessages
# emits: header, translated/untranslated/fuzzy entries, multi-line and plural
# msgstrs, and obsolete #~ entries. See ADR 0015.

from __future__ import annotations

from pathlib import Path

from core.i18n import (
    CatalogueStats,
    count_untranslated,
    count_untranslated_file,
)

# A catalogue covering every shape the counter must classify.
SAMPLE_PO = """
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

msgid "%d item"
msgid_plural "%d items"
msgstr[0] ""
msgstr[1] ""

msgid "%d cat"
msgid_plural "%d cats"
msgstr[0] "%d chat"
msgstr[1] "%d chats"

msgid "multi"
msgstr ""
"first "
"second"

#~ msgid "Removed"
#~ msgstr ""
"""


def test_counts_untranslated_and_fuzzy() -> None:
    """The counter classifies each entry shape correctly.

    Expected: "World" and the empty plural are untranslated (2); "Goodbye" is
    fuzzy (1). The header, translated singular/plural, multi-line translation,
    and obsolete entry are all excluded.
    """
    stats = count_untranslated(SAMPLE_PO)
    assert stats.untranslated == 2
    assert stats.fuzzy == 1
    assert stats.total == 3


def test_fuzzy_and_empty_entry_counts_once_as_fuzzy() -> None:
    """An entry that is both fuzzy and empty is counted only under fuzzy."""
    po = 'msgid ""\nmsgstr ""\n\n#, fuzzy\nmsgid "x"\nmsgstr ""\n'
    stats = count_untranslated(po)
    assert stats.untranslated == 0
    assert stats.fuzzy == 1
    assert stats.total == 1


def test_fully_translated_catalogue_is_zero() -> None:
    """A catalogue with only a header and translated entries needs no attention."""
    po = 'msgid ""\nmsgstr ""\n\nmsgid "a"\nmsgstr "b"\n'
    assert count_untranslated(po) == CatalogueStats(untranslated=0, fuzzy=0)


def test_empty_string_is_zero() -> None:
    """An empty catalogue text yields all-zero stats."""
    assert count_untranslated("").total == 0


def test_count_untranslated_file_reads_disk(tmp_path: Path) -> None:
    """count_untranslated_file parses a .po file from disk."""
    po_file = tmp_path / "django.po"
    po_file.write_text(SAMPLE_PO, encoding="utf-8")
    assert count_untranslated_file(po_file).total == 3


def test_count_untranslated_file_missing_is_zero(tmp_path: Path) -> None:
    """A missing .po file is treated as an empty (all-zero) catalogue."""
    stats = count_untranslated_file(tmp_path / "absent.po")
    assert stats == CatalogueStats(untranslated=0, fuzzy=0)
