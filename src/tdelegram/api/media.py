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
    """Download the file a message carries, whatever kind of media it is."""
    from tdelegram.api.messages import get

    media = get(client, chat_ref, message_id).get("media") or {}
    file_id = media.get("file_id")
    if not isinstance(file_id, int):
        raise ValueError(f"Message {message_id} has no downloadable file.")
    return _download(client, file_id, timeout=timeout)


def upload(
    client: TelegramClient,
    chat_ref: str,
    path: str | Path,
    *,
    caption: str = "",
    wait: bool = False,
    allow_write: bool = False,
) -> dict[str, Any]:
    chat_id = resolve_id(client, chat_ref)
    pending = _send_file(client, chat_id, path, caption=caption, allow_write=allow_write)
    if not wait:
        return {
            "status": "pending",
            "pending_message": pending,
            "note": "delivery not confirmed; pass wait=True",
        }
    mid = (pending.get("message") or pending).get("id", 0)
    return wait_for_send(client, int(mid or 0), chat_id)


def local_file(path: str | Path) -> dict[str, Any]:
    """An InputFile for a path on this machine.

    TDLib wraps it once more per media kind, in an input object that also
    carries the thumbnail and dimensions: inputMessagePhoto takes an
    inputPhoto, not an InputFile. The bare file is what older TDLib wanted,
    and the pinned one refuses every media send built that way.
    """
    return {"@type": "inputFileLocal", "path": str(Path(path).expanduser())}


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
                "photo": {"@type": "inputPhoto", "photo": local_file(path)},
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
                "video": {"@type": "inputVideo", "video": local_file(path)},
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
                "voice_note": {"@type": "inputVoiceNote", "voice_note": local_file(path)},
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
                # A numeric file id is a local one; inputFileRemote takes the
                # string remote id.
                "sticker": {
                    "@type": "inputSticker",
                    "sticker": {"@type": "inputFileId", "id": sticker_file_id},
                },
            },
        },
        allow_write=allow_write,
    )
