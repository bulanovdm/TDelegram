"""Per-chat draft messages."""

from __future__ import annotations

from typing import Any

from tdelegram.api.chats import resolve_id
from tdelegram.client import TelegramClient


def set_draft(
    client: TelegramClient, chat_ref: str, text: str, *, allow_write: bool = False
) -> dict[str, Any]:
    """Leave `text` in the chat's input box, for the user to review and send.

    A draft is only visible to the account itself, across its devices, which
    makes it the natural hand-off for anything an agent composes. The text goes
    in `content`; the old `input_message_text` field is gone from TDLib, which
    ignored it and saved an empty draft.
    """
    return client.call(
        "setChatDraftMessage",
        {
            "chat_id": resolve_id(client, chat_ref),
            "draft_message": {
                "@type": "draftMessage",
                "content": {
                    "@type": "draftMessageContentText",
                    "text": {"@type": "formattedText", "text": text, "entities": []},
                },
            },
        },
        allow_write=allow_write,
    )


def clear_drafts(
    client: TelegramClient,
    *,
    allow_write: bool = False,
    allow_destructive: bool = False,
) -> dict[str, Any]:
    """Wipe every draft. There is no undo."""
    return client.call(
        "clearAllDraftMessages",
        {"exclude_secret_chats": False},
        allow_write=allow_write,
        allow_destructive=allow_destructive,
    )
