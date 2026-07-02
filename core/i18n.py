# Translation-catalogue inspection helpers.
#
# Utilities for measuring how much of a gettext ``.po`` catalogue is still
# untranslated. Used by the ``update_messages`` management command and, through
# it, by the code-review audit and the update-messages Routine to decide when a
# catalogue rebuild is worth a dedicated pass (see ADR 0016).
#
# The parsing here is deliberately minimal: it understands the subset of the PO
# format that Django's ``makemessages`` emits (single- and multi-line ``msgid`` /
# ``msgstr``, plural forms, and ``#, fuzzy`` flags) and skips obsolete ``#~``
# entries and the catalogue header. It is not a general PO parser — ``polib``
# would be the tool for that; this avoids the dependency for a simple count.

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class CatalogueStats:
    """Counts of entries in a ``.po`` catalogue that need a translator's attention.

    ``untranslated`` and ``fuzzy`` are disjoint: a fuzzy entry is counted only
    under ``fuzzy`` even when its ``msgstr`` is also empty, so ``total`` never
    double-counts.

    Attributes:
        untranslated: Entries whose every ``msgstr`` is empty and which are not
            flagged fuzzy.
        fuzzy: Entries carrying a ``#, fuzzy`` flag.
    """

    untranslated: int
    fuzzy: int

    @property
    def total(self) -> int:
        """Total entries needing attention (untranslated plus fuzzy)."""
        return self.untranslated + self.fuzzy


def _iter_blocks(po_text: str) -> Iterator[list[str]]:
    """Yield each PO entry as a list of its lines, split on blank lines.

    Args:
        po_text: The full text of a ``.po`` catalogue.

    Yields:
        The non-empty lines of each entry, in file order.
    """
    block: list[str] = []
    for line in po_text.splitlines():
        if line.strip() == "":
            if block:
                yield block
                block = []
        else:
            block.append(line)
    if block:
        yield block


def _quoted(line: str) -> str:
    """Return the content between the first and last double-quote on ``line``.

    Handles both keyword lines (``msgstr "x"``) and bare continuation lines
    (``"x"``). Returns the empty string when there is no quoted span.

    Args:
        line: A single stripped PO line.

    Returns:
        The raw (still-escaped) content inside the outermost quotes.
    """
    first = line.find('"')
    last = line.rfind('"')
    if first == -1 or last <= first:
        return ""
    return line[first + 1 : last]


def _msgid(block: list[str]) -> str:
    """Return the concatenated ``msgid`` text of an entry (excluding plurals).

    Args:
        block: The lines of a single PO entry.

    Returns:
        The joined ``msgid`` string; empty for the catalogue header.
    """
    parts: list[str] = []
    in_msgid = False
    for line in block:
        stripped = line.strip()
        if stripped.startswith("msgid_plural"):
            in_msgid = False
        elif stripped.startswith("msgid"):
            in_msgid = True
            parts.append(_quoted(stripped))
        elif stripped.startswith(("msgstr", "msgctxt")):
            in_msgid = False
        elif in_msgid and stripped.startswith('"'):
            parts.append(_quoted(stripped))
    return "".join(parts)


def _msgstr_is_empty(block: list[str]) -> bool:
    """Return whether every ``msgstr`` (all plural forms) of an entry is empty.

    Args:
        block: The lines of a single PO entry.

    Returns:
        True when the entry has no translated text in any ``msgstr`` slot.
    """
    values: list[str] = []
    in_msgstr = False
    for line in block:
        stripped = line.strip()
        if stripped.startswith("msgstr"):
            in_msgstr = True
            values.append(_quoted(stripped))
        elif in_msgstr and stripped.startswith('"'):
            values[-1] += _quoted(stripped)
        elif stripped.startswith(("msgid", "msgctxt", "#")):
            in_msgstr = False
    return all(value == "" for value in values)


def _is_fuzzy(block: list[str]) -> bool:
    """Return whether an entry carries a ``#, fuzzy`` flag.

    Args:
        block: The lines of a single PO entry.

    Returns:
        True when a flag comment marks the entry fuzzy.
    """
    return any(line.strip().startswith("#,") and "fuzzy" in line for line in block)


def count_untranslated(po_text: str) -> CatalogueStats:
    """Count the untranslated and fuzzy entries in a ``.po`` catalogue.

    The header entry (empty ``msgid``) and obsolete ``#~`` entries are ignored.

    Args:
        po_text: The full text of a ``.po`` catalogue.

    Returns:
        A :class:`CatalogueStats` with the untranslated and fuzzy counts.
    """
    untranslated = 0
    fuzzy = 0
    for block in _iter_blocks(po_text):
        if any(line.lstrip().startswith("#~") for line in block):
            continue  # obsolete entry
        if _msgid(block) == "":
            continue  # catalogue header
        if _is_fuzzy(block):
            fuzzy += 1
        elif _msgstr_is_empty(block):
            untranslated += 1
    return CatalogueStats(untranslated=untranslated, fuzzy=fuzzy)


def count_untranslated_file(path: Path) -> CatalogueStats:
    """Count untranslated/fuzzy entries in a ``.po`` file on disk.

    A missing file is treated as an empty catalogue (all-zero stats), so callers
    can point at a locale that has not been extracted yet without special-casing.

    Args:
        path: Path to the ``.po`` catalogue.

    Returns:
        A :class:`CatalogueStats` for the file's contents.
    """
    if not path.exists():
        return CatalogueStats(untranslated=0, fuzzy=0)
    return count_untranslated(path.read_text(encoding="utf-8"))
