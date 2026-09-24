"""Output helpers: data on stdout as JSONL, everything else on stderr."""

from __future__ import annotations

import json
import sys
from collections.abc import Iterable
from pathlib import Path
from typing import Any


def emit(record: dict[str, Any], *, fmt: str = "jsonl", out: str | None = None) -> None:
    if fmt == "json":
        payload = json.dumps(record, ensure_ascii=False, indent=2)
    elif fmt == "table":
        payload = _as_table(record)
    elif fmt == "text":
        payload = as_text(record)
    else:
        payload = json.dumps(record, ensure_ascii=False, separators=(",", ":"))
    if out:
        with open(out, "a", encoding="utf-8") as handle:
            handle.write(payload + "\n")
    else:
        print(payload, flush=True)


def emit_many(
    records: Iterable[dict[str, Any]], *, fmt: str = "jsonl", out: str | None = None
) -> None:
    if fmt == "json" and out is None:
        items = list(records)
        print(json.dumps(items, ensure_ascii=False, indent=2), flush=True)
        return
    for record in records:
        emit(record, fmt=fmt, out=out)


def warn(message: str) -> None:
    print(message, file=sys.stderr, flush=True)


def error_envelope(code: int, message: str, method: str = "") -> dict[str, Any]:
    return {"ok": False, "error": {"code": code, "message": message, "method": method}}


FORMATS = ("jsonl", "json", "table", "text")

_MEDIA_WORDS = {"voice_note": "voice message", "video_note": "video message"}


def as_text(record: dict[str, Any]) -> str:
    """Plain, linear text: a message as one line a person or a screen reader can
    take in order -- when, who, where, what -- with media spelled out in words.

    Continuation lines of a long message are indented, so each message still
    starts on a line of its own. Anything that is not a message or a chat falls
    back to `key: value` pairs.
    """
    if "message_id" in record and "content_type" in record:
        return _message_text(record)
    if "chat_id" in record and "title" in record and "unread_count" in record:
        handle = f"@{record['username']}" if record.get("username") else record.get("chat_id")
        unread = record.get("unread_count") or 0
        return f"{record.get('title')} ({handle}), {unread} unread"
    return "; ".join(
        f"{key}: {_plain(value)}"
        for key, value in record.items()
        if key != "raw" and value not in (None, "", [], {})
    )


def _plain(value: Any) -> str:
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False)
    return str(value)


def _message_text(record: dict[str, Any]) -> str:
    when = str(record.get("date") or "")[:16].replace("T", " ")
    who = record.get("sender_name") or "someone"
    if record.get("is_outgoing"):
        who = "you"
    where = f" in {record['chat_title']}" if record.get("chat_title") else ""
    lines = (record.get("text") or "").splitlines() or [""]
    media = record.get("media")
    if media:
        kind = str(media.get("kind", "file"))
        words = _MEDIA_WORDS.get(kind, kind.replace("_", " "))
        detail = [str(media["file_name"])] if media.get("file_name") else []
        if media.get("duration"):
            detail.append(f"{media['duration']} seconds")
        described = f"[{words}{', ' + ', '.join(detail) if detail else ''}]"
        if media.get("transcript"):
            described += f' transcript: "{media["transcript"]}"'
        lines[0] = f"{lines[0]} {described}".strip()
    first = f"{when}, {who}{where}: {lines[0]}"
    return "\n".join([first, *(f"    {line}" for line in lines[1:])])


def _as_table(record: dict[str, Any]) -> str:
    parts = [f"{key}={value!r}" for key, value in record.items() if key != "raw"]
    return " | ".join(parts)


def data_file(path: str | Path) -> Path:
    return Path(path).expanduser()
