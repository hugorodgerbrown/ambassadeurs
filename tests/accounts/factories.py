# Test factories for auth users and the Account profile.

import factory
from django.contrib.auth import get_user_model
from django.contrib.auth.models import User

from accounts.models import Account


class UserFactory(factory.django.DjangoModelFactory[User]):
    """Factory for the default Django User."""

    class Meta:
        model = get_user_model()
        django_get_or_create = ["username"]

    username = factory.Sequence(lambda n: f"user{n}")
    email = factory.LazyAttribute(lambda o: f"{o.username}@example.com")


class AccountFactory(factory.django.DjangoModelFactory[Account]):
    """Factory for the Account profile."""

    class Meta:
        model = Account

    user = factory.SubFactory(UserFactory)
    phone = factory.Sequence(lambda n: f"+4179000{n:04d}")
    preferred_language = "en"
