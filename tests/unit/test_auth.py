"""Auth state machine: all eleven states covered."""

from __future__ import annotations

from typing import Any

from tdelegram.auth import response_for_state


class StaticProvider:
    def get_api_id(self) -> int:
        return 123

    def get_api_hash(self) -> str:
        return "hash"

    def get_database_key(self) -> str:
        return "key"

    def get_phone(self) -> str:
        return "+10000000000"

    def get_code(self) -> str:
        return "11111"

    def get_password(self) -> str:
        return "secret"

    def get_email(self) -> str:
        return "a@b.c"

    def get_email_code(self) -> str:
        return "22222"


def _req(state: str, data: dict[str, Any] | None = None) -> Any:
    return response_for_state(
        state,
        data or {},
        StaticProvider(),
        database_directory="/tmp/db",
        files_directory="/tmp/files",
    )


def test_wait_tdlib_parameters() -> None:
    req = _req("authorizationStateWaitTdlibParameters")
    assert req is not None and req["@type"] == "setTdlibParameters"
    assert req["api_id"] == 123


def test_the_database_key_rides_in_the_parameters() -> None:
    """TDLib dropped WaitEncryptionKey and checkDatabaseEncryptionKey.

    The key is part of setTdlibParameters now, which is the only place a
    locked database can be opened, so it must travel there.
    """
    import base64

    class KeyedProvider(StaticProvider):
        def get_database_key(self) -> str:
            return "s3cret"

    req = response_for_state(
        "authorizationStateWaitTdlibParameters",
        {},
        KeyedProvider(),
        database_directory="/tmp/db",
        files_directory="/tmp/files",
    )
    assert req is not None
    assert base64.b64decode(req["database_encryption_key"]) == b"s3cret"


def test_wait_phone() -> None:
    req = _req("authorizationStateWaitPhoneNumber")
    assert req is not None and req["@type"] == "setAuthenticationPhoneNumber"


def test_wait_code() -> None:
    req = _req("authorizationStateWaitCode")
    assert req is not None and req["@type"] == "checkAuthenticationCode"


def test_wait_password() -> None:
    req = _req("authorizationStateWaitPassword")
    assert req is not None and req["@type"] == "checkAuthenticationPassword"


def test_wait_email() -> None:
    req = _req("authorizationStateWaitEmailAddress")
    assert req is not None and req["@type"] == "setAuthenticationEmailAddress"


def test_wait_email_code_nested_envelope() -> None:
    req = _req("authorizationStateWaitEmailCode")
    assert req is not None and req["@type"] == "checkAuthenticationEmailCode"
    assert req["code"]["@type"] == "emailAddressAuthenticationCode"


def test_device_confirmation_raises_with_link() -> None:
    import pytest

    with pytest.raises(RuntimeError, match="QR/device confirmation"):
        _req("authorizationStateWaitOtherDeviceConfirmation", {"link": "https://t.me/x"})


def test_unregistered_raises() -> None:
    import pytest

    with pytest.raises(RuntimeError, match="not registered"):
        _req("authorizationStateWaitRegistration")


def test_premium_raises() -> None:
    import pytest

    with pytest.raises(RuntimeError, match="Premium"):
        _req("authorizationStateWaitPremiumPurchase")


def test_ready_returns_none() -> None:
    assert _req("authorizationStateReady") is None


def test_run_auth_retries_on_error_event() -> None:
    """One mistyped code (error event) must not kill the login attempt."""
    from tdelegram.auth import run_auth
    from tdelegram.loop import DispatchLoop
    from tdelegram.transport import FakeTransport

    transport = FakeTransport()
    loop = DispatchLoop(transport)
    client_id = transport.create_client_id()
    loop.ensure_client(client_id)
    loop.start()

    class FakeClient:
        def __init__(self) -> None:
            self.sent: list[dict] = []
            self._updates: list[dict] = [
                {
                    "@type": "updateAuthorizationState",
                    "authorization_state": {"@type": "authorizationStateWaitCode"},
                },
                {"@type": "error", "code": 400, "message": "PHONE_CODE_INVALID"},
                {
                    "@type": "updateAuthorizationState",
                    "authorization_state": {"@type": "authorizationStateWaitCode"},
                },
                {
                    "@type": "updateAuthorizationState",
                    "authorization_state": {"@type": "authorizationStateReady"},
                },
            ]

        def next_update(self, timeout: float = 1.0) -> dict | None:
            if not self._updates:
                return None
            return self._updates.pop(0)

        def send_request(self, request: dict) -> dict:
            self.sent.append(request)
            return {"@type": "ok"}

    fake = FakeClient()
    result = run_auth(
        fake,
        StaticProvider(),
        database_directory="/tmp/db",
        files_directory="/tmp/files",
        timeout=10.0,
    )  # type: ignore[arg-type]
    assert result == {"@type": "ok"}
    # Two code prompts sent (one per WaitCode update), error did not abort.
    assert len([s for s in fake.sent if s.get("@type") == "checkAuthenticationCode"]) == 2
    loop.stop()


