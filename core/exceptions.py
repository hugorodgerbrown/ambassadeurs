# Shared exception types for the project's state machines.
#
# StateTransitionError is the fail-hard-low signal raised by model
# state-mutation methods (e.g. Match.expire, Registration.pause) when the
# instance's current state is not a legal precursor to the requested
# transition. Model methods validate their own source state and raise this
# immediately (fail hard, low in the stack); service functions that call
# these methods either let it bubble to their caller or catch it where a
# benign, expected race makes a skip the correct response (catch high) —
# never both raise defensively and re-check the same condition at the
# service layer (docs/decisions/0017-state-transition-model-service-split.md).

from __future__ import annotations


class StateTransitionError(Exception):
    """Raised when a state-mutation method is called from an illegal state.

    Model methods that mutate a state field (e.g. ``Match.expire``,
    ``Registration.pause``) validate that ``self``'s current state is a legal
    precursor to the proposed target state before mutating anything, and raise
    this exception if it is not. Captures both the current and proposed state
    values (and, optionally, the offending object) so callers — and log lines
    — can report exactly which transition was rejected.

    Args:
        current: The state value the object was in when the transition was
            attempted.
        proposed: The state value the transition would have moved to.
        obj: The object the transition was attempted on, if available. Used
            only for the error message; not required.
    """

    def __init__(self, current: str, proposed: str, obj: object = None) -> None:
        self.current = current
        self.proposed = proposed
        self.obj = obj
        message = f"Illegal transition {current!r} -> {proposed!r}"
        if obj is not None:
            message += f" for {obj}"
        super().__init__(message)
