"""Message reactions."""

from __future__ import annotations

from typing import Any

from tdelegram.api.chats import resolve_id
from tdelegram.client import TelegramClient


def add_reaction(
    client: TelegramClient, chat_ref: str, message_id: int, emoji: str, *, allow_write: bool = False
) -> dict[str, Any]:
    return client.call(
        "addMessageReaction",
        {
            "chat_id": resolve_id(client, chat_ref),
            "message_id": message_id,
            "reaction_type": {"@type": "reactionTypeEmoji", "emoji": emoji},
        },
        allow_write=allow_write,
    )


def remove_reaction(
    client: TelegramClient, chat_ref: str, message_id: int, emoji: str, *, allow_write: bool = False
) -> dict[str, Any]:
    return client.call(
        "removeMessageReaction",
        {
            "chat_id": resolve_id(client, chat_ref),
            "message_id": message_id,
            "reaction_type": {"@type": "reactionTypeEmoji", "emoji": emoji},
        },
        allow_write=allow_write,
    )


def added_reactions(client: TelegramClient, chat_ref: str, message_id: int) -> dict[str, Any]:
    return client.call(
        "getMessageAddedReactions",
        {"chat_id": resolve_id(client, chat_ref), "message_id": message_id},
    )
