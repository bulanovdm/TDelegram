"""Search: in-chat, global, and public chat lookup."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

from tdelegram import normalize
from tdelegram.api.chats import resolve_id
from tdelegram.api.users import SenderNames
from tdelegram.client import TelegramClient
from tdelegram.dates import parse_date
from tdelegram.paging import paginate, search_pages


def search_messages(
    client: TelegramClient,
    query: str,
    *,
    chat_ref: str | None = None,
    limit: int = 20,
    from_message_id: int = 0,
) -> dict[str, Any]:
    """One page of results, in one chat or across all of them.

    The two searches page differently: in a chat by message id, globally by an
    opaque `next_offset` string. Global search used to send `offset: 0` and a
    `from_message_id` it does not have, and TDLib refuses a number where the
    schema says string, so it never returned anything.
    """
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
    return client.call("searchMessages", {"query": query, "offset": "", "limit": limit})


def search_public_chats(client: TelegramClient, query: str) -> dict[str, Any]:
    return client.call("searchPublicChats", {"query": query})


def iter_global_search(
    client: TelegramClient,
    query: str,
    *,
    maximum: int | None = None,
    since: str | int | None = None,
    until: str | int | None = None,
) -> Iterator[dict[str, Any]]:
    """Normalized results from every chat, newest first, within an optional window."""
    params: dict[str, Any] = {"query": query, "limit": 100}
    since_ts = since if isinstance(since, int) else parse_date(since)
    # A date alone means through the end of that day, not its first second.
    until_ts = until if isinstance(until, int) else parse_date(until, end_of_day=True)
    if since_ts is not None:
        params["min_date"] = since_ts
    if until_ts is not None:
        params["max_date"] = until_ts
    names = SenderNames(client)
    offset = ""
    seen: set[tuple[Any, Any]] = set()
    yielded = 0
    while True:
        result = client.call("searchMessages", {**params, "offset": offset})
        messages: list[dict[str, Any]] = result.get("messages", [])
        # Message ids are only unique per chat, so dedup on the pair.
        fresh = [m for m in messages if (m.get("chat_id"), m.get("id")) not in seen]
        if not fresh:
            return
        for message in fresh:
            seen.add((message.get("chat_id"), message.get("id")))
            yield names.label(normalize.message_record(message))
            yielded += 1
            if maximum is not None and yielded >= maximum:
                return
        nxt = result.get("next_offset")
        if not isinstance(nxt, str) or not nxt or nxt == offset:
            return
        offset = nxt


def iter_chat_search(
    client: TelegramClient,
    chat_ref: str,
    query: str,
    *,
    sender_id: int | None = None,
    topic_id: int | None = None,
    maximum: int | None = None,
) -> Iterator[dict[str, Any]]:
    """Server-side search inside one chat, as the same records `chat history` yields."""
    names = SenderNames(client)
    fetch = search_pages(
        client, resolve_id(client, chat_ref), query, sender_id=sender_id, topic_id=topic_id
    )
    for message in paginate(fetch, maximum=maximum):
        yield names.label(normalize.message_record(message))
