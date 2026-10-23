# Tests for passkeys: the service (accounts.passkeys) and its JSON views.
#
# Ported from the Titan template's tests/accounts/test_passkeys.py (ADR 0030).
# py_webauthn's verifiers are replaced with stubs: a real signature needs a
# real authenticator, which a browser with a virtual authenticator supplies.
# These tests hold everything around the verification.

import base64
import json
from types import SimpleNamespace
from typing import Any

import pytest
from django.contrib.auth.models import User
from django.core.cache import cache
from django.test import Client, override_settings
from django.urls import reverse

from accounts import passkeys
from accounts.models import Passkey
from tests.accounts.factories import PasskeyFactory, UserFactory

pytestmark = pytest.mark.django_db

CREDENTIAL = {
    "id": "credential-1",
    "rawId": "credential-1",
    "type": "public-key",
    "response": {},
}


@pytest.fixture
def user() -> User:
    """A participant with an address and a name."""
    return UserFactory.create(email="ada@example.com", first_name="Ada", last_name="L")


@pytest.fixture
def signed_in(user: User) -> Client:
    """A client signed in as ``user``."""
    client = Client()
    client.force_login(user)
    return client


@pytest.fixture
def verified_registration(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make every registration verify, as credential ``AQID``."""
    result = SimpleNamespace(
        credential_id=b"\x01\x02\x03",
        credential_public_key=b"key",
        sign_count=0,
        credential_backed_up=True,
    )
    monkeypatch.setattr(
        passkeys.webauthn, "verify_registration_response", lambda **_: result
    )


@pytest.fixture
def verified_sign_in(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make every sign-in verify."""
    result = SimpleNamespace(new_sign_count=7, credential_backed_up=True)
    monkeypatch.setattr(
        passkeys.webauthn, "verify_authentication_response", lambda **_: result
    )


def post_json(client: Client, url: str, data: Any) -> Any:
    """POST ``data`` as JSON."""
    return client.post(url, json.dumps(data), content_type="application/json")


# ---------------------------------------------------------------------------
# Adding a passkey
# ---------------------------------------------------------------------------


def test_registration_options_name_the_site_and_skip_my_passkeys(
    signed_in: Client, user: User, settings: Any
) -> None:
    """Options carry this site, the user, a challenge, and exclude existing passkeys."""
    PasskeyFactory.create(user=user, credential_id="AQID")
    response = signed_in.post(reverse("accounts:passkey_register_options"))
    options = response.json()
    assert options["rp"] == {
        "id": settings.WEBAUTHN_RP_ID,
        "name": settings.WEBAUTHN_RP_NAME,
    }
    assert options["user"]["name"] == "ada@example.com"
    assert options["user"]["displayName"] == "Ada L"
    assert options["challenge"]
    assert [c["id"] for c in options["excludeCredentials"]] == ["AQID"]
    assert options["authenticatorSelection"]["userVerification"] == "required"
    assert options["authenticatorSelection"]["residentKey"] == "required"


def test_registration_options_user_id_is_not_personal_data(
    signed_in: Client, user: User
) -> None:
    """The WebAuthn user handle never carries the address (the spec forbids it)."""
    options = signed_in.post(reverse("accounts:passkey_register_options")).json()
    handle = base64.urlsafe_b64decode(options["user"]["id"] + "==")
    assert b"ada" not in handle
    assert len(handle) == 32


def test_register_saves_a_passkey_named_for_the_device(
    signed_in: Client, user: User, verified_registration: None
) -> None:
    """A verified answer becomes a passkey named after the device."""
    signed_in.post(reverse("accounts:passkey_register_options"))
    response = signed_in.post(
        reverse("accounts:passkey_register"),
        json.dumps(CREDENTIAL),
        content_type="application/json",
        headers={
            "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X)"
        },
    )
    assert response.status_code == 201
    passkey = Passkey.objects.get()
    assert passkey.user == user
    assert passkey.name == "Passkey on iPhone" == response.json()["name"]
    assert passkey.credential_id == "AQID"
    assert passkey.backed_up


def test_register_needs_the_challenge_first(
    signed_in: Client, verified_registration: None
) -> None:
    """Without options there is no challenge to verify against."""
    response = post_json(signed_in, reverse("accounts:passkey_register"), CREDENTIAL)
    assert response.status_code == 400
    assert not Passkey.objects.exists()


def test_register_challenge_is_used_once(
    signed_in: Client, verified_registration: None
) -> None:
    """A second answer to the same challenge finds none left."""
    signed_in.post(reverse("accounts:passkey_register_options"))
    post_json(signed_in, reverse("accounts:passkey_register"), CREDENTIAL)
    Passkey.objects.all().delete()
    response = post_json(signed_in, reverse("accounts:passkey_register"), CREDENTIAL)
    assert response.status_code == 400


def test_register_refuses_a_duplicate(
    signed_in: Client, verified_registration: None
) -> None:
    """A credential already saved isn't saved twice."""
    PasskeyFactory.create(credential_id="AQID")
    signed_in.post(reverse("accounts:passkey_register_options"))
    response = post_json(signed_in, reverse("accounts:passkey_register"), CREDENTIAL)
    assert response.status_code == 400
    assert Passkey.objects.count() == 1


def test_register_refuses_what_does_not_verify(signed_in: Client) -> None:
    """py_webauthn's rejection is a 400, with no detail sent back."""
    signed_in.post(reverse("accounts:passkey_register_options"))
    response = post_json(signed_in, reverse("accounts:passkey_register"), CREDENTIAL)
    assert response.status_code == 400
    assert response.json() == {"error": "not_verified"}


def test_register_refuses_a_body_that_is_not_json(signed_in: Client) -> None:
    """Garbage is a 400."""
    response = signed_in.post(
        reverse("accounts:passkey_register"), "nope", content_type="application/json"
    )
    assert response.status_code == 400


def test_registering_needs_sign_in() -> None:
    """Signed out, both endpoints answer 401 JSON rather than a login redirect."""
    client = Client()
    response = client.post(reverse("accounts:passkey_register_options"))
    assert response.status_code == 401
    assert response.json() == {"error": "not_signed_in"}
    response = post_json(client, reverse("accounts:passkey_register"), CREDENTIAL)
    assert response.status_code == 401


def test_registering_refuses_get(signed_in: Client) -> None:
    """The JSON endpoints are POST-only."""
    assert (
        signed_in.get(reverse("accounts:passkey_register_options")).status_code == 405
    )
    assert signed_in.get(reverse("accounts:passkey_register")).status_code == 405


# ---------------------------------------------------------------------------
# Removing a passkey
# ---------------------------------------------------------------------------


def test_delete_removes_my_passkey(signed_in: Client, user: User) -> None:
    """The owner's POST deletes the passkey and lands on the account page."""
    passkey = PasskeyFactory.create(user=user)
    response = signed_in.post(reverse("accounts:passkey_delete", args=[passkey.pk]))
    assert response.status_code == 302
    assert response.url == reverse("accounts:detail")
    assert not Passkey.objects.exists()


def test_delete_cannot_touch_another_users_passkey(signed_in: Client) -> None:
    """Someone else's passkey id is a 404, and the passkey stays."""
    passkey = PasskeyFactory.create()
    response = signed_in.post(reverse("accounts:passkey_delete", args=[passkey.pk]))
    assert response.status_code == 404
    assert Passkey.objects.filter(pk=passkey.pk).exists()


def test_delete_refuses_get(signed_in: Client, user: User) -> None:
    """A GET (a prefetch, a crawler) never deletes."""
    passkey = PasskeyFactory.create(user=user)
    response = signed_in.get(reverse("accounts:passkey_delete", args=[passkey.pk]))
    assert response.status_code == 405
    assert Passkey.objects.exists()


def test_delete_needs_sign_in() -> None:
    """Signed out, the delete redirects to the login page."""
    passkey = PasskeyFactory.create()
    response = Client().post(reverse("accounts:passkey_delete", args=[passkey.pk]))
    assert response.status_code == 302
    assert reverse("accounts:login") in response.url
    assert Passkey.objects.exists()


# ---------------------------------------------------------------------------
# Signing in with one
# ---------------------------------------------------------------------------


def signed(challenge: str, credential: dict[str, Any] = CREDENTIAL) -> dict[str, Any]:
    """Return ``credential`` as a browser's answer to ``challenge``.

    The signature itself is stubbed by ``verified_sign_in``.
    """
    client_data = json.dumps({"type": "webauthn.get", "challenge": challenge}).encode()
    encoded = base64.urlsafe_b64encode(client_data).decode().rstrip("=")
    return {**credential, "response": {"clientDataJSON": encoded}}


def options(client: Client) -> str:
    """Ask for sign-in options; return the challenge."""
    response = client.post(reverse("accounts:passkey_sign_in_options"))
    return str(response.json()["challenge"])


def answer(client: Client, credential: Any) -> Any:
    """Post a sign-in answer."""
    return post_json(
        client, reverse("accounts:passkey_sign_in"), {"credential": credential}
    )


def sign_in_with(client: Client, credential: Any) -> Any:
    """Fetch options, then post ``credential`` signed over their challenge."""
    challenge = options(client)
    if isinstance(credential, dict) and "id" in credential:
        credential = signed(challenge, credential)
    return answer(client, credential)


def test_sign_in_options_allow_any_passkey(settings: Any) -> None:
    """No allowCredentials: the browser offers every passkey it holds for this site."""
    options = Client().post(reverse("accounts:passkey_sign_in_options")).json()
    assert options["challenge"]
    assert not options.get("allowCredentials")
    assert options["userVerification"] == "required"
    # passkeys.js signals an unknown passkey under this RP ID.
    assert options["rpId"] == settings.WEBAUTHN_RP_ID


def test_passkey_signs_in_its_owner(verified_sign_in: None) -> None:
    """A verified passkey signs its owner in, to the account page, and records it."""
    client = Client()
    passkey = PasskeyFactory.create(credential_id="credential-1")
    response = sign_in_with(client, CREDENTIAL)
    assert response.status_code == 200
    assert response.json() == {"next": reverse("accounts:detail")}
    assert client.session["_auth_user_id"] == str(passkey.user.pk)
    passkey.refresh_from_db()
    assert passkey.sign_count == 7
    assert passkey.last_used_at is not None


def test_unknown_passkey_is_named_so_the_device_can_forget_it() -> None:
    """A passkey deleted here answers 404 with its id."""
    response = sign_in_with(Client(), CREDENTIAL)
    assert response.status_code == 404
    assert response.json() == {
        "error": "unknown_passkey",
        "credentialId": "credential-1",
    }


def test_passkey_that_does_not_verify() -> None:
    """A bad signature is a 400 and nobody is signed in."""
    client = Client()
    PasskeyFactory.create(credential_id="credential-1")
    response = sign_in_with(client, CREDENTIAL)
    assert response.status_code == 400
    assert "_auth_user_id" not in client.session


def test_passkey_sign_in_needs_the_challenge(verified_sign_in: None) -> None:
    """An answer to a challenge this session wasn't given fails."""
    client = Client()
    PasskeyFactory.create(credential_id="credential-1")
    assert answer(client, signed("never-issued")).status_code == 400
    options(client)
    assert answer(client, signed("never-issued")).status_code == 400


def test_two_ceremonies_at_once(verified_sign_in: None) -> None:
    """Two tabs each get a challenge; either answer signs in, once."""
    client = Client()
    PasskeyFactory.create(credential_id="credential-1")
    first, second = options(client), options(client)
    assert answer(client, signed(first)).status_code == 200
    assert answer(client, signed(second)).status_code == 200
    assert answer(client, signed(second)).status_code == 400


def test_a_failed_answer_spends_only_its_own_challenge() -> None:
    """A bad signature uses up the challenge it signed and no other."""
    client = Client()
    PasskeyFactory.create(credential_id="credential-1")
    first, second = options(client), options(client)
    assert answer(client, signed(first)).status_code == 400
    assert client.session["passkeys.sign_in_challenges"] == [second]


def test_old_challenges_lapse(verified_sign_in: None) -> None:
    """Only the last few challenges are kept."""
    client = Client()
    PasskeyFactory.create(credential_id="credential-1")
    oldest = options(client)
    for _ in range(passkeys.MAX_SIGN_IN_CHALLENGES):
        options(client)
    assert answer(client, signed(oldest)).status_code == 400


def test_passkey_for_a_closed_account(verified_sign_in: None) -> None:
    """An inactive account's passkey doesn't sign in."""
    client = Client()
    PasskeyFactory.create(
        credential_id="credential-1", user=UserFactory.create(is_active=False)
    )
    assert sign_in_with(client, CREDENTIAL).status_code == 403
    assert "_auth_user_id" not in client.session


@pytest.mark.parametrize("body", ["nope", "[]", '{"credential": "x"}'])
def test_passkey_sign_in_refuses_bad_bodies(body: str) -> None:
    """Anything but {"credential": {...}} is a 400."""
    response = Client().post(
        reverse("accounts:passkey_sign_in"), body, content_type="application/json"
    )
    assert response.status_code == 400


def test_credential_without_an_id() -> None:
    """A credential with no id can't be looked up."""
    assert sign_in_with(Client(), {"type": "public-key"}).status_code == 400


@pytest.mark.parametrize("client_data", ["%%%", "bm90IGpzb24", "WzFd"])
def test_credential_with_unreadable_client_data(client_data: str) -> None:
    """A clientDataJSON that isn't base64url JSON with a challenge is a 400."""
    client = Client()
    options(client)
    credential = {**CREDENTIAL, "response": {"clientDataJSON": client_data}}
    assert answer(client, credential).status_code == 400


@override_settings(RATELIMIT_ENABLE=True)
def test_passkey_options_are_rate_limited() -> None:
    """Past 30 challenges per IP per 10 minutes the options endpoint answers 429."""
    cache.clear()
    client = Client(REMOTE_ADDR="10.30.0.1")
    for _ in range(30):
        client.post(reverse("accounts:passkey_sign_in_options"))
    response = client.post(reverse("accounts:passkey_sign_in_options"))
    assert response.status_code == 429
    assert response.json() == {"error": "rate_limited"}
    cache.clear()


@override_settings(RATELIMIT_ENABLE=True)
def test_passkey_register_is_rate_limited(signed_in: Client) -> None:
    """Past 30 attempts per IP per 10 minutes registration answers 429."""
    cache.clear()
    for _ in range(30):
        post_json(signed_in, reverse("accounts:passkey_register"), CREDENTIAL)
    response = post_json(signed_in, reverse("accounts:passkey_register"), CREDENTIAL)
    assert response.status_code == 429
    cache.clear()


@pytest.mark.parametrize(
    ("user_agent", "name"),
    [
        ("Mozilla/5.0 (iPad; CPU OS 18_0 like Mac OS X)", "Passkey on iPad"),
        ("Mozilla/5.0 (Linux; Android 15; Pixel 9)", "Passkey on Android"),
        ("Mozilla/5.0 (X11; CrOS x86_64 14541.0.0)", "Passkey on Chromebook"),
        ("Mozilla/5.0 (Macintosh; Intel Mac OS X 15_0)", "Passkey on Mac"),
        ("Mozilla/5.0 (Windows NT 10.0; Win64; x64)", "Passkey on Windows"),
        ("Mozilla/5.0 (X11; Linux x86_64)", "Passkey on Linux"),
        ("curl/8", "Passkey"),
    ],
)
def test_default_name(user_agent: str, name: str) -> None:
    """New passkeys are named after the device they were made on."""
    assert passkeys.default_name(user_agent) == name


# ---------------------------------------------------------------------------
# Pages
# ---------------------------------------------------------------------------


def test_login_page_offers_a_passkey() -> None:
    """The login page carries the (hidden until supported) passkey section."""
    response = Client().get(reverse("accounts:login"))
    content = response.content.decode()
    assert "data-passkey-sign-in" in content
    assert reverse("accounts:passkey_sign_in_options") in content
    assert "js/passkeys.js" in content


def test_account_page_lists_my_passkeys_only(signed_in: Client, user: User) -> None:
    """The account page lists the user's passkeys with a remove button each."""
    mine = PasskeyFactory.create(user=user, name="Passkey on Mac")
    PasskeyFactory.create(name="Someone else's key")
    response = signed_in.get(reverse("accounts:detail"))
    content = response.content.decode()
    assert "Passkey on Mac" in content
    assert "Someone else&#x27;s key" not in content
    assert reverse("accounts:passkey_delete", args=[mine.pk]) in content
    assert "data-passkey-register" in content


def test_account_page_without_passkeys(signed_in: Client) -> None:
    """With none yet, the account page says so."""
    response = signed_in.get(reverse("accounts:detail"))
    assert "added a passkey yet." in response.content.decode()
