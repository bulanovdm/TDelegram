"""File upload/download helpers with honest delivery semantics."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from tdelegram.client import TelegramClient


def download(
    client: TelegramClient,
    file_id: int,
    *,
    timeout: float = 300.0,
    wait_for_completion: bool = True,
) -> dict[str, Any]:
    """Download via downloadFile(synchronous=true). Returns the final file object.

    No `allow_write` is passed: downloadFile is a read in the registry, and the
    registry is what decides. Granting permission here would silently survive a
    reclassification.
    """
    result = client.call(
        "downloadFile",
        {"file_id": file_id, "priority": 1, "offset": 0, "limit": 0, "synchronous": True},
        timeout=timeout,
    )
    # Synchronous mode already blocks; optionally confirm local path exists.
    if wait_for_completion:
        deadline = time.monotonic() + min(30.0, timeout)
        while time.monotonic() < deadline:
            local = (result.get("local") or {}) if isinstance(result, dict) else {}
            if local.get("is_downloading_completed"):
                break
            if local.get("path"):
                break
            time.sleep(0.2)
    return result


def send_file(
    client: TelegramClient,
    chat_id: int,
    path: str | Path,
    *,
    caption: str = "",
    timeout: float = 60.0,
    allow_write: bool = False,
) -> dict[str, Any]:
    """Upload a file as a document. Returns the pending message (delivery unconfirmed).

    This used to pass allow_write=True itself, so `media upload` sent the file
    to whatever chat it was pointed at without --yes: the one mutating command
    the gate did not cover.
    """
    local_path = str(Path(path).expanduser())
    content = {
        "@type": "inputMessageDocument",
        "document": {
            "@type": "inputDocument",
            "document": {"@type": "inputFileLocal", "path": local_path},
        },
        "caption": {"@type": "formattedText", "text": caption, "entities": []},
    }
    return client.call(
        "sendMessage",
        {"chat_id": chat_id, "input_message_content": content},
        timeout=timeout,
        allow_write=allow_write,
    )


def uploaded_file_id(pending: dict[str, Any]) -> int | None:
    """The id of the file a just-sent document message carries, if it has one."""
    message = pending.get("message") or pending
    document = (message.get("content") or {}).get("document") or {}
    file_id = (document.get("document") or {}).get("id")
    return file_id if isinstance(file_id, int) else None


def _is_ours(event: dict[str, Any], pending_message_id: int, chat_id: int) -> bool:
    """Whether a send update is about the message we sent, not another one's.

    Matching on the chat alone reported "sent" for any message that left it
    meanwhile. The local id is the identity; the chat only has to agree when
    the update says which one it is.
    """
    if event.get("old_message_id") != pending_message_id:
        return False
    chat = (event.get("message") or {}).get("chat_id")
    return chat is None or chat == chat_id


def wait_for_send(
    client: TelegramClient,
    pending_message_id: int,
    chat_id: int,
    *,
    timeout: float = 60.0,
    file_id: int | None = None,
) -> dict[str, Any]:
    """Block until updateMessageSendSucceeded/Failed for a pending send.

    `timeout` is how long to go without news, not a cap on the whole send: a
    large file on a slow link is still being uploaded, and an `updateFile` for
    `file_id` showing more of it gone up starts the clock again.
    """
    deadline = time.monotonic() + timeout
    uploaded = 0
    while (remaining := deadline - time.monotonic()) > 0:
        event = client.next_update(timeout=min(1.0, remaining))
        if event is None:
            continue
        etype = event.get("@type")
        if etype == "updateMessageSendSucceeded" and _is_ours(event, pending_message_id, chat_id):
            return {"status": "sent", "message": event.get("message") or {}}
        if etype == "updateMessageSendFailed" and _is_ours(event, pending_message_id, chat_id):
            return {"status": "failed", "error": event.get("error")}
        if etype == "updateFile" and file_id is not None:
            file = event.get("file") or {}
            so_far = (file.get("remote") or {}).get("uploaded_size") or 0
            if file.get("id") == file_id and so_far > uploaded:
                uploaded = so_far
                deadline = time.monotonic() + timeout
    return {"status": "pending", "message_id": pending_message_id}
