# Tests for the allauth account adapter.

from accounts.adapters import AccountAdapter


def test_clean_email_lowercases() -> None:
    """clean_email normalises the address to lowercase (invariant 5)."""
    adapter = AccountAdapter()
    assert adapter.clean_email("Foo.Bar@Example.COM") == "foo.bar@example.com"
