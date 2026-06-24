# Test factories for the core app.

import factory
from django.contrib.contenttypes.models import ContentType

from core.models import StateTransitionLog
from tests.matching.factories import MatchFactory


class StateTransitionLogFactory(factory.django.DjangoModelFactory[StateTransitionLog]):
    """Factory for StateTransitionLog.

    The ``target`` GenericForeignKey is populated from a ``MatchFactory``
    instance by default; override ``content_type`` and ``object_id`` to point
    at a different model instance.

    The GFK target is built via a ``Params`` trait rather than a bare
    ``_target`` attribute to work correctly with factory_boy's build pipeline.
    """

    class Meta:
        model = StateTransitionLog
        exclude = ["_match_target"]

    # Build a Match instance as the default GFK target.
    _match_target = factory.SubFactory(MatchFactory)

    content_type = factory.LazyAttribute(
        lambda o: ContentType.objects.get_for_model(o._match_target)
    )
    object_id = factory.LazyAttribute(lambda o: o._match_target.pk)

    field_name = "status"
    state_before = "PROPOSED"
    state_after = "ACCEPTED"
