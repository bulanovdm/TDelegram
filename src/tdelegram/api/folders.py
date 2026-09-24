"""Chat folders."""

from __future__ import annotations

import time
from typing import Any

from tdelegram.client import TelegramClient
from tdelegram.errors import TelegramTimeoutError


def folder_record(info: dict[str, Any]) -> dict[str, Any]:
    name = info.get("name") or {}
    return {
        "folder_id": info.get("id"),
        "name": ((name.get("text") or {}).get("text")) if isinstance(name, dict) else name,
        "icon": (info.get("icon") or {}).get("name"),
        "is_shareable": info.get("is_shareable"),
        "has_my_invite_links": info.get("has_my_invite_links"),
    }


def list_folders(client: TelegramClient, *, timeout: float = 5.0) -> list[dict[str, Any]]:
    """The account's chat folders, in the order the user arranged them.

    TDLib has no getChatFolders. The list only ever arrives as an
    updateChatFolders update, pushed once shortly after login, so this reads
    the one the client remembered, waiting briefly if it has not come yet.
    The command that asked for getChatFolders failed closed on every run.
    """
    deadline = time.monotonic() + timeout
    event = client.latest_update("updateChatFolders")
    while event is None and time.monotonic() < deadline:
        time.sleep(0.05)
        event = client.latest_update("updateChatFolders")
    if event is None:
        raise TelegramTimeoutError(f"TDLib did not report the chat folders within {timeout:g}s.")
    return [folder_record(info) for info in event.get("chat_folders") or []]


def get_folder(client: TelegramClient, folder_id: int) -> dict[str, Any]:
    """One folder's full definition: its included, excluded and pinned chats."""
    return client.call("getChatFolder", {"chat_folder_id": folder_id})


def create_folder(
    client: TelegramClient, name: str, chat_ids: list[int], *, allow_write: bool = False
) -> dict[str, Any]:
    return client.call(
        "createChatFolder",
        {
            "folder": {
                "@type": "chatFolder",
                "name": {
                    "@type": "chatFolderName",
                    "text": {"@type": "formattedText", "text": name, "entities": []},
                },
                "included_chat_ids": chat_ids,
            }
        },
        allow_write=allow_write,
    )


def delete_folder(
    client: TelegramClient,
    folder_id: int,
    *,
    allow_write: bool = False,
    allow_destructive: bool = False,
) -> dict[str, Any]:
    return client.call(
        "deleteChatFolder",
        {"chat_folder_id": folder_id},
        allow_write=allow_write,
        allow_destructive=allow_destructive,
    )
