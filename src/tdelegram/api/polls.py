"""Polls: answering, stopping, reading voters."""

from __future__ import annotations

from typing import Any

from tdelegram.api.chats import resolve_id
from tdelegram.client import TelegramClient


def answer_poll(
    client: TelegramClient,
    chat_ref: str,
    message_id: int,
    option_ids: list[int],
    *,
    allow_write: bool = False,
) -> dict[str, Any]:
    return client.call(
        "setPollAnswer",
        {
            "chat_id": resolve_id(client, chat_ref),
            "message_id": message_id,
            "option_ids": option_ids,
        },
        allow_write=allow_write,
    )


def stop_poll(
    client: TelegramClient, chat_ref: str, message_id: int, *, allow_write: bool = False
) -> dict[str, Any]:
    return client.call(
        "stopPoll",
        {"chat_id": resolve_id(client, chat_ref), "message_id": message_id},
        allow_write=allow_write,
    )


def poll_voters(
    client: TelegramClient, chat_ref: str, message_id: int, option_id: int, *, limit: int = 50
) -> dict[str, Any]:
    return client.call(
        "getPollVoters",
        {
            "chat_id": resolve_id(client, chat_ref),
            "message_id": message_id,
            "option_id": option_id,
            "limit": limit,
        },
    )