class DormantClient:
    """A client that speaks only when spoken to, like the real TDLib.

    `td_create_client_id` merely reserves an id: the instance stays
    dormant and emits no updates at all until it receives its first
    request. Tests that hand updates out of `next_update` unprompted
    model a TDLib that does not exist, and hide bootstrap bugs.
    """

    def __init__(self, replies: dict[str, Any] | None = None) -> None:
        self.sent: list[dict[str, Any]] = []
        self._pending: list[dict[str, Any]] = []
        self._replies = replies or {}

    def next_update(self, timeout: float = 1.0) -> dict[str, Any] | None:
        return self._pending.pop(0) if self._pending else None

    def emit(self, state: str) -> None:
        self._pending.append(
            {
                "@type": "updateAuthorizationState",
                "authorization_state": {"@type": state},
            }
        )

    def send_request(self, request: dict[str, Any]) -> dict[str, Any]:
        self.sent.append(request)
        return self._replies.get(str(request.get("@type")), {"@type": "ok"})

    def types_sent(self) -> list[str]:
        return [str(s.get("@type")) for s in self.sent]


def _run(client: Any, timeout: float = 5.0) -> dict[str, Any]:
    from tdelegram.auth import run_auth

    return run_auth(
        client,
        StaticProvider(),
        timeout=timeout,
        database_directory="/tmp/db",
        files_directory="/tmp/files",
    )


def test_run_auth_bootstraps_dormant_client() -> None:
    """Regression: waiting on updates before sending anything hangs forever."""
    client = DormantClient(
        {"getAuthorizationState": {"@type": "authorizationStateWaitTdlibParameters"}}
    )

    def _send(request: dict[str, Any]) -> dict[str, Any]:
        client.sent.append(request)
        if request["@type"] == "setTdlibParameters":
            client.emit("authorizationStateReady")
        return client._replies.get(str(request.get("@type")), {"@type": "ok"})

    client.send_request = _send  # type: ignore[method-assign]
    assert _run(client) == {"@type": "ok"}
    # The probe must come first, or TDLib never wakes and never updates.
    assert client.types_sent()[0] == "getAuthorizationState"
    assert "setTdlibParameters" in client.types_sent()


def test_run_auth_ready_session_needs_only_the_probe() -> None:
    """An already-authorized session answers the probe and is done."""
    client = DormantClient({"getAuthorizationState": {"@type": "authorizationStateReady"}})
    assert _run(client) == {"@type": "ok"}
    assert client.types_sent() == ["getAuthorizationState"]


def test_run_auth_ignores_repeated_state_and_noise() -> None:
    """A re-emitted state must not prompt twice; other updates are skipped."""
    client = DormantClient({"getAuthorizationState": {"@type": "authorizationStateWaitCode"}})
    client._pending = [
        {"@type": "updateNewMessage", "message": {}},
        {
            "@type": "updateAuthorizationState",
            "authorization_state": {"@type": "authorizationStateWaitCode"},
        },
        {
            "@type": "updateAuthorizationState",
            "authorization_state": {"@type": "authorizationStateReady"},
        },
    ]
    assert _run(client) == {"@type": "ok"}
    codes = [s for s in client.types_sent() if s == "checkAuthenticationCode"]
    assert len(codes) == 1, "repeated state re-prompted for the same code"


def test_run_auth_retries_when_send_returns_error() -> None:
    """A rejected code leaves the state open so the next update re-prompts."""
    client = DormantClient(
        {
            "getAuthorizationState": {"@type": "authorizationStateWaitCode"},
            "checkAuthenticationCode": {
                "@type": "error",
                "code": 400,
                "message": "PHONE_CODE_INVALID",
            },
        }
    )
    client.emit("authorizationStateWaitCode")
    client.emit("authorizationStateReady")
    assert _run(client) == {"@type": "ok"}
    codes = [s for s in client.types_sent() if s == "checkAuthenticationCode"]
    assert len(codes) == 2, "error response must not latch the state shut"
