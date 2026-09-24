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
    client: TelegramClient,
    bot_user_id: int,
    query: str,
    *,
    offset: str = "",
    allow_write: bool = False,
) -> dict[str, Any]:
    """Send an inline query to a bot. The bot is notified, so this is a write."""
    return client.call(
        "getInlineQueryResults",
        {"bot_user_id": bot_user_id, "chat_id": 0, "query": query, "offset": offset},
        allow_write=allow_write,
    )


def send_inline_result(
    client: TelegramClient,
    chat_ref: str,
    query_id: int | str,
    result_id: str,
    *,
    allow_write: bool = False,
) -> dict[str, Any]:
    """Send one result of an inline query. `query_id` comes from inline_results()."""
    return client.call(
        "sendInlineQueryResultMessage",
        {"chat_id": resolve_id(client, chat_ref), "query_id": query_id, "result_id": result_id},
        allow_write=allow_write,
    )


def press_button(
    client: TelegramClient,
    chat_ref: str,
    message_id: int,
    label: str,
    *,
    allow_write: bool = False,
) -> dict[str, Any]:
    """Press the inline button labelled `label` under a bot's message.

    The bot sees the press and acts on it, which is why getCallbackQueryAnswer
    is a write. A URL button has nothing to press, so its link comes back
    instead; a button that needs a password, a game or a payment is refused.
    """
    chat_id = resolve_id(client, chat_ref)
    message = client.call("getMessage", {"chat_id": chat_id, "message_id": message_id})
    markup = message.get("reply_markup") or {}
    rows = markup.get("rows") if markup.get("@type") == "replyMarkupInlineKeyboard" else None
    buttons = [button for row in rows or [] for button in row or []]
    chosen = [b for b in buttons if b.get("text") == label] or [
        b for b in buttons if str(b.get("text", "")).casefold() == label.casefold()
    ]
    if not chosen:
        offered = ", ".join(repr(b.get("text")) for b in buttons) or "none"
        raise ValueError(f"Message {message_id} has no button {label!r}; its buttons: {offered}.")
    kind = chosen[0].get("type") or {}
    found = {"chat_id": chat_id, "message_id": message_id, "button": chosen[0].get("text")}
    if kind.get("@type") == "inlineKeyboardButtonTypeUrl":
        return {**found, "pressed": False, "url": kind.get("url")}
    if kind.get("@type") != "inlineKeyboardButtonTypeCallback":
        raise ValueError(f"{label!r} is a {kind.get('@type')} button, which only an app can use.")
    answer = client.call(
        "getCallbackQueryAnswer",
        {
            "chat_id": chat_id,
            "message_id": message_id,
            "payload": {"@type": "callbackQueryPayloadData", "data": kind.get("data", "")},
        },
        allow_write=allow_write,
    )
    return {
        **found,
        "pressed": True,
        "answer": answer.get("text") or None,
        "show_alert": bool(answer.get("show_alert")),
        "url": answer.get("url") or None,
    }


def answer_callback(
    client: TelegramClient, query_id: int, *, text: str = "", allow_write: bool = False
) -> dict[str, Any]:
    """For bot accounts only: a user account cannot answer callback queries."""
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
