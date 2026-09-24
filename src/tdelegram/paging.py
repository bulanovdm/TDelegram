"""Pagination discipline for TDLib's cursor-based history endpoints.

Per-page dedup sets, "no fresh items -> stop", stop_after_page so an
out-of-window item ends the walk after finishing its page, and
cursor-didn't-advance guards. Generator streaming end to end keeps
`--all` constant-memory.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from typing import Any

from tdelegram.client import TelegramClient

PageFn = Callable[[int], tuple[list[dict[str, Any]], int | None]]
"""Fetch one page from a cursor. Returns (items, next_cursor)."""


def paginate(
    fetch: PageFn,
    *,
    maximum: int | None = None,
    stop_when: Callable[[dict[str, Any]], bool] | None = None,
    start: int = 0,
) -> Iterator[dict[str, Any]]:
    """Walk pages from `start` (0: the newest end) until the cursor gives out."""
    seen: set[Any] = set()
    yielded = 0
    cursor = start
    while True:
        items, next_cursor = fetch(cursor)
        if not items:
            return
        fresh = [item for item in items if _identity(item) not in seen]
        if not fresh:
            return
        stop_after_page = False
        for item in fresh:
            seen.add(_identity(item))
            if stop_when is not None and stop_when(item):
                stop_after_page = True
                continue
            yield item
            yielded += 1
            if maximum is not None and yielded >= maximum:
                return
        if stop_after_page:
            return
        if next_cursor is None or next_cursor == cursor or next_cursor == 0:
            return
        cursor = next_cursor


def _identity(item: dict[str, Any]) -> Any:
    return item.get("id", id(item))


def history_pages(
    client: TelegramClient,
    chat_id: int,
    *,
    limit: int = 100,
    topic_id: int | None = None,
    topic_kind: str = "forum",
) -> PageFn:
    from tdelegram.api.topics import topic_object

    def _fetch(cursor: int) -> tuple[list[dict[str, Any]], int | None]:
        result = client.call(
            "getChatHistory",
            {
                "chat_id": chat_id,
                "from_message_id": cursor,
                "offset": 0,
                "limit": limit,
                "only_local": False,
            },
        )
        messages = result.get("messages", [])
        if not messages:
            return [], None
        last = messages[-1].get("id")
        nxt = last if isinstance(last, int) and last != cursor else None
        _ = topic_object(topic_id, topic_kind)
        return messages, nxt

    return _fetch


def search_pages(
    client: TelegramClient,
    chat_id: int,
    query: str,
    *,
    limit: int = 100,
    sender_id: int | None = None,
    topic_id: int | None = None,
    topic_kind: str = "forum",
) -> PageFn:
    from tdelegram.api.topics import topic_object

    def _fetch(cursor: int) -> tuple[list[dict[str, Any]], int | None]:
        sender = {"@type": "messageSenderUser", "user_id": sender_id} if sender_id else None
        result = client.call(
            "searchChatMessages",
            {
                "chat_id": chat_id,
                "topic_id": topic_object(topic_id, topic_kind),
                "query": query,
                "sender_id": sender,
                "from_message_id": cursor,
                "offset": 0,
                "limit": limit,
                "filter": None,
            },
        )
        messages = result.get("messages", [])
        if not messages:
            return [], None
        nxt = result.get("next_from_message_id")
        if not isinstance(nxt, int) or nxt == 0 or nxt == cursor:
            last = messages[-1].get("id")
            nxt = last if isinstance(last, int) and last != cursor else None
        return messages, nxt

    return _fetch
