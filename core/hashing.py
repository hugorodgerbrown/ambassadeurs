# Email hashing utilities for privacy-preserving post-decline history lookups.
#
# A keyed HMAC-SHA256 hash of a normalised email address is stored on DECLINED
# matches (``Match.declined_by_email_hash``) so that:
#   - prior decline history can be surfaced at re-registration time (to set
#     prior_decline_count on the new Registration) without retaining the raw
#     address once the User row is deleted; and
#   - the hash is keyed to EMAIL_HASH_SECRET so it cannot be reversed and does
#     not match a hash produced by a different site installation.
#
# See docs/decisions/0008-decline-email-hash-design.md for the full rationale
# (blind index, deterministic by design, pepper-keyed defence model).

import hashlib
import hmac

from django.conf import settings

from core.emails import normalise_email


def hash_email(email: str) -> str:
    """Return a deterministic HMAC-SHA256 hex digest of a normalised email address.

    This is a **blind index** for equality lookup (cross-check a new
    registrant's email against prior declines), not a password hash.  It must
    be deterministic (same email → same digest) so lookups work across separate
    registration events.  The secret pepper (``settings.EMAIL_HASH_SECRET``) is
    kept outside the database so a DB-only leak cannot brute-force low-entropy
    addresses without the key.

    Normalisation is delegated to ``core.emails.normalise_email`` so the hash
    and all stored email values share one canonical form.

    Args:
        email: The email address to hash (raw input; normalised before hashing).

    Returns:
        A 64-character lowercase hex string.
    """
    normalised = normalise_email(email)
    return hmac.new(
        settings.EMAIL_HASH_SECRET.encode(),
        normalised.encode(),
        hashlib.sha256,
    ).hexdigest()
