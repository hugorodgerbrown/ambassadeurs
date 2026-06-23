# Test factories for auth users.
#
# Account has been removed. Participant attributes live on matching.Registration.

import factory
from django.contrib.auth import get_user_model
from django.contrib.auth.models import User


class UserFactory(factory.django.DjangoModelFactory[User]):
    """Factory for the default Django User."""

    class Meta:
        model = get_user_model()
        django_get_or_create = ["username"]

    username = factory.Sequence(lambda n: f"user{n}")
    email = factory.LazyAttribute(lambda o: f"{o.username}@example.com")
