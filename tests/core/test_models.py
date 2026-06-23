# Tests for core model abstractions.

import pytest

from core.models import BaseModel


class _Concrete(BaseModel):
    """Throwaway concrete subclass that does not override to_string()."""

    class Meta:
        app_label = "core"
        managed = False


def test_base_model_requires_to_string() -> None:
    """A subclass that forgets to_string() raises NotImplementedError."""
    with pytest.raises(NotImplementedError):
        _Concrete().to_string()
