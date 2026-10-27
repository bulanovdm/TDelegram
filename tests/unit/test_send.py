"""Sending text: the command returns once Telegram has the message.

Same shape as a file upload, only quicker. TDLib answers sendMessage with a
pending message and delivers it in the background, so a client that returns on
the answer and closes can take the message down with it.
"""

from __future__ import annotations

from typing import Any

import pytest

from tdelegram.api import messages
from tdelegram.client import TelegramClient
from tdelegram.errors import InvalidRequest, TelegramTimeoutError, WriteConfirmationRequired
from tdelegram.transport import FakeTransport

CHAT_ID = 1
PENDING_ID = 9


def script_text_send(
    transport: FakeTransport, client: TelegramClient, *later: dict[str, Any]
) -> None:
    """Answer sendMessage with a pending text message, and have `later` follow it."""

    def _answer(request: dict[str, Any]) -> dict[str, Any]:
        for update in later:
            transport.add_update(update, client_id=client.client_id)
        return {
            "@type": "message",
            "id": PENDING_ID,
            "chat_id": CHAT_ID,
            "sending_state": {"@type": "messageSendingStatePending"},
        }

    transport.add_simple_response("getChat", {"@type": "chat", "id": CHAT_ID})
    transport.add_response(lambda r: r.get("@type") == "sendMessage", _answer)


def sent(old_id: int = PENDING_ID, new_id: int = 10) -> dict[str, Any]:
    return {
        "@type": "updateMessageSendSucceeded",
        "old_message_id": old_id,
        "message": {"@type": "message", "id": new_id, "chat_id": CHAT_ID},
    }


def refused(code: int = 400, text: str = "CHAT_WRITE_FORBIDDEN") -> dict[str, Any]:
    return {
        "@type": "updateMessageSendFailed",
        "old_message_id": PENDING_ID,
        "message": {"@type": "message", "id": PENDING_ID, "chat_id": CHAT_ID},
        "error": {"@type": "error", "code": code, "message": text},
    }


def test_send_returns_the_message_as_sent(client: TelegramClient, transport: FakeTransport) -> None:
    script_text_send(transport, client, sent())
    message = messages.send(client, "1", "hi", allow_write=True)
    assert message["id"] == 10, "the pending id is a local one and stops meaning anything"


def test_send_raises_what_telegram_refused(
    client: TelegramClient, transport: FakeTransport
) -> None:
    script_text_send(transport, client, refused())
    with pytest.raises(InvalidRequest) as caught:
        messages.send(client, "1", "hi", allow_write=True)
    assert caught.value.code == 400
    assert caught.value.message == "CHAT_WRITE_FORBIDDEN"
    assert caught.value.method == "sendMessage"


def test_send_raises_when_nothing_confirms_it(
    client: TelegramClient, transport: FakeTransport
) -> None:
    script_text_send(transport, client)
    with pytest.raises(TelegramTimeoutError, match="did not confirm"):
        messages.send(client, "1", "hi", timeout=0.3, allow_write=True)


def test_send_without_waiting_returns_the_pending_message(
    client: TelegramClient, transport: FakeTransport
) -> None:
    script_text_send(transport, client)
    assert messages.send(client, "1", "hi", wait=False, allow_write=True)["id"] == PENDING_ID


def test_a_reply_waits_too(client: TelegramClient, transport: FakeTransport) -> None:
    script_text_send(transport, client, sent())
    assert messages.reply(client, "1", 5, "yo", allow_write=True)["id"] == 10


def test_an_unconfirmed_write_leaves_nothing_listening(
    client: TelegramClient, transport: FakeTransport
) -> None:
    """The watch comes off when the gate refuses, so a long-lived client does not
    pile up a handler for every send that was only previewed."""
    script_text_send(transport, client)
    before = len(client._subscriber.handlers)
    with pytest.raises(WriteConfirmationRequired):
        messages.send(client, "1", "hi")
    assert len(client._subscriber.handlers) == before
    with pytest.raises(TelegramTimeoutError):
        messages.send(client, "1", "hi", timeout=0.2, allow_write=True)
    assert len(client._subscriber.handlers) == before
