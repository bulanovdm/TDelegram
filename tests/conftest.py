"""Shared fixtures: FakeTransport-backed clients with no TDLib required."""

from __future__ import annotations

import pytest

from tdelegram.client import TelegramClient
from tdelegram.loop import reset_loops
from tdelegram.transport import FakeTransport


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
