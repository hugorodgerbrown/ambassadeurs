# Test factories for auth users and their passkeys.
#
# Account has been removed. Participant attributes live on matching.Registration.

import factory
from django.contrib.auth import get_user_model
from django.contrib.auth.models import User

from accounts.models import Passkey


class UserFactory(factory.django.DjangoModelFactory[User]):
    """Factory for the default Django User."""

    class Meta:
        model = get_user_model()
        django_get_or_create = ["username"]

    username = factory.Sequence(lambda n: f"user{n}")
    email = factory.LazyAttribute(lambda o: f"{o.username}@example.com")


class PasskeyFactory(factory.django.DjangoModelFactory[Passkey]):
    """A saved passkey (its key is a placeholder: it can't verify a real ceremony)."""

    class Meta:
        model = Passkey

    user = factory.SubFactory(UserFactory)
    credential_id = factory.Sequence(lambda n: f"credential-{n}")
    public_key = b"not-a-real-key"
    name = "Passkey on iPhone"
