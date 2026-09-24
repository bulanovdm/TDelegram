"""TDLib objects -> flat records. include_raw is the pressure valve."""

from __future__ import annotations

import re
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


# Keys that carry text inside TDLib's nested rich-text/page-block structures.
_RICH_KEYS = ("text", "texts", "blocks", "caption", "page_blocks")


def _harvest_text(node: Any, depth: int = 0) -> list[str]:
    """Collect plain strings out of a nested RichText / PageBlock tree."""
    if depth > 16:
        return []
    if isinstance(node, str):
        return [node] if node.strip() else []
    if isinstance(node, list):
        found: list[str] = []
        for item in node:
            found.extend(_harvest_text(item, depth + 1))
        return found
    if isinstance(node, dict):
        found = []
        for key in _RICH_KEYS:
            if key in node:
                found.extend(_harvest_text(node[key], depth + 1))
        return found
    return []


def rich_message_text(content: dict[str, Any]) -> str:
    """Flatten a messageRichMessage (instant-view style post) to plain text."""
    message = content.get("message")
    if not isinstance(message, dict):
        return ""
    return " ".join(_harvest_text(message.get("blocks"))).strip()


def message_text(message: dict[str, Any]) -> str:
    """Best-effort plain text for any message content.

    Reading only `messageText` and captions drops whole posts silently: a
    `messageRichMessage` keeps its words in nested page blocks, and gift,
    premium-code and poll-option contents carry their own `text`. A sweep
    filtering on text then misses them with no sign anything was skipped.
    """
    content = message.get("content") or {}
    if content.get("@type") == "messageRichMessage":
        return rich_message_text(content)
    return formatted_text(content.get("text")) or formatted_text(content.get("caption"))


def message_entities(message: dict[str, Any]) -> list[dict[str, Any]]:
    content = message.get("content") or {}
    return entities_of(content.get("text")) or entities_of(content.get("caption"))


def message_links(message: dict[str, Any]) -> list[str]:
    content = message.get("content") or {}
    return links_of(content.get("text")) or links_of(content.get("caption"))


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


# Where each kind of media keeps its file: content[holder][file_field].
_MEDIA = {
    "messageVideo": ("video", "video", "video"),
    "messageDocument": ("document", "document", "document"),
    "messageAudio": ("audio", "audio", "audio"),
    "messageAnimation": ("animation", "animation", "animation"),
    "messageVoiceNote": ("voice_note", "voice", "voice_note"),
    "messageVideoNote": ("video_note", "video", "video_note"),
    "messageSticker": ("sticker", "sticker", "sticker"),
}
_MEDIA_DETAILS = ("file_name", "mime_type", "duration", "width", "height", "title", "performer")


def media_of(message: dict[str, Any]) -> dict[str, Any] | None:
    """The file a message carries, if any: kind, file id, name, type, size.

    `file_id` is what `media download` takes. Records used to carry only a
    document's file name, so the documented way to fetch a message's file had
    no id to pass -- and a voice note's file, which lives under `voice` rather
    than a key named for its kind, was not found at all.
    """
    content = message.get("content") or {}
    kind = content.get("@type")
    if kind == "messagePhoto":
        sizes = [s for s in (content.get("photo") or {}).get("sizes") or [] if isinstance(s, dict)]
        if not sizes:
            return None
        largest = max(sizes, key=lambda s: int(s.get("width") or 0) * int(s.get("height") or 0))
        found: dict[str, Any] = {"kind": "photo", **_file_fields(largest.get("photo"))}
        found.update(width=largest.get("width"), height=largest.get("height"))
        return _compact(found)
    if kind not in _MEDIA:
        return None
    holder_key, file_key, label = _MEDIA[kind]
    holder = content.get(holder_key) or {}
    found = {"kind": label, **_file_fields(holder.get(file_key))}
    found.update({key: holder.get(key) for key in _MEDIA_DETAILS if holder.get(key)})
    if kind == "messageSticker":
        found["emoji"] = holder.get("emoji")
    transcript = speech_text(holder.get("speech_recognition_result"))
    if transcript is not None:
        found["transcript"] = transcript
    return _compact(found)


def _file_fields(file: Any) -> dict[str, Any]:
    if not isinstance(file, dict):
        return {}
    return {"file_id": file.get("id"), "size": file.get("size") or file.get("expected_size")}


def _compact(fields: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in fields.items() if value not in (None, "")}


def speech_text(result: Any) -> str | None:
    """A finished voice or video note transcription, which Telegram keeps on the
    message once anyone has asked for it."""
    if isinstance(result, dict) and result.get("@type") == "speechRecognitionResultText":
        return str(result.get("text", ""))
    return None


def reactions_of(message: dict[str, Any]) -> list[dict[str, Any]]:
    """[{"reaction": "👍", "count": 12}]: emoji as themselves, custom emoji as
    `custom:<id>`, paid star reactions as `paid`."""
    info = (message.get("interaction_info") or {}).get("reactions") or {}
    found = []
    for reaction in info.get("reactions") or []:
        kind = reaction.get("type") or {}
        if kind.get("@type") == "reactionTypeEmoji":
            label = str(kind.get("emoji", ""))
        elif kind.get("@type") == "reactionTypeCustomEmoji":
            label = f"custom:{kind.get('custom_emoji_id')}"
        else:
            label = "paid"
        found.append({"reaction": label, "count": reaction.get("total_count")})
    return found


