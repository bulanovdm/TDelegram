"""Forum topics and threads, including the careful offset-triple pagination."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

from tdelegram.client import TelegramClient


def topic_object(topic_id: int | None, topic_kind: str = "forum") -> dict[str, Any] | None:
    if topic_id is None:
        return None
    if topic_kind == "thread":
        return {"@type": "messageTopicThread", "message_thread_id": topic_id}
    if topic_kind == "direct":
        return {"@type": "messageTopicDirectMessages", "direct_messages_chat_topic_id": topic_id}
    return {"@type": "messageTopicForum", "forum_topic_id": topic_id}


def list_topics(
    client: TelegramClient,
    chat_ref: str,
    *,
    limit: int = 100,
    offset_date: int = 0,
    offset_message_id: int = 0,
    offset_topic_id: int = 0,
) -> dict[str, Any]:
    from tdelegram.api.chats import resolve_id

    return client.call(
        "getForumTopics",
        {
            "chat_id": resolve_id(client, chat_ref),
            "query": "",
            "offset_date": offset_date,
            "offset_message_id": offset_message_id,
            "offset_topic_id": offset_topic_id,
            "limit": limit,
        },
    )


def iter_topics(client: TelegramClient, chat_ref: str) -> Iterator[dict[str, Any]]:
    from tdelegram.api.chats import resolve_id

    chat_id = resolve_id(client, chat_ref)
    seen: set[int] = set()
    offset_date, offset_message_id, offset_topic_id = 0, 0, 0
    while True:
        result = client.call(
            "getForumTopics",
            {
                "chat_id": chat_id,
                "query": "",
                "offset_date": offset_date,
                "offset_message_id": offset_message_id,
                "offset_topic_id": offset_topic_id,
                "limit": 100,
            },
        )
        topics: list[dict[str, Any]] = result.get("topics", [])
        if not topics:
            return
        fresh = [t for t in topics if t.get("info", {}).get("topic_id", id(t)) not in seen]
        if not fresh:
            return
        for topic in fresh:
            tid = topic.get("info", {}).get("topic_id")
            if isinstance(tid, int):
                seen.add(tid)
            yield topic
        last = topics[-1]
        info = last.get("info") or {}
        new_triple = (
            int(last.get("last_message_date", 0) or 0),
            int((last.get("last_message") or {}).get("id", 0) or 0),
            int(info.get("topic_id", 0) or 0),
        )
        # Offset-triple didn't advance -> stop, or this loops forever.
        if new_triple == (offset_date, offset_message_id, offset_topic_id):
            return
        offset_date, offset_message_id, offset_topic_id = new_triple


def create_topic(
    client: TelegramClient, chat_ref: str, name: str, *, allow_write: bool = False
) -> dict[str, Any]:
    from tdelegram.api.chats import resolve_id

    return client.call(
        "createForumTopic",
        {"chat_id": resolve_id(client, chat_ref), "name": name},
        allow_write=allow_write,
    )


def edit_topic(
    client: TelegramClient, chat_ref: str, topic_id: int, name: str, *, allow_write: bool = False
) -> dict[str, Any]:
    from tdelegram.api.chats import resolve_id

    return client.call(
        "editForumTopic",
        {"chat_id": resolve_id(client, chat_ref), "topic_id": topic_id, "name": name},
        allow_write=allow_write,
    )


def close_topic(
    client: TelegramClient,
    chat_ref: str,
    topic_id: int,
    *,
    closed: bool = True,
    allow_write: bool = False,
) -> dict[str, Any]:
    from tdelegram.api.chats import resolve_id

    return client.call(
        "toggleForumTopicIsClosed",
        {"chat_id": resolve_id(client, chat_ref), "topic_id": topic_id, "is_closed": closed},
        allow_write=allow_write,
    )


def delete_topic(
    client: TelegramClient,
    chat_ref: str,
    topic_id: int,
    *,
    allow_write: bool = False,
    allow_destructive: bool = False,
) -> dict[str, Any]:
    from tdelegram.api.chats import resolve_id

    return client.call(
        "deleteForumTopic",
        {"chat_id": resolve_id(client, chat_ref), "topic_id": topic_id},
        allow_write=allow_write,
        allow_destructive=allow_destructive,
    )
