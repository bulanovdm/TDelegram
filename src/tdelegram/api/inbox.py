"""Unread messages across chats: what the account has not read yet.

Read-only by construction. getChatHistory marks nothing read; viewMessages
does, and nothing here calls it -- nor openChat, which tells TDLib the chat is
on screen -- so an inbox can be triaged, summarized or drafted against without
a single sender seeing a read receipt.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

from tdelegram import normalize
from tdelegram.api.chats import chat_list_object
from tdelegram.api.users import SenderNames
from tdelegram.client import TelegramClient
from tdelegram.errors import TelegramError
from tdelegram.paging import history_pages, paginate


def notification_scope(chat: dict[str, Any]) -> str:
    """Which default notification settings a chat falls under."""
    chat_type = chat.get("type") or {}
    kind = chat_type.get("@type")
    if kind in ("chatTypePrivate", "chatTypeSecret"):
        return "notificationSettingsScopePrivateChats"
    if kind == "chatTypeSupergroup" and chat_type.get("is_channel"):
        return "notificationSettingsScopeChannelChats"
    return "notificationSettingsScopeGroupChats"


class MuteRules:
    """Whether a chat is muted, resolving "use the default" once per scope."""

    def __init__(self, client: TelegramClient) -> None:
        self._client = client
        self._defaults: dict[str, bool] = {}

    def is_muted(self, chat: dict[str, Any]) -> bool:
        settings = chat.get("notification_settings") or {}
        if settings and not settings.get("use_default_mute_for", True):
            return int(settings.get("mute_for") or 0) > 0
        scope = notification_scope(chat)
        if scope not in self._defaults:
            try:
                found = self._client.call(
                    "getScopeNotificationSettings", {"scope": {"@type": scope}}
                )
                self._defaults[scope] = int(found.get("mute_for") or 0) > 0
            except TelegramError:
                self._defaults[scope] = False
        return self._defaults[scope]


def unread_chats(
    client: TelegramClient,
    *,
    scope: str = "main",
    include_muted: bool = False,
    maximum: int | None = None,
) -> Iterator[dict[str, Any]]:
    """Raw chats with unread messages, in chat-list order.

    A muted chat is left out unless asked for -- but not when it mentions the
    account, because a mention is exactly what muting is not meant to hide.
    """
    scopes = ("main", "archive") if scope == "all" else (scope,)
    mutes = MuteRules(client)
    seen: set[int] = set()
    found = 0
    for current in scopes:
        listed = client.call("getChats", {"chat_list": chat_list_object(current), "limit": 1000})
        for chat_id in listed.get("chat_ids", []):
            if not isinstance(chat_id, int) or chat_id in seen:
                continue
            seen.add(chat_id)
            chat = client.call("getChat", {"chat_id": chat_id})
            if int(chat.get("unread_count") or 0) <= 0:
                continue
            mentioned = int(chat.get("unread_mention_count") or 0) > 0
            if not include_muted and not mentioned and mutes.is_muted(chat):
                continue
            yield chat
            found += 1
            if maximum is not None and found >= maximum:
                return


def iter_unread(
    client: TelegramClient,
    *,
    scope: str = "main",
    chats: int | None = 20,
    per_chat: int = 20,
    include_muted: bool = False,
) -> Iterator[dict[str, Any]]:
    """Unread incoming messages as records, chat by chat, oldest first within each.

    At most `per_chat` from each chat -- the newest ones, since those are the
    ones a reply would answer. Each record carries `chat_title` and the chat's
    `unread_count` so a digest can say how much it left out.
    """
    names = SenderNames(client)
    for chat in unread_chats(client, scope=scope, include_muted=include_muted, maximum=chats):
        chat_id = int(chat["id"])
        last_read = int(chat.get("last_read_inbox_message_id") or 0)
        wanted = min(per_chat, int(chat.get("unread_count") or 0))

        def _read_already(message: dict[str, Any], last_read: int = last_read) -> bool:
            mid = message.get("id")
            return isinstance(mid, int) and mid <= last_read

        newest_first: list[dict[str, Any]] = []
        for message in paginate(history_pages(client, chat_id), stop_when=_read_already):
            if message.get("is_outgoing"):
                continue
            newest_first.append(message)
            if len(newest_first) >= wanted:
                break
        for message in reversed(newest_first):
            record = names.label(normalize.message_record(message))
            record["chat_title"] = chat.get("title")
            record["chat_unread_count"] = chat.get("unread_count")
            yield record
