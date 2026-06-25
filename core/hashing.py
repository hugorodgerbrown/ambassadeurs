# Email hashing utilities for privacy-preserving post-season analysis.
#
# A salted HMAC-SHA256 hash of a normalised email address is stored on DECLINED
# matches so that:
#   - prior decline history can be surfaced at re-registration time (to set
#     prior_decline_count on the new Registration) without retaining the raw
#     address once the User row is deleted; and
#   - the hash is keyed to EMAIL_HASH_SECRET so it cannot be reversed and does
#     not match a hash produced by a different site installation.

import hashlib
import hmac

from django.conf import settings


def hash_email(email: str) -> str:
    """Return a salted HMAC-SHA256 hex digest of a normalised email address.

    The email is stripped and lowercased before hashing so that the same
    address always produces the same digest regardless of how it was typed.
    The secret is taken from ``settings.EMAIL_HASH_SECRET``.

    Args:
        email: The email address to hash (may have leading/trailing whitespace
            or mixed case; both are normalised before hashing).

    Returns:
        A 64-character lowercase hex string.
    """
    normalised = email.strip().lower()
    return hmac.new(
        settings.EMAIL_HASH_SECRET.encode(),
        normalised.encode(),
        hashlib.sha256,
    ).hexdigest()
