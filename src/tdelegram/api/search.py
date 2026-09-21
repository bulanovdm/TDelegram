"""Search: in-chat, global, and public chat lookup."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

from tdelegram import normalize
from tdelegram.api.chats import resolve_id
from tdelegram.client import TelegramClient


def search_messages(
    client: TelegramClient,
    query: str,
    *,
    chat_ref: str | None = None,
    limit: int = 20,
    from_message_id: int = 0,
) -> dict[str, Any]:
    if chat_ref is not None:
        return client.call(
            "searchChatMessages",
            {
                "chat_id": resolve_id(client, chat_ref),
                "query": query,
                "from_message_id": from_message_id,
                "limit": limit,
            },
        )
    return client.call(
        "searchMessages",
        {"query": query, "offset": 0, "limit": limit, "from_message_id": from_message_id},
    )


def search_public_chats(client: TelegramClient, query: str) -> dict[str, Any]:
    return client.call("searchPublicChats", {"query": query})


def iter_global_search(
    client: TelegramClient, query: str, *, maximum: int | None = None
) -> Iterator[dict[str, Any]]:
    cursor = 0
    seen: set[int] = set()
    yielded = 0
    while True:
        result = client.call(
            "searchMessages", {"query": query, "offset": 0, "limit": 100, "from_message_id": cursor}
        )
        messages: list[dict[str, Any]] = result.get("messages", [])
        if not messages:
            return
        fresh = [m for m in messages if m.get("id") not in seen]
        if not fresh:
            return
        for message in fresh:
            mid = message.get("id")
            if isinstance(mid, int):
                seen.add(mid)
            yield normalize.message_record(message)
            yielded += 1
            if maximum is not None and yielded >= maximum:
                return
        nxt = result.get("next_from_message_id", 0)
        if not isinstance(nxt, int) or nxt in (0, cursor):
            return
        cursor = nxt
