"""Shared fixtures: FakeTransport-backed clients with no TDLib required."""

from __future__ import annotations

from typing import Any

import pytest

from tdelegram.client import TelegramClient
from tdelegram.loop import reset_loops
from tdelegram.transport import FakeTransport


@pytest.fixture(autouse=True)
def requests_match_the_schema(monkeypatch: pytest.MonkeyPatch) -> Any:
    """Fail any test whose code sent TDLib a request TDLib does not accept.

    FakeTransport already answers such a request with a 400, which usually
    fails the test on its own -- unless the code under test swallows errors,
    or the test only checks an exit code. This makes the failure name the
    request and the field instead.
    """
    created: list[FakeTransport] = []
    original_init = FakeTransport.__init__

    def _tracking_init(self: FakeTransport, *args: Any, **kwargs: Any) -> None:
        original_init(self, *args, **kwargs)
        created.append(self)

    monkeypatch.setattr(FakeTransport, "__init__", _tracking_init)
    yield
    violations = [
        f"{request.get('@type')}: {'; '.join(problems)}"
        for transport in created
        for request, problems in transport.schema_violations
    ]
    assert not violations, "requests that do not match td_api.tl:\n" + "\n".join(violations)


@pytest.fixture
def transport() -> FakeTransport:
    return FakeTransport()


@pytest.fixture
def client(transport: FakeTransport) -> TelegramClient:
    c = TelegramClient(transport)
    yield c
    try:
        c.close()
    except Exception:
        pass
    reset_loops()


def auth_update(state: str, extra: dict | None = None) -> dict:
    obj: dict = {
        "@type": "updateAuthorizationState",
        "@client_id": 1,
        "authorization_state": {"@type": state},
    }
    if extra:
        obj["authorization_state"].update(extra)
    return obj