def forward_origin_of(message: dict[str, Any]) -> dict[str, Any] | None:
    """Where a forwarded message first came from, and when it was first sent."""
    info = message.get("forward_info") or {}
    origin = info.get("origin") or {}
    kind = origin.get("@type")
    if kind == "messageOriginUser":
        found: dict[str, Any] = {"type": "user", "user_id": origin.get("sender_user_id")}
    elif kind == "messageOriginHiddenUser":
        found = {"type": "hidden_user", "name": origin.get("sender_name")}
    elif kind == "messageOriginChat":
        found = {"type": "chat", "chat_id": origin.get("sender_chat_id")}
    elif kind == "messageOriginChannel":
        found = {
            "type": "channel",
            "chat_id": origin.get("chat_id"),
            "message_id": origin.get("message_id"),
        }
    else:
        return None
    if origin.get("author_signature"):
        found["author_signature"] = origin["author_signature"]
    found["date"] = iso_date(info.get("date"))
    return found


def buttons_of(message: dict[str, Any]) -> list[dict[str, Any]]:
    """A message's inline keyboard, flattened: [{"text", "type", "url"?}]."""
    markup = message.get("reply_markup") or {}
    if markup.get("@type") != "replyMarkupInlineKeyboard":
        return []
    found = []
    for row in markup.get("rows") or []:
        for button in row or []:
            kind = (button.get("type") or {}).get("@type", "")
            entry = {"text": button.get("text"), "type": _button_kind(kind)}
            url = (button.get("type") or {}).get("url")
            if url:
                entry["url"] = url
            found.append(entry)
    return found


def _button_kind(tdlib_type: str) -> str:
    name = tdlib_type.removeprefix("inlineKeyboardButtonType")
    return re.sub(r"(?<!^)(?=[A-Z])", "_", name).lower() if name else "unknown"


def message_record(message: dict[str, Any], *, include_raw: bool = False) -> dict[str, Any]:
    content = message.get("content") or {}
    media = media_of(message)
    interaction = message.get("interaction_info") or {}
    album = str(message.get("media_album_id") or "0")
    record: dict[str, Any] = {
        "chat_id": message.get("chat_id"),
        "message_id": message.get("id"),
        "date": iso_date(message.get("date")),
        "edit_date": iso_date(message.get("edit_date")),
        "sender_id": sender_of(message),
        "topic_id": topic_id_of(message),
        "content_type": content.get("@type"),
        "text": message_text(message),
        "entities": message_entities(message),
        "links": message_links(message),
        "media": media,
        "file_name": (media or {}).get("file_name"),
        "is_channel_post": message.get("is_channel_post"),
        "is_outgoing": message.get("is_outgoing"),
        "reply_to": (message.get("reply_to") or {}).get("message_id"),
        "forwarded_from": forward_origin_of(message),
        "album_id": None if album == "0" else album,
        "author_signature": message.get("author_signature") or None,
        "views": interaction.get("view_count"),
        "forwards": interaction.get("forward_count"),
        "replies": (interaction.get("reply_info") or {}).get("reply_count"),
        "reactions": reactions_of(message),
        "buttons": buttons_of(message),
    }
    if include_raw:
        record["raw"] = message
    return record


def chat_record(
    chat: dict[str, Any],
    *,
    detail: dict[str, Any] | None = None,
    include_raw: bool = False,
) -> dict[str, Any]:
    """A flat chat record. `detail` is the chat's user, supergroup or basic group.

    TDLib keeps usernames, member counts and the forum flag on those objects,
    not on the chat, so without `detail` they are unknown -- the chat-only
    version of this read them from fields the chat does not have and reported
    null for every chat.
    """
    chat_type = chat.get("type") or {}
    kind = chat_type.get("@type")
    detail = detail or {}
    active = (detail.get("usernames") or {}).get("active_usernames") or []
    # A supergroup reports 0 members when it does not know how many it has.
    members = detail.get("member_count")
    record: dict[str, Any] = {
        "chat_id": chat.get("id"),
        "title": chat.get("title"),
        "username": active[0] if active else None,
        "usernames": list(active),
        "type": kind,
        "is_channel": chat_type.get("is_channel"),
        "is_forum": detail.get("is_forum") if kind == "chatTypeSupergroup" else False,
        "member_count": members or None,
        "unread_count": chat.get("unread_count"),
        "unread_mention_count": chat.get("unread_mention_count"),
        "last_read_inbox_message_id": chat.get("last_read_inbox_message_id"),
        "has_protected_content": chat.get("has_protected_content"),
        "last_message": message_record(chat["last_message"])
        if isinstance(chat.get("last_message"), dict)
        else None,
        "permissions": chat.get("permissions"),
    }
    if include_raw:
        record["raw"] = chat
    return record


def display_name(user: dict[str, Any]) -> str | None:
    """How Telegram would show a user: their name, else @username."""
    if (user.get("type") or {}).get("@type") == "userTypeDeleted":
        return "Deleted Account"
    name = " ".join(part for part in (user.get("first_name"), user.get("last_name")) if part)
    if name.strip():
        return name.strip()
    active = (user.get("usernames") or {}).get("active_usernames") or []
    return f"@{active[0]}" if active else None


def user_record(user: dict[str, Any], *, include_raw: bool = False) -> dict[str, Any]:
    record: dict[str, Any] = {
        "user_id": user.get("id"),
        "first_name": user.get("first_name"),
        "last_name": user.get("last_name"),
        # A user whose usernames are all disabled or collectible has an empty
        # active list, which indexing [0] turned into a crash mid-listing.
        "username": ((user.get("usernames") or {}).get("active_usernames") or [None])[0]
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
        "size": obj.get("size") or obj.get("expected_size"),
        "path": local.get("path") or None,
        "completed": bool(local.get("is_downloading_completed")),
        "is_downloading": local.get("is_downloading_active"),
        "downloaded_size": local.get("downloaded_size"),
        "remote_id": remote.get("id"),
    }
