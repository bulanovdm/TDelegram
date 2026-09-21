"""Stories: posting eligibility, archive listing, deletion."""

from __future__ import annotations

from typing import Any

from tdelegram.api.chats import resolve_id
from tdelegram.client import TelegramClient


def can_post_story(client: TelegramClient) -> dict[str, Any]:
    return client.call("canPostStory", {"chat_id": 0})


def list_archived_stories(
    client: TelegramClient, chat_ref: str, *, limit: int = 20
) -> dict[str, Any]:
    return client.call(
        "getChatArchivedStories",
        {"chat_id": resolve_id(client, chat_ref), "from_story_id": 0, "limit": limit},
    )


def delete_story(
    client: TelegramClient,
    story_id: int,
    *,
    allow_write: bool = False,
    allow_destructive: bool = False,
) -> dict[str, Any]:
    return client.call(
        "deleteStory",
        {"story_id": story_id},
        allow_write=allow_write,
        allow_destructive=allow_destructive,
    )


# -- secret chats -----------------------------------------------------
