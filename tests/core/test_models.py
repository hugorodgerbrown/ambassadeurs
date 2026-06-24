# Tests for core model abstractions.

import pytest
from django.contrib.contenttypes.models import ContentType
from django.utils import timezone

from core.models import BaseModel, StateTransitionLog
from core.services import record_transition
from tests.core.factories import StateTransitionLogFactory
from tests.matching.factories import MatchFactory, RegistrationFactory

pytestmark = pytest.mark.django_db


# ---------------------------------------------------------------------------
# BaseModel
# ---------------------------------------------------------------------------


class _Concrete(BaseModel):
    """Throwaway concrete subclass that does not override to_string()."""

    class Meta:
        app_label = "core"
        managed = False


def test_base_model_requires_to_string() -> None:
    """A subclass that forgets to_string() raises NotImplementedError."""
    with pytest.raises(NotImplementedError):
        _Concrete().to_string()


# ---------------------------------------------------------------------------
# StateTransitionLog — model behaviour
# ---------------------------------------------------------------------------


def test_state_transition_log_to_string() -> None:
    """to_string returns a readable summary of content type, pk, and transition."""
    log = StateTransitionLogFactory.create(
        field_name="status",
        state_before="PROPOSED",
        state_after="ACCEPTED",
    )
    result = log.to_string()
    assert "status" in result
    assert "PROPOSED" in result
    assert "ACCEPTED" in result
    assert str(log.object_id) in result


def test_state_transition_log_str_delegates_to_to_string() -> None:
    """__str__ delegates to to_string (CLAUDE.md convention)."""
    log = StateTransitionLogFactory.create()
    assert str(log) == log.to_string()


def test_state_transition_log_default_ordering_is_newest_first() -> None:
    """Meta.ordering is -created_at so the most recent entry comes first."""
    log_a = StateTransitionLogFactory.create(state_after="ACCEPTED")
    log_b = StateTransitionLogFactory.create(state_after="DECLINED")
    qs = list(StateTransitionLog.objects.all())
    # log_b was created after log_a, so it should appear first.
    assert qs[0].pk == log_b.pk
    assert qs[1].pk == log_a.pk


def test_state_transition_log_gfk_resolves_to_target_instance() -> None:
    """The GenericForeignKey target attribute resolves to the original instance."""
    match = MatchFactory.create()
    # Supply content_type and object_id directly; do not pass _match_target so
    # the factory does not create a second, unrelated Match.
    log = StateTransitionLogFactory.create(
        content_type=ContentType.objects.get_for_model(match),
        object_id=match.pk,
    )
    log.refresh_from_db()
    # Accessing .target should resolve to the same Match row.
    assert log.target is not None
    assert log.target.pk == match.pk


def test_state_transition_log_queryset_filters() -> None:
    """StateTransitionLogQuerySet supports standard queryset operations."""
    StateTransitionLogFactory.create(field_name="status", state_after="ACCEPTED")
    StateTransitionLogFactory.create(field_name="status", state_after="DECLINED")
    assert StateTransitionLog.objects.filter(state_after="ACCEPTED").count() == 1
    assert StateTransitionLog.objects.all().count() == 2


# ---------------------------------------------------------------------------
# record_transition service
# ---------------------------------------------------------------------------


def test_record_transition_writes_one_row_with_correct_fields() -> None:
    """record_transition creates a single log row with all expected field values."""
    match = MatchFactory.create()
    before_count = StateTransitionLog.objects.count()
    before = timezone.now()

    log = record_transition(match, "status", before="PROPOSED", after="ACCEPTED")

    after = timezone.now()
    assert StateTransitionLog.objects.count() == before_count + 1
    assert log.field_name == "status"
    assert log.state_before == "PROPOSED"
    assert log.state_after == "ACCEPTED"
    assert log.object_id == match.pk
    assert log.content_type == ContentType.objects.get_for_model(match)
    assert log.created_at is not None
    assert before <= log.created_at <= after


def test_record_transition_works_with_registration_instance() -> None:
    """record_transition is model-agnostic — it works with Registration too."""
    reg = RegistrationFactory.create()

    log = record_transition(reg, "status", before="WAITING", after="MATCHED")

    assert log.field_name == "status"
    assert log.state_before == "WAITING"
    assert log.state_after == "MATCHED"
    assert log.object_id == reg.pk
    assert log.content_type == ContentType.objects.get_for_model(reg)
