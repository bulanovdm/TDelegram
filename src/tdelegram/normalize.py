"""TDLib objects -> flat records. include_raw is the pressure valve."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any


def iso_date(timestamp: Any) -> str | None:
    if isinstance(timestamp, int) and timestamp:
        try:
            return datetime.fromtimestamp(timestamp, tz=timezone.utc).isoformat()
        except (OverflowError, OSError, ValueError):
            return None
    return None


def formatted_text(value: dict[str, Any] | None) -> str:
    if not value:
        return ""
    return str(value.get("text", ""))


def entities_of(value: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not value:
        return []
    entities = value.get("entities") or []
    return [e for e in entities if isinstance(e, dict)]


def links_of(value: dict[str, Any] | None) -> list[str]:
    links: list[str] = []
    for entity in entities_of(value):
        etype = entity.get("type") or {}
        if etype.get("@type") == "textEntityTypeTextUrl":
            url = etype.get("url")
            if url:
                links.append(str(url))
        elif etype.get("@type") == "textEntityTypeUrl":
            offset = entity.get("offset", 0)
            length = entity.get("length", 0)
            text = formatted_text(value)
            try:
                link = text[int(offset) : int(offset) + int(length)]
            except (ValueError, TypeError, IndexError):
                continue
            if link:
                links.append(link)
    return links


def message_text(message: dict[str, Any]) -> str:
    content = message.get("content") or {}
    if content.get("@type") == "messageText":
        return formatted_text(content.get("text"))
    return formatted_text(content.get("caption"))


def message_entities(message: dict[str, Any]) -> list[dict[str, Any]]:
    content = message.get("content") or {}
    if content.get("@type") == "messageText":
        return entities_of(content.get("text"))
    return entities_of(content.get("caption"))


def message_links(message: dict[str, Any]) -> list[str]:
    content = message.get("content") or {}
    if content.get("@type") == "messageText":
        return links_of(content.get("text"))
    return links_of(content.get("caption"))


def topic_id_of(message: dict[str, Any]) -> int | None:
    topic = message.get("topic_id") or {}
    for key in (
        "forum_topic_id",
        "message_thread_id",
        "direct_messages_chat_topic_id",
        "saved_messages_topic_id",
    ):
        value = topic.get(key)
        if isinstance(value, int):
            return value
    return None


def sender_of(message: dict[str, Any]) -> dict[str, Any] | None:
    sender = message.get("sender_id")
    return sender if isinstance(sender, dict) else None


def message_record(message: dict[str, Any], *, include_raw: bool = False) -> dict[str, Any]:
    content = message.get("content") or {}
    document = content.get("document") or {}
    record: dict[str, Any] = {
        "chat_id": message.get("chat_id"),
        "message_id": message.get("id"),
        "date": iso_date(message.get("date")),
        "sender_id": sender_of(message),
        "topic_id": topic_id_of(message),
        "content_type": content.get("@type"),
        "text": message_text(message),
        "entities": message_entities(message),
        "links": message_links(message),
        "file_name": document.get("file_name"),
        "is_channel_post": message.get("is_channel_post"),
        "is_outgoing": message.get("is_outgoing"),
        "reply_to": (message.get("reply_to") or {}).get("message_id"),
    }
    if include_raw:
        record["raw"] = message
    return record


def chat_record(chat: dict[str, Any], *, include_raw: bool = False) -> dict[str, Any]:
    chat_type = chat.get("type") or {}
    usernames = chat.get("usernames") or {}
    active = usernames.get("active_usernames") or []
    record: dict[str, Any] = {
        "chat_id": chat.get("id"),
        "title": chat.get("title"),
        "username": chat.get("username") or (active[0] if active else None),
        "usernames": active,
        "type": chat_type.get("@type"),
        "is_channel": chat_type.get("is_channel"),
        "is_forum": chat.get("is_forum"),
        "member_count": chat.get("member_count"),
        "unread_count": chat.get("unread_count"),
        "last_message": message_record(chat["last_message"])
        if isinstance(chat.get("last_message"), dict)
        else None,
        "permissions": chat.get("permissions"),
    }
    if include_raw:
        record["raw"] = chat
    return record


def user_record(user: dict[str, Any], *, include_raw: bool = False) -> dict[str, Any]:
    record: dict[str, Any] = {
        "user_id": user.get("id"),
        "first_name": user.get("first_name"),
        "last_name": user.get("last_name"),
        "username": (user.get("usernames") or {}).get("active_usernames", [None])[0]
        if isinstance(user.get("usernames"), dict)
        else user.get("username"),
        "phone": user.get("phone_number"),
        "is_bot": user.get("type", {}).get("@type") == "userTypeBot"
        if isinstance(user.get("type"), dict)
        else False,
        "is_premium": user.get("is_premium"),
        "status": (user.get("status") or {}).get("@type")
        if isinstance(user.get("status"), dict)
        else None,
    }
    if include_raw:
        record["raw"] = user
    return record


def file_record(obj: dict[str, Any]) -> dict[str, Any]:
    local = obj.get("local") or {}
    remote = obj.get("remote") or {}
    return {
        "file_id": obj.get("id"),
        "size": obj.get("size"),
        "path": local.get("path"),
        "is_downloading": local.get("is_downloading_active"),
        "downloaded_size": local.get("downloaded_size"),
        "remote_id": remote.get("id"),
    }
