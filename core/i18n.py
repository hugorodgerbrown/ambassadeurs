# Internationalisation helpers.
#
# Two unrelated concerns share this module because both are i18n plumbing with
# no better home:
#
#   1. Translation-catalogue inspection — measuring how much of a gettext ``.po``
#      catalogue is still untranslated. Used by the ``update_messages`` command
#      and, through it, by the code-review audit and the update-messages Routine
#      to decide when a catalogue rebuild is worth a dedicated pass (ADR 0016).
#
#   2. Language alternates — computing the per-language URL variants of a path
#      for ``hreflang`` link tags (SKI-153, ADR 0025).
#
# The PO parsing here is deliberately minimal: it understands the subset of the
# format that Django's ``makemessages`` emits (single- and multi-line ``msgid`` /
# ``msgstr``, plural forms, and ``#, fuzzy`` flags) and skips obsolete ``#~``
# entries and the catalogue header. It is not a general PO parser — ``polib``
# would be the tool for that; this avoids the dependency for a simple count.

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

from django.conf import settings
from django.urls import Resolver404, resolve, translate_url
from django.utils import translation


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


@dataclass(frozen=True)
class LanguageAlternate:
    """One language variant of a page: the language tag and where it lives.

    Both forms of the location are carried because the two consumers need
    different ones: ``hreflang`` link tags require an absolute URL, while the
    language switcher posts a root-relative ``next`` to ``set_language``.

    Attributes:
        hreflang: The value for the ``hreflang`` attribute — a language code
            from ``settings.LANGUAGES``, or the literal ``x-default``.
        path: The root-relative path serving that language variant.
        url: The same location as an absolute URL. Empty until a caller with a
            request fills it in (see the ``language_alternates`` template tag).
    """

    hreflang: str
    path: str
    url: str = ""


def _language_matching(path: str) -> str | None:
    """Return the language code under which ``path`` resolves, or None.

    ``resolve()`` matches ``i18n_patterns`` against the *currently active*
    language, so ``/fr/faq/`` does not resolve while English is active. This
    tries each configured language in turn and reports the one that matches.

    Args:
        path: A root-relative path.

    Returns:
        The matching language code, or None when the path resolves under no
        language (a genuine 404, or a route mounted outside the prefix).
    """
    for code, _ in settings.LANGUAGES:
        with translation.override(code):
            try:
                resolve(path)
            except Resolver404:
                continue
            return code
    return None


def language_alternate_paths(path: str) -> list[LanguageAlternate]:
    """Return the ``hreflang`` alternates for a path, one per configured language.

    Under ``i18n_patterns(..., prefix_default_language=False)`` the default
    language keeps the bare path (``/faq/``) and every other language gains a
    prefix (``/fr/faq/``). This maps a path in *any* language to the full set,
    so a page can advertise its own translations — ``/faq/`` and ``/fr/faq/``
    both produce the same alternate set, which is what makes the tags symmetric.

    A trailing ``x-default`` entry points at the default-language variant. It
    tells a search engine which URL to serve when no language matches the user,
    and is the conventional companion to a full alternates set.

    The path is resolved under whichever language matches it (see
    :func:`_language_matching`) rather than under the active one. In a request
    those coincide, because ``LocaleMiddleware`` activates the language named by
    the prefix — but not depending on that keeps the helper correct when called
    outside the request cycle, and keeps it unit-testable.

    A path that resolves under no language — a 404, or a route mounted outside
    the prefix such as ``/robots.txt`` — yields the same path for every
    alternate. Callers that only emit tags for real pages need no special case.

    Args:
        path: The root-relative path of the current page (``request.path``).

    Returns:
        One :class:`LanguageAlternate` per entry in ``settings.LANGUAGES``,
        followed by the ``x-default`` entry.
    """
    source_language = _language_matching(path)
    if source_language is None:
        return [
            LanguageAlternate(hreflang=code, path=path)
            for code, _ in [*settings.LANGUAGES, ("x-default", "")]
        ]
    with translation.override(source_language):
        alternates = [
            LanguageAlternate(hreflang=code, path=translate_url(path, code))
            for code, _ in settings.LANGUAGES
        ]
        default_path = translate_url(path, settings.LANGUAGE_CODE)
    alternates.append(LanguageAlternate(hreflang="x-default", path=default_path))
    return alternates


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
