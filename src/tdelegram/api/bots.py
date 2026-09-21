"""Bots: inline queries, inline results, callback answers."""

from __future__ import annotations

from typing import Any

from tdelegram.api.chats import resolve_id
from tdelegram.client import TelegramClient


def start_bot(
    client: TelegramClient,
    bot_ref: str,
    chat_ref: str,
    *,
    parameter: str = "",
    allow_write: bool = False,
) -> dict[str, Any]:
    bot_id = resolve_id(client, bot_ref)
    chat_id = resolve_id(client, chat_ref)
    return client.call(
        "sendBotStartMessage",
        {"bot_user_id": bot_id, "chat_id": chat_id, "parameter": parameter},
        allow_write=allow_write,
    )


def inline_results(
    client: TelegramClient, bot_user_id: int, query: str, *, offset: str = ""
) -> dict[str, Any]:
    return client.call(
        "getInlineQueryResults",
        {"bot_user_id": bot_user_id, "chat_id": 0, "query": query, "offset": offset},
    )


def send_inline_result(
    client: TelegramClient, chat_ref: str, result_id: str, *, allow_write: bool = False
) -> dict[str, Any]:
    return client.call(
        "sendInlineQueryResultMessage",
        {"chat_id": resolve_id(client, chat_ref), "result_id": result_id},
        allow_write=allow_write,
    )


def answer_callback(
    client: TelegramClient, query_id: int, *, text: str = "", allow_write: bool = False
) -> dict[str, Any]:
    return client.call(
        "answerCallbackQuery",
        {"callback_query_id": query_id, "text": text},
        allow_write=allow_write,
    )


def answer_inline(
    client: TelegramClient,
    inline_query_id: int,
    results: list[dict[str, Any]],
    *,
    allow_write: bool = False,
) -> dict[str, Any]:
    return client.call(
        "answerInlineQuery",
        {"inline_query_id": inline_query_id, "results": results},
        allow_write=allow_write,
    )


# -- stories ----------------------------------------------------------
