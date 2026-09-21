"""Chat operations: list, resolve, info, create, join/leave, archive, mute, pin, read."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

from tdelegram import normalize
from tdelegram.client import TelegramClient
from tdelegram.paging import paginate


def _chat_list_object(scope: str) -> dict[str, str]:
    if scope == "archive":
        return {"@type": "chatListArchive"}
    return {"@type": "chatListMain"}


SELF_REFS = frozenset({"me", "self", "saved"})


def resolve(client: TelegramClient, chat_ref: str) -> dict[str, Any]:
    """Resolve @handle, a bare handle, a numeric id, or `me` to a chat object.

    `me` is the usual way to name Saved Messages, which is simply the private
    chat with yourself. Without it the reference goes to searchPublicChat and
    comes back USERNAME_INVALID, which is a confusing answer to a reasonable
    request.
    """
    value = chat_ref.strip()
    if value.startswith("@"):
        value = value[1:]
    if not value:
        raise ValueError("A Telegram chat username or numeric chat ID is required.")
    if value.casefold() in SELF_REFS:
        own_id = client.call("getMe", {}).get("id")
        if not isinstance(own_id, int):
            raise ValueError("Could not determine the current account's user id.")
        return client.call("getChat", {"chat_id": own_id})
    import re

    if re.fullmatch(r"-?\d+", value):
        return client.call("getChat", {"chat_id": int(value)})
    return client.call("searchPublicChat", {"username": value})


def resolve_id(client: TelegramClient, chat_ref: str) -> int:
    chat = resolve(client, chat_ref)
    cid = chat.get("id")
    if not isinstance(cid, int):
        raise ValueError(f"Could not resolve chat {chat_ref!r}.")
    return cid


def info(client: TelegramClient, chat_ref: str, *, include_raw: bool = False) -> dict[str, Any]:
    chat = resolve(client, chat_ref)
    return normalize.chat_record(chat, include_raw=include_raw)


def iter_list(
    client: TelegramClient, *, scope: str = "main", maximum: int | None = None
) -> Iterator[dict[str, Any]]:
    scopes = ("main", "archive") if scope == "all" else (scope,)
    seen: set[int] = set()
    yielded = 0
    request_limit = 1000 if maximum is None else max(1, min(int(maximum), 1000))
    for current in scopes:
        result = client.call(
            "getChats", {"chat_list": _chat_list_object(current), "limit": request_limit}
        )
        for chat_id in result.get("chat_ids", []):
            if not isinstance(chat_id, int) or chat_id in seen:
                continue
            seen.add(chat_id)
            chat = client.call("getChat", {"chat_id": chat_id})
            record = normalize.chat_record(chat)
            record["chat_list"] = current
            yield record
            yielded += 1
            if maximum is not None and yielded >= maximum:
                return


def list_all(
    client: TelegramClient, *, scope: str = "main", maximum: int | None = None
) -> list[dict[str, Any]]:
    return list(iter_list(client, scope=scope, maximum=maximum))


def create_supergroup(
    client: TelegramClient, title: str, *, description: str = "", allow_write: bool = False
) -> dict[str, Any]:
    return client.call(
        "createNewSupergroupChat",
        {"title": title, "description": description},
        allow_write=allow_write,
    )


def create_basic_group(
    client: TelegramClient, user_ids: list[int], title: str, *, allow_write: bool = False
) -> dict[str, Any]:
    return client.call(
        "createNewBasicGroupChat",
        {"user_ids": user_ids, "title": title},
        allow_write=allow_write,
    )


def join(client: TelegramClient, chat_ref: str, *, allow_write: bool = False) -> dict[str, Any]:
    return client.call(
        "joinChat", {"chat_id": resolve_id(client, chat_ref)}, allow_write=allow_write
    )


def join_by_invite(
    client: TelegramClient, invite_link: str, *, allow_write: bool = False
) -> dict[str, Any]:
    return client.call(
        "joinChatByInviteLink", {"invite_link": invite_link}, allow_write=allow_write
    )


def leave(
    client: TelegramClient,
    chat_ref: str,
    *,
    allow_write: bool = False,
    allow_destructive: bool = False,
) -> dict[str, Any]:
    """Leave a chat. A private chat cannot be rejoined without a new invite."""
    return client.call(
        "leaveChat",
        {"chat_id": resolve_id(client, chat_ref)},
        allow_write=allow_write,
        allow_destructive=allow_destructive,
    )


def archive(
    client: TelegramClient, chat_ref: str, *, archived: bool = True, allow_write: bool = False
) -> dict[str, Any]:
    return client.call(
        "addChatToList",
        {
            "chat_id": resolve_id(client, chat_ref),
            "chat_list": _chat_list_object("archive" if archived else "main"),
        },
        allow_write=allow_write,
    )


def set_pinned(
    client: TelegramClient, chat_ref: str, *, pinned: bool = True, allow_write: bool = False
) -> dict[str, Any]:
    return client.call(
        "toggleChatIsPinned",
        {
            "chat_list": _chat_list_object("main"),
            "chat_id": resolve_id(client, chat_ref),
            "is_pinned": pinned,
        },
        allow_write=allow_write,
    )


def mark_read(
    client: TelegramClient, chat_ref: str, *, allow_write: bool = False
) -> dict[str, Any]:
    # viewMessages/openChat are classified read (local/read-receipt only).
    chat_id = resolve_id(client, chat_ref)
    client.call("openChat", {"chat_id": chat_id}, allow_write=allow_write)
    history = client.call(
        "getChatHistory",
        {"chat_id": chat_id, "from_message_id": 0, "offset": 0, "limit": 1, "only_local": True},
        allow_write=allow_write,
    )
    messages = history.get("messages", [])
    if messages and isinstance(messages[0].get("id"), int):
        client.call(
            "viewMessages",
            {"chat_id": chat_id, "message_ids": [messages[0]["id"]], "force_read": True},
            allow_write=allow_write,
        )
    return {"@type": "ok", "chat_id": chat_id}


def set_notification_mute(
    client: TelegramClient, chat_ref: str, *, muted: bool = True, allow_write: bool = False
) -> dict[str, Any]:
    mute_for = 365 * 86400 if muted else 0
    return client.call(
        "setChatNotificationSettings",
        {
            "chat_id": resolve_id(client, chat_ref),
            "notification_settings": {
                "@type": "chatNotificationSettings",
                "mute_for": mute_for,
            },
        },
        allow_write=allow_write,
    )


def invite_link(
    client: TelegramClient, chat_ref: str, *, allow_write: bool = False
) -> dict[str, Any]:
    return client.call(
        "createChatInviteLink",
        {"chat_id": resolve_id(client, chat_ref)},
        allow_write=allow_write,
    )


def members(
    client: TelegramClient, chat_ref: str, *, limit: int = 100, offset: int = 0
) -> dict[str, Any]:
    return client.call(
        "getSupergroupMembers",
        {
            "supergroup_id": resolve_id(client, chat_ref),
            "filter": {"@type": "supergroupMembersFilterRecent"},
            "offset": offset,
            "limit": limit,
        },
    )


def iter_all(client: TelegramClient, *, scope: str = "main") -> Iterator[dict[str, Any]]:
    yield from iter_list(client, scope=scope, maximum=None)


def paginated(client: TelegramClient, *, scope: str = "main") -> Any:
    def _fetch(cursor: int) -> tuple[list[dict[str, Any]], int | None]:
        items = list(iter_list(client, scope=scope, maximum=100))
        return items, None

    return paginate(_fetch)
