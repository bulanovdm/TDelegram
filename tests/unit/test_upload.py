"""Sending a file: the upload only finishes if the client waits for it.

TDLib answers sendMessage at once with a pending message and uploads in the
background, reporting the outcome later as an update. `media upload` used to
return on the pending message, and closing the session then closed TDLib under
the upload, so a PDF stayed unsent. These tests script the late outcome.
"""

from __future__ import annotations

import json
import threading
import time
from typing import Any

import pytest

from tdelegram.api import media as media_api
from tdelegram.client import TelegramClient
from tdelegram.errors import TelegramTimeoutError
from tdelegram.files import uploaded_file_id, wait_for_send
from tdelegram.loop import reset_loops
from tdelegram.transport import FakeTransport

CHAT_ID = 1
PENDING_ID = 4242
FILE_ID = 77


def pending_message(message_id: int = PENDING_ID) -> dict[str, Any]:
    """What sendMessage answers for a document: the message, not yet sent."""
    return {
        "@type": "message",
        "id": message_id,
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


def test_overlapping_uploads_do_not_take_each_others_updates(
    client: TelegramClient, transport: FakeTransport
) -> None:
    """Regression: uploads on one client read one shared queue, so one could swallow
    the other's result and the other report "pending" for a file that was sent."""
    ids = iter([PENDING_ID, PENDING_ID + 1])
    placed = threading.Semaphore(0)

    def _answer(request: dict[str, Any]) -> dict[str, Any]:
        placed.release()
        return pending_message(next(ids))

    transport.add_simple_response("getChat", {"@type": "chat", "id": CHAT_ID})
    transport.add_response(lambda r: r.get("@type") == "sendMessage", _answer)
    results: dict[str, dict[str, Any]] = {}

    def upload(name: str) -> None:
        results[name] = media_api.upload(client, "1", "/tmp/cv.pdf", timeout=3.0, allow_write=True)

    first = threading.Thread(target=upload, args=("first",))
    first.start()
    assert placed.acquire(timeout=5)
    second = threading.Thread(target=upload, args=("second",))
    second.start()
    assert placed.acquire(timeout=5)
    time.sleep(0.3)  # both are waiting now, the first for longer
    # The second upload's result comes first, to the one waiting longest. Given a
    # moment to act on it, the first now waits behind the second, which then
    # meets the first one's result. Sharing a queue, each throws away the other's.
    transport.add_update(succeeded(PENDING_ID + 1, 101), client_id=client.client_id)
    time.sleep(0.2)
    transport.add_update(succeeded(PENDING_ID, 100), client_id=client.client_id)
    first.join(5)
    second.join(5)
    assert results["first"]["message"]["id"] == 100
    assert results["second"]["message"]["id"] == 101


def test_the_first_answer_is_bounded_by_the_clients_own_timeout(
    transport: FakeTransport, monkeypatch: pytest.MonkeyPatch
) -> None:
    """sendMessage used to wait a fixed minute whatever the caller had asked for."""
    transport.add_simple_response("getChat", {"@type": "chat", "id": CHAT_ID})
    real_send = transport.send

    def silent_about_sendmessage(client_id: int, request: str) -> None:
        if json.loads(request).get("@type") != "sendMessage":
            real_send(client_id, request)

    monkeypatch.setattr(transport, "send", silent_about_sendmessage)
    quick = TelegramClient(transport, default_timeout=0.3)
    try:
        started = time.monotonic()
        with pytest.raises(TelegramTimeoutError):
            media_api.upload(quick, "1", "/tmp/cv.pdf", allow_write=True)
        assert time.monotonic() - started < 5
    finally:
        quick.close()
        reset_loops()


def never_delivered() -> list[dict[str, Any]]:
    """The newest messages of a chat: one pending PDF, one failed, one delivered."""
    stuck = pending_message()
    stuck["is_outgoing"] = True
    failed_out = {
        "@type": "message",
        "id": 4000,
        "is_outgoing": True,
        "sending_state": {
            "@type": "messageSendingStateFailed",
            "error": {"@type": "error", "code": 400, "message": "Message is too old"},
            "can_retry": True,
            "retry_after": 0.0,
        },
        "content": {"@type": "messageText", "text": {"text": "hi", "entities": []}},
    }
    delivered = {"@type": "message", "id": 3, "is_outgoing": True, "content": {}}
    received = {**pending_message(5), "is_outgoing": False}
    return [stuck, failed_out, delivered, received]


def test_unsent_lists_what_never_went_out(client: TelegramClient, transport: FakeTransport) -> None:
    transport.add_simple_response("getChat", {"@type": "chat", "id": CHAT_ID})
    transport.add_simple_response(
        "getChatHistory", {"@type": "messages", "messages": never_delivered()}
    )
    found = media_api.unsent(client, "1")
    assert [m["message_id"] for m in found] == [PENDING_ID, 4000]
    assert found[0]["file_name"] == "cv.pdf"
    assert found[0]["sending_state"] == {"state": "pending"}
    assert found[1]["sending_state"]["state"] == "failed"
    history = [r for _, r in transport.sent if r.get("@type") == "getChatHistory"]
    assert history[0]["only_local"] is True, "this is a courtesy check, not a trip to the server"


def test_unsent_is_empty_when_nothing_is_stuck(
    client: TelegramClient, transport: FakeTransport
) -> None:
    transport.add_simple_response("getChat", {"@type": "chat", "id": CHAT_ID})
    transport.add_simple_response(
        "getChatHistory", {"@type": "messages", "messages": never_delivered()[2:]}
    )
    assert media_api.unsent(client, "1") == []


def test_a_chat_that_cannot_be_read_is_no_news(
    client: TelegramClient, transport: FakeTransport
) -> None:
    """The check must never be the reason an upload does not happen."""
    transport.add_simple_response("getChat", {"@type": "chat", "id": CHAT_ID})
    transport.add_simple_response(
        "getChatHistory", {"@type": "error", "code": 500, "message": "database is locked"}
    )
    assert media_api.unsent(client, "1") == []
    transport.add_simple_response("searchPublicChat", {"@type": "chat"})
    assert media_api.unsent(client, "nobody") == []
