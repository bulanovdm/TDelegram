"""Users: lookup, own account, full info, user search."""

from __future__ import annotations

from typing import Any

from tdelegram import normalize
from tdelegram.client import TelegramClient


def get_user(client: TelegramClient, user_id: int) -> dict[str, Any]:
    return normalize.user_record(client.call("getUser", {"user_id": user_id}))


def me(client: TelegramClient) -> dict[str, Any]:
    return normalize.user_record(client.call("getMe", {}))


def full_info(client: TelegramClient, user_id: int) -> dict[str, Any]:
    return client.call("getUserFullInfo", {"user_id": user_id})


def search_users(client: TelegramClient, query: str, *, limit: int = 20) -> dict[str, Any]:
    return client.call("searchContacts", {"query": query, "limit": limit})


# -- contacts ---------------------------------------------------------
