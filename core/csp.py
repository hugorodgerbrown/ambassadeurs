"""Guard on the runtime CSP layer (Invariant 10, SKI-171).

SKI-170 made the Content-Security-Policy editable at runtime: enabled
``csp.CspRule`` rows extend the settings baseline. Invariant 10 says the policy
never gains ``'unsafe-inline'`` or ``'unsafe-eval'``, and it binds that runtime
layer as much as the baseline — but nothing enforced it, and within minutes of
the SKI-170 deploy a rule adding ``'unsafe-inline'`` to ``style-src`` was live.

The failure mode is why this is worth a guard rather than a convention. A rule
can only *add* to a directive, so a bad one never breaks a page: it silently
permits what the policy was withholding, and the header reads as unremarkable.
There is nothing to notice and nothing to debug.

Two write paths reach ``CspRule``, and only one of them is a form:

1. the admin create/change form (guarded by ``core.admin.CspRuleForm``, which
   gives a field error rather than a 500);
2. ``csp.models.convert_report``, called by the *Add new CSP rule for selected
   violations* action on the violation changelist. It calls
   ``CspRule.objects.create`` directly. This is the path that matters most — a
   ``style-src-elem`` violation whose ``blocked_uri`` is the literal string
   ``"inline"`` normalises to ``'unsafe-inline'``, so the whole loosening is one
   click on a report that looks routine.

``reject_unsafe_csp_rule`` is therefore a ``pre_save`` receiver, not a
service-layer call. CLAUDE.md bans signals **for side effects** — side effects
belong inline in the service that causes them. This is neither a side effect nor
ours to put in a service: it is a last-line validity check on a third-party
model whose writes happen in third-party code. Failing closed at ``pre_save`` is
the only hook that covers every path, including a shell ``create()``.
"""

from __future__ import annotations

from typing import Any

from csp.models import CspRule
from django.core.exceptions import ValidationError
from django.utils.translation import gettext_lazy as _

# The two source expressions Invariant 10 withholds. Deliberately not a wider
# net: 'unsafe-hashes' and 'wasm-unsafe-eval' are real loosenings but much
# narrower ones, and quietly banning more than the invariant states would put
# the code and the documented rule out of step. Widen both together or neither.
UNSAFE_SOURCES = frozenset({"'unsafe-inline'", "'unsafe-eval'"})


def unsafe_source(value: str) -> str | None:
    """Return the banned source ``value`` normalises to, or None if it is fine.

    Normalisation matters: ``CspRule.clean_value`` turns a bare ``inline`` into
    ``'unsafe-inline'`` and upper-cases into lower, so checking the raw input
    would miss most of the ways the value can arrive. Check what will actually
    be written.
    """
    cleaned = CspRule.clean_value(value)
    return cleaned if cleaned in UNSAFE_SOURCES else None


def reject_unsafe_csp_rule(sender: type, instance: CspRule, **kwargs: Any) -> None:
    """Refuse to save a CspRule carrying an unsafe source (Invariant 10).

    Connected to ``pre_save`` in ``core.apps.CoreConfig.ready`` — see the module
    docstring for why this is a signal rather than a service call.
    """
    if banned := unsafe_source(instance.value):
        raise ValidationError(
            _("%(value)s is not permitted in the CSP (Invariant 10).")
            % {"value": banned}
        )
