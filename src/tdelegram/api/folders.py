"""Chat folders."""

from __future__ import annotations

from typing import Any

from tdelegram.client import TelegramClient


def list_folders(client: TelegramClient) -> dict[str, Any]:
    # getChatFolders does not exist in all TDLib builds; fall back gracefully.
    try:
        return client.call("getChatFolders", {})
    except RuntimeError:
        try:
            return client.call("getChatFolder", {"chat_folder_id": 0})
        except Exception:
            return {"@type": "chatFolders", "folders": []}


def create_folder(
    client: TelegramClient, name: str, chat_ids: list[int], *, allow_write: bool = False
) -> dict[str, Any]:
    return client.call(
        "createChatFolder",
        {"folder": {"@type": "chatFolder", "name": name, "included_chat_ids": chat_ids}},
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
