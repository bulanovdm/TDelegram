"""File upload/download helpers with honest delivery semantics."""

from __future__ import annotations

import queue
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from tdelegram.client import TelegramClient
from tdelegram.errors import TelegramError, TelegramTimeoutError, from_td_error
from tdelegram.normalize import media_of, sending_state_of


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
    timeout: float | None = None,
    allow_write: bool = False,
) -> dict[str, Any]:
    """Upload a file as a document. Returns the pending message (delivery unconfirmed).

    `timeout` bounds TDLib's answer to sendMessage, which comes at once while the
    upload carries on in the background; left out, it is the client's own. It is
    not how long the upload may take, and a short one is a risk of its own: a
    write that times out may have been made, and a retry would send it twice.

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


class SendWatch:
    """The updates that say how sends end, collected from before a send is made.

    The client's own update queue has one reader at a time: two uploads waiting
    on it take each other's updates, and one reports "pending" for a message
    that was sent. A watch belongs to its caller alone. It is opened before
    sendMessage so that an update arriving ahead of the answer is not missed.
    """

    KINDS = frozenset({"updateMessageSendSucceeded", "updateMessageSendFailed", "updateFile"})

    def __init__(self, client: TelegramClient) -> None:
        self._events: queue.Queue[dict[str, Any]] = queue.Queue()
        self._subscription = client.on_update(self._offer)

    def _offer(self, event: dict[str, Any]) -> None:
        if event.get("@type") in self.KINDS:
            self._events.put(event)

    def next(self, timeout: float) -> dict[str, Any] | None:
        try:
            return self._events.get(timeout=timeout)
        except queue.Empty:
            return None

    def close(self) -> None:
        self._subscription.unsubscribe()

    def __enter__(self) -> SendWatch:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()


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
    watch: SendWatch | None = None,
) -> dict[str, Any]:
    """Block until updateMessageSendSucceeded/Failed for a pending send.

    `timeout` is how long to go without news, not a cap on the whole send: a
    large file on a slow link is still being uploaded, and an `updateFile` for
    `file_id` showing more of it gone up starts the clock again.

    Without a `watch` this reads the client's shared update queue, which only
    one waiter at a time can do. Callers that may overlap open a `SendWatch`
    before sending and pass it in.
    """
    next_event = watch.next if watch is not None else client.next_update
    deadline = time.monotonic() + timeout
    uploaded = 0
    while (remaining := deadline - time.monotonic()) > 0:
        event = next_event(min(1.0, remaining))
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


def send_and_wait(
    client: TelegramClient,
    chat_id: int,
    send: Callable[[], dict[str, Any]],
    *,
    timeout: float,
) -> dict[str, Any]:
    """Make a send and wait for how it ends: `status` sent, failed or pending.

    TDLib answers a send at once with a pending message and delivers it in the
    background, so a caller that returns on the answer, and closes the client,
    abandons the send. `send` is the sendMessage call; the watch is open before
    it is made. For `timeout`, see wait_for_send.
    """
    with SendWatch(client) as watch:
        pending = send()
        message = pending.get("message") or pending
        return wait_for_send(
            client,
            int(message.get("id") or 0),
            chat_id,
            timeout=timeout,
            file_id=uploaded_file_id(pending),
            watch=watch,
        )


def confirmed_message(outcome: dict[str, Any], *, timeout: float) -> dict[str, Any]:
    """The sent message, or the error that says why there is none."""
    if outcome["status"] == "sent":
        message: dict[str, Any] = outcome["message"]
        return message
    if outcome["status"] == "failed":
        raise from_td_error(outcome.get("error") or {}, "sendMessage")
    raise TelegramTimeoutError(
        f"Telegram did not confirm the message within {timeout:g}s. It is still pending, "
        "and is abandoned if the client closes now. Wait longer with a larger timeout."
    )


def unsent_in_chat(client: TelegramClient, chat_id: int) -> list[dict[str, Any]]:
    """Outgoing messages in a chat that were never delivered, newest first.

    TDLib keeps a send in its log until it is delivered and starts it again the
    next time it runs, if it is under a day old. A client that exited before an
    upload finished therefore leaves one behind, and sending the file again
    makes two. Reads the local history only. A courtesy check, so a failure to
    read it is no news.
    """
    try:
        history = client.call(
            "getChatHistory",
            {
                "chat_id": chat_id,
                "from_message_id": 0,
                "offset": 0,
                "limit": 50,
                "only_local": True,
            },
        )
    except TelegramError:
        return []
    stuck = []
    for message in history.get("messages") or []:
        state = sending_state_of(message)
        if message.get("is_outgoing") and state is not None:
            stuck.append(
                {
                    "message_id": message.get("id"),
                    "file_name": (media_of(message) or {}).get("file_name"),
                    "sending_state": state,
                }
            )
    return stuck
