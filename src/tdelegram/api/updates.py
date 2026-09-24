"""Live update stream."""

from __future__ import annotations

import re
import time
from collections.abc import Iterator
from typing import Any

from tdelegram import normalize
from tdelegram.client import TelegramClient
from tdelegram.errors import TelegramError


def follow(client: TelegramClient, *, timeout: float = 3600.0) -> Iterator[dict[str, Any]]:
    import time

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        event = client.next_update(timeout=1.0)
        if event is None:
            continue
        yield event


def follow_filtered(
    client: TelegramClient, types: list[str], *, timeout: float = 3600.0
) -> Iterator[dict[str, Any]]:
    for event in follow(client, timeout=timeout):
        if not types or event.get("@type") in types:
            yield event


def watch_messages(
    client: TelegramClient,
    *,
    chat_ids: set[int] | None = None,
    terms: list[str] | None = None,
    pattern: str | None = None,
    sender_id: int | None = None,
    include_outgoing: bool = False,
    timeout: float | None = None,
    count: int | None = None,
) -> Iterator[dict[str, Any]]:
    """New messages as they arrive, as records, filtered.

    `terms` match when any one appears, case-insensitively -- an alert list is
    a set of alternatives -- and `pattern` is a regular expression, also
    case-insensitive. The account's own messages are left out unless asked
    for. Stops after `timeout` seconds or `count` matches, whichever is first.
    """
    from tdelegram.api.users import SenderNames

    regex = re.compile(pattern, re.IGNORECASE) if pattern else None
    folded = [term.casefold() for term in terms or []]
    names = SenderNames(client)
    titles: dict[int, str | None] = {}
    deadline = None if timeout is None else time.monotonic() + timeout
    matched = 0
    while deadline is None or time.monotonic() < deadline:
        event = client.next_update(timeout=0.5)
        if event is None or event.get("@type") != "updateNewMessage":
            continue
        message = event.get("message") or {}
        chat_id = message.get("chat_id")
        if message.get("is_outgoing") and not include_outgoing:
            continue
        if chat_ids and chat_id not in chat_ids:
            continue
        if sender_id is not None and (message.get("sender_id") or {}).get("user_id") != sender_id:
            continue
        text = normalize.message_text(message)
        if folded and not any(term in text.casefold() for term in folded):
            continue
        if regex is not None and not regex.search(text):
            continue
        record = names.label(normalize.message_record(message))
        if isinstance(chat_id, int) and chat_id not in titles:
            try:
                titles[chat_id] = client.call("getChat", {"chat_id": chat_id}).get("title")
            except TelegramError:
                titles[chat_id] = None
        record["chat_title"] = titles.get(chat_id) if isinstance(chat_id, int) else None
        yield record
        matched += 1
        if count is not None and matched >= count:
            return
