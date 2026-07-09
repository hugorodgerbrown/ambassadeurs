# Shared private helpers used across the ``public.views`` package.
#
# Kept intentionally tiny — anything used by more than one sibling module
# lives here rather than being duplicated or imported cross-module, so the
# dependency direction stays one-way (siblings import from ``_shared``,
# never from each other for these two helpers).

from __future__ import annotations

from django.http import HttpRequest

from matching.models import Registration


def _authenticated_registration(request: HttpRequest) -> Registration | None:
    """Return the Registration for the currently authenticated user, or None.

    Mirrors the ``DoesNotExist`` guard used in ``accounts/views.py``. Returns
    ``None`` for anonymous requests and for authenticated users who have no
    Registration (e.g. staff-only admin users).
    """
    if not request.user.is_authenticated:
        return None
    try:
        return Registration.objects.get(user=request.user)
    except Registration.DoesNotExist:
        return None


def _stripe_metadata_get(obj: object, key: str) -> str | None:
    """Return the string metadata value ``obj.metadata[key]``, or None.

    Stripe's ``StripeObject`` deliberately has no ``.get()`` method (calling
    it raises ``AttributeError``) — read defensively via ``getattr`` (which
    tolerates a missing ``metadata`` attribute) plus membership and subscript
    access instead of assuming metadata, or the key within it, is present.
    """
    metadata = getattr(obj, "metadata", None)
    if metadata is None or key not in metadata:
        return None
    return str(metadata[key])
