"""Secret chats: creation, closing, in-chat search."""

from __future__ import annotations

from typing import Any

from tdelegram.api.chats import resolve_id
from tdelegram.client import TelegramClient


def create_secret(
    client: TelegramClient, user_id: int, *, allow_write: bool = False
) -> dict[str, Any]:
    return client.call("createNewSecretChat", {"user_id": user_id}, allow_write=allow_write)


def close_secret(
    client: TelegramClient,
    secret_chat_id: int,
    *,
    allow_write: bool = False,
    allow_destructive: bool = False,
) -> dict[str, Any]:
    return client.call(
        "closeSecretChat",
        {"secret_chat_id": secret_chat_id},
        allow_write=allow_write,
        allow_destructive=allow_destructive,
    )


def search_secret(
    client: TelegramClient, chat_ref: str, query: str, *, limit: int = 20
) -> dict[str, Any]:
    return client.call(
        "searchSecretMessages",
        {
            "chat_id": resolve_id(client, chat_ref),
            "query": query,
            "offset": "",
            "limit": limit,
        },
    )


# -- proxies ----------------------------------------------------------
