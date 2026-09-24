"""Message operations: history, send/reply/edit/delete/forward/pin/schedule/react/link/get."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from typing import Any

from tdelegram import normalize
from tdelegram.api.chats import resolve_id
from tdelegram.client import TelegramClient
from tdelegram.dates import parse_date
from tdelegram.entities import parse_entities
from tdelegram.paging import paginate


def get(
    client: TelegramClient, chat_ref: str, message_id: int, *, include_raw: bool = False
) -> dict[str, Any]:
    """Fetch one message as a normalized record.

    `chat history` yields flat records, so returning the raw TDLib object here
    meant the same message had two shapes depending on how it was fetched and
    `.text` was null on one of them. `include_raw=True` keeps the original
    under `raw`, matching `chats.info`.
    """
    from tdelegram.api.users import SenderNames

    chat_id = resolve_id(client, chat_ref)
    message = client.call("getMessage", {"chat_id": chat_id, "message_id": message_id})
    return SenderNames(client).label(normalize.message_record(message, include_raw=include_raw))


def iter_history(
    client: TelegramClient,
    chat_ref: str,
    *,
    maximum: int | None = None,
    since: str | int | None = None,
    until: str | int | None = None,
    topic_id: int | None = None,
    sender_id: int | None = None,
    contains: list[str] | None = None,
    include_raw: bool = False,
) -> Iterator[dict[str, Any]]:
    from tdelegram.api.topics import topic_object
    from tdelegram.api.users import SenderNames

    names = SenderNames(client)
    chat_id = resolve_id(client, chat_ref)
    since_ts = (
        since if isinstance(since, int) else parse_date(since if isinstance(since, str) else None)
    )
    until_ts = (
        until if isinstance(until, int) else parse_date(until if isinstance(until, str) else None)
    )
    _ = topic_object(topic_id, "forum")

    def _fetch(cursor: int) -> tuple[list[dict[str, Any]], int | None]:
        result = client.call(
            "getChatHistory",
            {
                "chat_id": chat_id,
                "from_message_id": cursor,
                "offset": 0,
                "limit": 100,
                "only_local": False,
            },
        )
        messages: list[dict[str, Any]] = result.get("messages", [])
        if not messages:
            return [], None
        last = messages[-1].get("id")
        nxt = last if isinstance(last, int) and last != cursor else None
        return messages, nxt

    def _stop(item: dict[str, Any]) -> bool:
        ts = item.get("date")
        return isinstance(ts, int) and since_ts is not None and ts < since_ts

    yielded = 0
    for message in paginate(_fetch, maximum=None, stop_when=_stop):
        if sender_id is not None and (message.get("sender_id") or {}).get("user_id") != sender_id:
            continue
        if topic_id is not None:
            from tdelegram.normalize import topic_id_of

            if topic_id_of(message) != topic_id:
                continue
        ts = message.get("date")
        if isinstance(ts, int):
            if until_ts is not None and ts > until_ts:
                continue
        text = normalize.message_text(message).casefold()
        if contains and any(term.casefold() not in text for term in contains):
            continue
        yield names.label(normalize.message_record(message, include_raw=include_raw))
        yielded += 1
        if maximum is not None and yielded >= maximum:
            return


def history(client: TelegramClient, chat_ref: str, **kwargs: Any) -> list[dict[str, Any]]:
    return list(iter_history(client, chat_ref, **kwargs))


def _input_text(client: TelegramClient, text: str, parse_mode: str | None) -> dict[str, Any]:
    formatted = (
        parse_entities(client.transport, text, parse_mode)
        if parse_mode
        else {"@type": "formattedText", "text": text, "entities": []}
    )
    return {"@type": "inputMessageText", "text": formatted}


def send_options(
    *, schedule_date: int | None = None, silent: bool = False
) -> dict[str, Any] | None:
    """messageSendOptions, or None when every option is at its default.

    Scheduling lives inside this object. It used to be passed at the top level
    of sendMessage, which has no such field, so TDLib ignored it and a message
    meant for later went out at once.
    """
    options: dict[str, Any] = {}
    if silent:
        options["disable_notification"] = True
    if schedule_date is not None:
        options["scheduling_state"] = {
            "@type": "messageSchedulingStateSendAtDate",
            "send_date": schedule_date,
        }
    return {"@type": "messageSendOptions", **options} if options else None


def send(
    client: TelegramClient,
    chat_ref: str,
    text: str,
    *,
    parse_mode: str | None = None,
    reply_to: int | None = None,
    schedule_date: int | None = None,
    silent: bool = False,
    topic_id: int | None = None,
    allow_write: bool = False,
) -> dict[str, Any]:
    """Send a text message. `topic_id` names a forum topic; without it a forum
    message lands in General."""
    from tdelegram.api.topics import topic_object

    chat_id = resolve_id(client, chat_ref)
    content = _input_text(client, text, parse_mode)
    params: dict[str, Any] = {"chat_id": chat_id, "input_message_content": content}
    if topic_id is not None:
        params["topic_id"] = topic_object(topic_id, "forum")
    if reply_to is not None:
        params["reply_to"] = {"@type": "inputMessageReplyToMessage", "message_id": reply_to}
    options = send_options(schedule_date=schedule_date, silent=silent)
    if options is not None:
        params["options"] = options
    return client.call("sendMessage", params, allow_write=allow_write)


def reply(
    client: TelegramClient, chat_ref: str, message_id: int, text: str, **kwargs: Any
) -> dict[str, Any]:
    return send(client, chat_ref, text, reply_to=message_id, **kwargs)


def edit(
    client: TelegramClient,
    chat_ref: str,
    message_id: int,
    text: str,
    *,
    parse_mode: str | None = None,
    allow_write: bool = False,
) -> dict[str, Any]:
    chat_id = resolve_id(client, chat_ref)
    formatted = (
        parse_entities(client.transport, text, parse_mode)
        if parse_mode
        else {"@type": "formattedText", "text": text, "entities": []}
    )
    return client.call(
        "editMessageText",
        {
            "chat_id": chat_id,
            "message_id": message_id,
            "input_message_content": {"@type": "inputMessageText", "text": formatted},
        },
        allow_write=allow_write,
    )


def delete(
    client: TelegramClient,
    chat_ref: str,
    message_ids: list[int],
    *,
    revoke: bool = True,
    allow_write: bool = False,
    allow_destructive: bool = False,
) -> dict[str, Any]:
    chat_id = resolve_id(client, chat_ref)
    return client.call(
        "deleteMessages",
        {"chat_id": chat_id, "message_ids": message_ids, "revoke": revoke},
        allow_write=allow_write,
        allow_destructive=allow_destructive,
    )


def iter_own_messages(
    client: TelegramClient,
    chat_ref: str,
    *,
    since: str | int | None = None,
    until: str | int | None = None,
    maximum: int | None = None,
) -> Iterator[dict[str, Any]]:
    """The account's own messages in a chat, newest first, found server-side.

    Only messages sent as the user: posts made as a channel belong to the
    channel, and removing those is an admin's job, not a clean-up of one's own.
    """
    from tdelegram.paging import search_pages

    chat_id = resolve_id(client, chat_ref)
    own_id = client.call("getMe", {}).get("id")
    if not isinstance(own_id, int):
        raise ValueError("Could not determine the current account's user id.")
    since_ts = since if isinstance(since, int) else parse_date(since)
    until_ts = until if isinstance(until, int) else parse_date(until)

    def _too_old(item: dict[str, Any]) -> bool:
        date = item.get("date")
        return isinstance(date, int) and since_ts is not None and date < since_ts

    found = 0
    mine = search_pages(client, chat_id, "", sender_id=own_id)
    for message in paginate(mine, stop_when=_too_old):
        date = message.get("date")
        if until_ts is not None and isinstance(date, int) and date > until_ts:
            continue
        if (message.get("sender_id") or {}).get("user_id") != own_id:
            continue
        yield normalize.message_record(message)
        found += 1
        if maximum is not None and found >= maximum:
            return


BATCH = 100


def delete_in_batches(
    client: TelegramClient,
    chat_id: int,
    message_ids: list[int],
    *,
    revoke: bool = True,
    allow_write: bool = False,
    allow_destructive: bool = False,
    on_batch: Callable[[dict[str, Any]], None] | None = None,
) -> dict[str, Any]:
    """Delete many messages, a hundred at a time, reporting each batch.

    A batch TDLib refuses as a whole -- with a 400, or the 403 Telegram gives a
    message it will not delete -- is retried one message at a time, so a single
    such message is skipped instead of blocking every message behind it. If
    nothing at all could be deleted by then, the chat is refusing, not one
    message, and the error is raised rather than tried on every other batch.
    A FloodWait still raises: writes never retry on their own, and a re-run
    picks up whatever is left.
    """
    from tdelegram.errors import InvalidRequest, TelegramPermissionError

    refusals = (InvalidRequest, TelegramPermissionError)
    deleted, skipped = 0, []
    for start in range(0, len(message_ids), BATCH):
        batch = message_ids[start : start + BATCH]
        try:
            client.call(
                "deleteMessages",
                {"chat_id": chat_id, "message_ids": batch, "revoke": revoke},
                allow_write=allow_write,
                allow_destructive=allow_destructive,
            )
            deleted += len(batch)
        except refusals as refused:
            for mid in batch:
                try:
                    client.call(
                        "deleteMessages",
                        {"chat_id": chat_id, "message_ids": [mid], "revoke": revoke},
                        allow_write=allow_write,
                        allow_destructive=allow_destructive,
                    )
                    deleted += 1
                except refusals:
                    skipped.append(mid)
            if deleted == 0:
                raise refused from None
        if on_batch is not None:
            on_batch({"chat_id": chat_id, "deleted": deleted, "of": len(message_ids)})
    return {"chat_id": chat_id, "deleted": deleted, "skipped": skipped, "revoked": revoke}


def forward(
    client: TelegramClient,
    from_chat: str,
    to_chat: str,
    message_ids: list[int],
    *,
    allow_write: bool = False,
) -> dict[str, Any]:
    return client.call(
        "forwardMessages",
        {
            "chat_id": resolve_id(client, to_chat),
            "from_chat_id": resolve_id(client, from_chat),
            "message_ids": message_ids,
        },
        allow_write=allow_write,
    )


def pin(
    client: TelegramClient,
    chat_ref: str,
    message_id: int,
    *,
    disable_notification: bool = False,
    allow_write: bool = False,
) -> dict[str, Any]:
    return client.call(
        "pinChatMessage",
        {
            "chat_id": resolve_id(client, chat_ref),
            "message_id": message_id,
            "disable_notification": disable_notification,
        },
        allow_write=allow_write,
    )


def unpin(
    client: TelegramClient,
    chat_ref: str,
    message_id: int | None = None,
    *,
    allow_write: bool = False,
    allow_destructive: bool = False,
) -> dict[str, Any]:
    """Unpin one message, or every pinned message when no id is given.

    Unpinning everything is destructive -- the previous set is not recorded.
    This used to send unpinChatMessage without the message_id it requires.
    """
    chat_id = resolve_id(client, chat_ref)
    if message_id is None:
        return client.call(
            "unpinAllChatMessages",
            {"chat_id": chat_id},
            allow_write=allow_write,
            allow_destructive=allow_destructive,
        )
    return client.call(
        "unpinChatMessage", {"chat_id": chat_id, "message_id": message_id}, allow_write=allow_write
    )


def link(client: TelegramClient, chat_ref: str, message_id: int) -> dict[str, Any]:
    return client.call(
        "getMessageLink",
        {"chat_id": resolve_id(client, chat_ref), "message_id": message_id},
    )


def react(
    client: TelegramClient,
    chat_ref: str,
    message_id: int,
    emoji: str,
    *,
    allow_write: bool = False,
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
