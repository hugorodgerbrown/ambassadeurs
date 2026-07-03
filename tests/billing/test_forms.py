# Tests for billing.forms.TipForm.

from billing.forms import TipForm


def test_valid_form_with_preset_amount() -> None:
    """A preset amount (e.g. CHF 10) with no message validates."""
    form = TipForm(data={"amount_chf": 10, "message": ""})
    assert form.is_valid()
    assert form.cleaned_data["amount_chf"] == 10
    assert form.cleaned_data["message"] == ""


def test_valid_form_with_message() -> None:
    """An amount plus a message under 280 characters validates."""
    form = TipForm(data={"amount_chf": 20, "message": "Thanks for the help!"})
    assert form.is_valid()
    assert form.cleaned_data["message"] == "Thanks for the help!"


def test_message_is_optional() -> None:
    """Omitting the message entirely still validates."""
    form = TipForm(data={"amount_chf": 5})
    assert form.is_valid()
    assert form.cleaned_data["message"] == ""


def test_amount_chf_minimum_boundary_is_valid() -> None:
    """The minimum allowed amount (1 CHF) validates."""
    form = TipForm(data={"amount_chf": 1})
    assert form.is_valid()


def test_amount_chf_below_minimum_is_rejected() -> None:
    """An amount of 0 is rejected (min_value=1)."""
    form = TipForm(data={"amount_chf": 0})
    assert not form.is_valid()
    assert "amount_chf" in form.errors


def test_amount_chf_maximum_boundary_is_valid() -> None:
    """The maximum allowed amount (500 CHF) validates."""
    form = TipForm(data={"amount_chf": 500})
    assert form.is_valid()


def test_amount_chf_above_maximum_is_rejected() -> None:
    """An amount above 500 CHF is rejected (max_value=500)."""
    form = TipForm(data={"amount_chf": 501})
    assert not form.is_valid()
    assert "amount_chf" in form.errors


def test_amount_chf_is_required() -> None:
    """Omitting amount_chf entirely is rejected."""
    form = TipForm(data={})
    assert not form.is_valid()
    assert "amount_chf" in form.errors


def test_message_over_max_length_is_rejected() -> None:
    """A message over 280 characters is rejected."""
    form = TipForm(data={"amount_chf": 5, "message": "x" * 281})
    assert not form.is_valid()
    assert "message" in form.errors


def test_message_at_max_length_is_valid() -> None:
    """A message of exactly 280 characters validates."""
    form = TipForm(data={"amount_chf": 5, "message": "x" * 280})
    assert form.is_valid()
