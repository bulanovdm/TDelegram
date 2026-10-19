"""TelegramClient's wait for TDLib to catch up with what it missed offline."""

from __future__ import annotations

import threading
import time
from typing import Any

from tdelegram.client import TelegramClient
from tdelegram.transport import FakeTransport


def _connection(state: str) -> dict[str, Any]:
    return {"@type": "updateConnectionState", "state": {"@type": state}}


def _wake(transport: FakeTransport, client: TelegramClient, *states: str) -> None:
    """Send the first request, after which TDLib reports `states`.

    A dormant TDLib reports nothing until it is asked something, so the
    updates go out with the answer rather than ahead of it.
    """

    def _answer(request: dict[str, Any]) -> dict[str, Any]:
        for state in states:
            transport.add_update(_connection(state))
        return {"@type": "authorizationStateReady"}

    transport.add_response(lambda r: r.get("@type") == "getAuthorizationState", _answer)
    client.send_request({"@type": "getAuthorizationState"})


def test_waits_through_updating_until_ready(
    client: TelegramClient, transport: FakeTransport
) -> None:
    _wake(transport, client, "connectionStateConnecting", "connectionStateUpdating")
    later = threading.Timer(0.2, transport.add_update, [_connection("connectionStateReady")])
    later.start()
    try:
        assert client.wait_until_synced(5.0)
    finally:
        later.join()


def test_gives_up_when_ready_never_comes(client: TelegramClient, transport: FakeTransport) -> None:
    _wake(transport, client, "connectionStateUpdating")
    started = time.monotonic()
    assert not client.wait_until_synced(0.2)
    assert time.monotonic() - started < 2.0


def test_a_ready_that_came_first_is_not_missed(
    client: TelegramClient, transport: FakeTransport
) -> None:
    _wake(transport, client, "connectionStateReady")
    # Let the reader thread hand the update over before asking.
    deadline = time.monotonic() + 2.0
    while client.latest_update("updateConnectionState") is None and time.monotonic() < deadline:
        time.sleep(0.01)
    started = time.monotonic()
    assert client.wait_until_synced(5.0)
    assert time.monotonic() - started < 1.0


def test_losing_the_connection_means_not_caught_up(
    client: TelegramClient, transport: FakeTransport
) -> None:
    _wake(transport, client, "connectionStateReady", "connectionStateConnecting")
    deadline = time.monotonic() + 2.0
    while time.monotonic() < deadline:
        event = client.latest_update("updateConnectionState") or {}
        if event.get("state", {}).get("@type") == "connectionStateConnecting":
            break
        time.sleep(0.01)
    assert not client.wait_until_synced(0.1)


def test_waiting_leaves_the_update_queue_alone(
    client: TelegramClient, transport: FakeTransport
) -> None:
    """`watch` waits first and streams after; nothing it should see may be eaten."""
    message = {"@type": "updateNewMessage", "message": {"@type": "message", "id": 1}}

    def _answer(request: dict[str, Any]) -> dict[str, Any]:
        transport.add_update(message)
        transport.add_update(_connection("connectionStateReady"))
        return {"@type": "authorizationStateReady"}

    transport.add_response(lambda r: r.get("@type") == "getAuthorizationState", _answer)
    client.send_request({"@type": "getAuthorizationState"})
    assert client.wait_until_synced(5.0)
    kinds = []
    while (event := client.next_update(timeout=0.2)) is not None:
        kinds.append(event["@type"])
    assert kinds == ["updateNewMessage", "updateConnectionState"]
