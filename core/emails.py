# Email normalisation utilities.
#
# A single ``normalise_email`` function is the canonical entry point for
# cleaning an email address before storage or lookup.  All entry points in the
# codebase (forms, services, views, hashing) route through here so that the
# stored value and every derived value (e.g. the blind-index hash) are always
# produced from the same normalised form (CLAUDE.md invariant 5).


def normalise_email(email: str) -> str:
    """Return a fully-normalised email address.

    The normalisation steps, in order:

    1. Remove any non-printable / control characters (U+0000–U+001F, DEL,
       and Unicode non-printable code points).  U+0020 SPACE is printable so
       interior spaces are preserved at this stage; they are removed by the
       subsequent strip.
    2. Strip leading and trailing whitespace.
    3. Lowercase.

    The function is idempotent: ``normalise_email(normalise_email(x)) ==
    normalise_email(x)`` for all inputs.

    Args:
        email: The raw email address to normalise.

    Returns:
        The normalised email address string.
    """
    printable = "".join(ch for ch in email if ch.isprintable())
    return printable.strip().lower()
