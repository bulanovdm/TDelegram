"""Sending a file: the upload only finishes if the client waits for it.

TDLib answers sendMessage at once with a pending message and uploads in the
background, reporting the outcome later as an update. `media upload` used to
return on the pending message, and closing the session then closed TDLib under
the upload, so a PDF stayed unsent. These tests script the late outcome.
"""

from __future__ import annotations

import threading
import time
from typing import Any

from tdelegram.api import media as media_api
from tdelegram.client import TelegramClient
from tdelegram.files import uploaded_file_id, wait_for_send
from tdelegram.transport import FakeTransport

CHAT_ID = 1
PENDING_ID = 4242
FILE_ID = 77


def pending_message() -> dict[str, Any]:
    """What sendMessage answers for a document: the message, not yet sent."""
    return {
        "@type": "message",
        "id": PENDING_ID,
        "chat_id": CHAT_ID,
        "sending_state": {"@type": "messageSendingStatePending"},
        "content": {
            "@type": "messageDocument",
            "document": {
                "@type": "document",
                "file_name": "cv.pdf",
                "mime_type": "application/pdf",
                "document": {
                    "@type": "file",
                    "id": FILE_ID,
                    "size": 1000,
                    "remote": {"@type": "remoteFile", "uploaded_size": 0},
                },
            },
            "caption": {"@type": "formattedText", "text": "", "entities": []},
        },
    }


def succeeded(old_id: int = PENDING_ID, new_id: int = 99) -> dict[str, Any]:
    return {
        "@type": "updateMessageSendSucceeded",
        "old_message_id": old_id,
        "message": {"@type": "message", "id": new_id, "chat_id": CHAT_ID},
    }


def failed(old_id: int = PENDING_ID) -> dict[str, Any]:
    return {
        "@type": "updateMessageSendFailed",
        "old_message_id": old_id,
        "message": {"@type": "message", "id": old_id, "chat_id": CHAT_ID},
        "error": {"@type": "error", "code": 400, "message": "FILE_PARTS_INVALID"},
    }


def progress(file_id: int, uploaded: int) -> dict[str, Any]:
    return {
        "@type": "updateFile",
        "file": {
            "@type": "file",
            "id": file_id,
            "size": 1000,
            "remote": {"@type": "remoteFile", "uploaded_size": uploaded},
        },
    }


def script_send(transport: FakeTransport, client: TelegramClient, *later: dict[str, Any]) -> None:
    """Answer sendMessage with the pending message, and have `later` follow it."""

    def _answer(request: dict[str, Any]) -> dict[str, Any]:
        for update in later:
            transport.add_update(update, client_id=client.client_id)
        return pending_message()

    transport.add_simple_response("getChat", {"@type": "chat", "id": CHAT_ID})
    transport.add_response(lambda r: r.get("@type") == "sendMessage", _answer)


def test_upload_returns_once_the_message_is_sent(
    client: TelegramClient, transport: FakeTransport
) -> None:
    script_send(transport, client, succeeded())
    result = media_api.upload(client, "1", "/tmp/cv.pdf", allow_write=True)
    assert result["status"] == "sent"
    assert result["message"]["id"] == 99


def test_upload_reports_a_failed_send(client: TelegramClient, transport: FakeTransport) -> None:
    script_send(transport, client, failed())
    result = media_api.upload(client, "1", "/tmp/cv.pdf", allow_write=True)
    assert result["status"] == "failed"
    assert result["error"]["message"] == "FILE_PARTS_INVALID"


def test_upload_gives_up_when_nothing_arrives(
    client: TelegramClient, transport: FakeTransport
) -> None:
    script_send(transport, client)
    result = media_api.upload(client, "1", "/tmp/cv.pdf", timeout=0.3, allow_write=True)
    assert result == {"status": "pending", "message_id": PENDING_ID}


def test_upload_without_waiting_says_it_is_unconfirmed(
    client: TelegramClient, transport: FakeTransport
) -> None:
    script_send(transport, client)
    result = media_api.upload(client, "1", "/tmp/cv.pdf", wait=False, allow_write=True)
    assert result["status"] == "pending"
    assert result["pending_message"]["id"] == PENDING_ID
    assert "abandoned" in result["note"]


def test_a_message_that_is_not_ours_does_not_count(
    client: TelegramClient, transport: FakeTransport
) -> None:
    """Regression: any message that left the same chat was reported as ours."""
    transport.add_update(succeeded(old_id=PENDING_ID + 1), client_id=client.client_id)
    transport.add_update(failed(old_id=PENDING_ID + 1), client_id=client.client_id)
    result = wait_for_send(client, PENDING_ID, CHAT_ID, timeout=0.3)
    assert result == {"status": "pending", "message_id": PENDING_ID}


def test_the_chat_must_agree_when_the_update_names_one(
    client: TelegramClient, transport: FakeTransport
) -> None:
    elsewhere = succeeded()
    elsewhere["message"]["chat_id"] = CHAT_ID + 1
    transport.add_update(elsewhere, client_id=client.client_id)
    assert wait_for_send(client, PENDING_ID, CHAT_ID, timeout=0.3)["status"] == "pending"


def _feed(
    transport: FakeTransport, client: TelegramClient, steps: list[tuple[float, dict[str, Any]]]
) -> None:
    """Deliver each update after its delay, as an upload that takes a while does."""

    def _run() -> None:
        started = time.monotonic()
        for at, update in steps:
            time.sleep(max(0.0, at - (time.monotonic() - started)))
            transport.add_update(update, client_id=client.client_id)

    threading.Thread(target=_run, daemon=True).start()


def test_progress_keeps_a_slow_upload_waiting(
    client: TelegramClient, transport: FakeTransport
) -> None:
    """A big file on a slow link takes longer than --timeout; it is not stuck."""
    _feed(
        transport,
        client,
        [
            (0.2, progress(FILE_ID, 100)),
            (0.4, progress(FILE_ID, 200)),
            (0.6, progress(FILE_ID, 300)),
            (0.8, progress(FILE_ID, 400)),
            (1.0, succeeded()),
        ],
    )
    result = wait_for_send(client, PENDING_ID, CHAT_ID, timeout=0.6, file_id=FILE_ID)
    assert result["status"] == "sent"


def test_progress_of_another_file_does_not_keep_it_waiting(
    client: TelegramClient, transport: FakeTransport
) -> None:
    _feed(
        transport,
        client,
        [
            (0.15, progress(FILE_ID + 1, 100)),
            (0.3, progress(FILE_ID + 1, 200)),
            (0.8, succeeded()),
        ],
    )
    result = wait_for_send(client, PENDING_ID, CHAT_ID, timeout=0.4, file_id=FILE_ID)
    assert result["status"] == "pending"


def test_a_stalled_upload_does_not_keep_it_waiting(
    client: TelegramClient, transport: FakeTransport
) -> None:
    """The same size again is no progress."""
    _feed(
        transport,
        client,
        [
            (0.15, progress(FILE_ID, 100)),
            (0.3, progress(FILE_ID, 100)),
            (0.45, progress(FILE_ID, 100)),
            (1.0, succeeded()),
        ],
    )
    result = wait_for_send(client, PENDING_ID, CHAT_ID, timeout=0.5, file_id=FILE_ID)
    assert result["status"] == "pending"


def test_uploaded_file_id() -> None:
    assert uploaded_file_id(pending_message()) == FILE_ID
    assert uploaded_file_id({"message": pending_message()}) == FILE_ID
    assert uploaded_file_id({"@type": "message", "content": {"@type": "messageText"}}) is None
    assert uploaded_file_id({}) is None
