"""Media: download / upload / send photo/file/voice/video/sticker/gif."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from tdelegram.api.chats import resolve_id
from tdelegram.client import TelegramClient
from tdelegram.files import download as _download
from tdelegram.files import send_file as _send_file
from tdelegram.files import wait_for_send


def download(client: TelegramClient, file_id: int, *, timeout: float = 300.0) -> dict[str, Any]:
    return _download(client, file_id, timeout=timeout)


def download_message_file(
    client: TelegramClient, chat_ref: str, message_id: int, *, timeout: float = 300.0
) -> dict[str, Any]:
    from tdelegram.api.messages import get

    # _message_file_id walks the raw TDLib content, which the normalized
    # record does not carry.
    record = get(client, chat_ref, message_id, include_raw=True)
    file_id = _message_file_id(record.get("raw") or {})
    if file_id is None:
        raise ValueError(f"Message {message_id} has no downloadable file.")
    return _download(client, file_id, timeout=timeout)


def _message_file_id(message: dict[str, Any]) -> int | None:
    content = message.get("content") or {}
    for key in (
        "document",
        "photo",
        "audio",
        "video",
        "voice_note",
        "video_note",
        "animation",
        "sticker",
    ):
        node = content.get(key)
        found = _deep_file_id(node)
        if found is not None:
            return found
    return _deep_file_id(content)


def _deep_file_id(node: Any, depth: int = 0) -> int | None:
    if depth > 4 or not isinstance(node, dict):
        return None
    file_node = node.get("file")
    if isinstance(file_node, dict) and isinstance(file_node.get("id"), int):
        return int(file_node["id"])
    if isinstance(node.get("id"), int) and node.get("@type") == "file":
        return int(node["id"])
    # document-style nesting: {document: {id: ...}} or {document: {file: {id}}}
    for key in ("document", "sticker", "audio", "video", "photo", "animation", "voice_note"):
        nested = node.get(key)
        if isinstance(nested, dict):
            found = _deep_file_id(nested, depth + 1)
            if found is not None:
                return found
    sizes = node.get("sizes")
    if isinstance(sizes, list) and sizes:
        try:
            biggest = max(
                sizes,
                key=lambda s: s.get("width", 0) * s.get("height", 0) if isinstance(s, dict) else 0,
            )
        except (ValueError, TypeError):
            biggest = None
        if isinstance(biggest, dict):
            photo_file = biggest.get("photo")
            if isinstance(photo_file, dict) and isinstance(photo_file.get("id"), int):
                return int(photo_file["id"])
    return None


def upload(
    client: TelegramClient,
    chat_ref: str,
    path: str | Path,
    *,
    caption: str = "",
    wait: bool = False,
) -> dict[str, Any]:
    pending = _send_file(client, resolve_id(client, chat_ref), path, caption=caption)
    if not wait:
        return {
            "status": "pending",
            "pending_message": pending,
            "note": "delivery not confirmed; pass wait=True",
        }
    mid = (pending.get("message") or pending).get("id", 0)
    return wait_for_send(client, int(mid or 0), resolve_id(client, chat_ref))


def send_photo(
    client: TelegramClient,
    chat_ref: str,
    path: str | Path,
    *,
    caption: str = "",
    allow_write: bool = False,
) -> dict[str, Any]:
    return client.call(
        "sendMessage",
        {
            "chat_id": resolve_id(client, chat_ref),
            "input_message_content": {
                "@type": "inputMessagePhoto",
                "photo": {"@type": "inputFileLocal", "path": str(path)},
                "caption": {"@type": "formattedText", "text": caption, "entities": []},
            },
        },
        allow_write=allow_write,
    )


def send_video(
    client: TelegramClient,
    chat_ref: str,
    path: str | Path,
    *,
    caption: str = "",
    allow_write: bool = False,
) -> dict[str, Any]:
    return client.call(
        "sendMessage",
        {
            "chat_id": resolve_id(client, chat_ref),
            "input_message_content": {
                "@type": "inputMessageVideo",
                "video": {"@type": "inputFileLocal", "path": str(path)},
                "caption": {"@type": "formattedText", "text": caption, "entities": []},
            },
        },
        allow_write=allow_write,
    )


def send_voice(
    client: TelegramClient,
    chat_ref: str,
    path: str | Path,
    *,
    caption: str = "",
    allow_write: bool = False,
) -> dict[str, Any]:
    return client.call(
        "sendMessage",
        {
            "chat_id": resolve_id(client, chat_ref),
            "input_message_content": {
                "@type": "inputMessageVoiceNote",
                "voice_note": {"@type": "inputFileLocal", "path": str(path)},
                "caption": {"@type": "formattedText", "text": caption, "entities": []},
            },
        },
        allow_write=allow_write,
    )


def send_sticker(
    client: TelegramClient, chat_ref: str, sticker_file_id: int, *, allow_write: bool = False
) -> dict[str, Any]:
    return client.call(
        "sendMessage",
        {
            "chat_id": resolve_id(client, chat_ref),
            "input_message_content": {
                "@type": "inputMessageSticker",
                "sticker": {"@type": "inputFileRemote", "id": sticker_file_id},
            },
        },
        allow_write=allow_write,
    )
