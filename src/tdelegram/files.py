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
    """Download via downloadFile(synchronous=true). Returns the final file object."""
    result = client.call(
        "downloadFile",
        {"file_id": file_id, "priority": 1, "offset": 0, "limit": 0, "synchronous": True},
        timeout=timeout,
        allow_write=True,
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
) -> dict[str, Any]:
    """Upload a file as a document. Returns the pending message (delivery unconfirmed)."""
    local_path = str(Path(path).expanduser())
    content = {
        "@type": "inputMessageDocument",
        "document": {"@type": "inputFileLocal", "path": local_path},
        "caption": {"@type": "formattedText", "text": caption, "entities": []},
    }
    return client.call(
        "sendMessage",
        {"chat_id": chat_id, "input_message_content": content},
        timeout=timeout,
        allow_write=True,
    )


def wait_for_send(
    client: TelegramClient,
    pending_message_id: int,
    chat_id: int,
    *,
    timeout: float = 60.0,
) -> dict[str, Any]:
    """Block until updateMessageSendSucceeded/Failed for a pending send."""
    import time as _time

    deadline = _time.monotonic() + timeout
    while _time.monotonic() < deadline:
        event = client.next_update(timeout=1.0)
        if event is None:
            continue
        etype = event.get("@type")
        if etype == "updateMessageSendSucceeded":
            message = event.get("message") or {}
            old = event.get("old_message_id")
            if old == pending_message_id or message.get("chat_id") == chat_id:
                return {"status": "sent", "message": message}
        if etype == "updateMessageSendFailed":
            old = event.get("old_message_id")
            if old == pending_message_id:
                return {"status": "failed", "error": event.get("error")}
    return {"status": "pending", "message_id": pending_message_id}
