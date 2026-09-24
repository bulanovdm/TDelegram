"""Users: lookup, own account, full info, user search."""

from __future__ import annotations

from typing import Any

from tdelegram import normalize
from tdelegram.client import TelegramClient
from tdelegram.errors import TelegramError


class SenderNames:
    """Display names for message senders, looked up once each per walk.

    A record's `sender_id` alone leaves every reader -- a person, an agent, a
    screen reader -- to resolve ids by hand. TDLib already holds the sender of
    every message it has loaded, so each lookup is local.
    """

    def __init__(self, client: TelegramClient) -> None:
        self._client = client
        self._cache: dict[tuple[Any, Any], str | None] = {}

    def __call__(self, sender: dict[str, Any] | None) -> str | None:
        if not sender:
            return None
        kind = sender.get("@type")
        key = (kind, sender.get("user_id", sender.get("chat_id")))
        if key not in self._cache:
            self._cache[key] = self._lookup(sender)
        return self._cache[key]

    def _lookup(self, sender: dict[str, Any]) -> str | None:
        try:
            if sender.get("@type") == "messageSenderUser":
                user = self._client.call("getUser", {"user_id": sender.get("user_id")})
                return normalize.display_name(user)
            if sender.get("@type") == "messageSenderChat":
                chat = self._client.call("getChat", {"chat_id": sender.get("chat_id")})
                return str(chat["title"]) if chat.get("title") else None
        except TelegramError:
            return None
        return None

    def label(self, record: dict[str, Any]) -> dict[str, Any]:
        """Add `sender_name` to a message record, in place, and return it."""
        record["sender_name"] = self(record.get("sender_id"))
        return record


def get_user(client: TelegramClient, user_id: int) -> dict[str, Any]:
    return normalize.user_record(client.call("getUser", {"user_id": user_id}))


def me(client: TelegramClient) -> dict[str, Any]:
    return normalize.user_record(client.call("getMe", {}))


def full_info(client: TelegramClient, user_id: int) -> dict[str, Any]:
    return client.call("getUserFullInfo", {"user_id": user_id})


def search_users(client: TelegramClient, query: str, *, limit: int = 20) -> dict[str, Any]:
    return client.call("searchContacts", {"query": query, "limit": limit})


# -- contacts ---------------------------------------------------------
