"""Resumable chat export: every message as a record, and optionally its file.

An export is a directory. `messages.jsonl` gets one record per message,
`chat.json` the chat itself, and `state.json` the stretch of history already
on disk -- so a re-run fetches only what is missing: first anything newer than
the newest message saved, then anything older than the oldest, until the
chat's beginning or `since`. Stop it at any point, or give it `maximum`, and
the next run carries on from there. Telegram Desktop's export can do neither,
and has no command line.

The walk holds one page at a time, so a chat of any length exports in constant
memory. The price is order: records are written as they are fetched, newest
first within a run, so sort on `message_id` when order matters. A run killed
between writing a page and saving its checkpoint repeats up to that page on
the next run; dedupe on `message_id` if one was.

Media are copied out only where Telegram lets them be saved. A chat with
protected content, or a message that cannot be saved, gets a note in its
record instead of a file: that is the chat owner's call, not ours.
"""

from __future__ import annotations

import json
import re
import shutil
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from tdelegram import normalize
from tdelegram.api.chats import detail_of, resolve
from tdelegram.api.users import SenderNames
from tdelegram.client import TelegramClient
from tdelegram.dates import parse_date
from tdelegram.errors import TelegramError
from tdelegram.files import download
from tdelegram.paging import history_pages, paginate

MESSAGES = "messages.jsonl"
STATE = "state.json"
CHAT = "chat.json"
MEDIA = "media"
# How often, in messages, the checkpoint catches up with the file.
CHECKPOINT_EVERY = 100


def export_chat(
    client: TelegramClient,
    chat_ref: str,
    out_dir: str | Path,
    *,
    since: str | int | None = None,
    media: bool = False,
    maximum: int | None = None,
) -> dict[str, Any]:
    """Bring the export in `out_dir` up to date. Returns a summary of the run."""
    out = Path(out_dir).expanduser()
    out.mkdir(parents=True, exist_ok=True)
    chat = resolve(client, chat_ref)
    chat_id = int(chat["id"])
    state = _load_state(out, chat_id)
    since_ts = since if isinstance(since, int) else parse_date(since)
    if state["complete"] and _widens(state.get("since"), since_ts):
        state["complete"] = False  # a longer window than any run so far has covered
    if state["newest_id"] is None:
        # Nothing saved yet: an empty chat, or all of it older than --since.
        # With no newest message to catch up from, only a fresh walk from the
        # top can find what arrived since, so such an export is never done --
        # marking it complete left every later run fetching nothing, forever.
        state["complete"] = False

    record = normalize.chat_record(chat, detail=detail_of(client, chat))
    (out / CHAT).write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n", "utf-8")
    writer = _Writer(client, out, chat, media=media)
    run = _Run(client, chat_id, state, writer, out, maximum)
    try:
        if state["newest_id"] is not None:
            run.newer()
        if not state["complete"] and not run.exhausted:
            run.older(since_ts)
    finally:
        writer.close()
        _save_state(out, state)
    return {
        "chat_id": chat_id,
        "title": chat.get("title"),
        "written": run.written,
        "exported": state["exported"],
        "newest_id": state["newest_id"],
        "oldest_id": state["oldest_id"],
        "complete": state["complete"],
        "media_saved": writer.media_saved,
        "media_skipped": writer.media_skipped,
        "out": str(out),
    }


def _widens(covered: int | None, wanted: int | None) -> bool:
    """Whether `wanted` reaches further back than the `covered` window did."""
    if covered is None:
        return False  # the whole history was already exported
    return wanted is None or wanted < covered


def _load_state(out: Path, chat_id: int) -> dict[str, Any]:
    path = out / STATE
    if not path.exists():
        return {
            "chat_id": chat_id,
            "newest_id": None,
            "oldest_id": None,
            "complete": False,
            "since": None,
            "exported": 0,
        }
    state: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    if state.get("chat_id") != chat_id:
        raise ValueError(
            f"{out} holds an export of chat {state.get('chat_id')}, not {chat_id}; "
            "give this chat its own --out."
        )
    return state


def _save_state(out: Path, state: dict[str, Any]) -> None:
    # Replace rather than rewrite, so a kill mid-write cannot leave half a file.
    scratch = out / f"{STATE}.tmp"
    scratch.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")
    scratch.replace(out / STATE)


class _Run:
    def __init__(
        self,
        client: TelegramClient,
        chat_id: int,
        state: dict[str, Any],
        writer: _Writer,
        out: Path,
        maximum: int | None,
    ) -> None:
        self._client = client
        self._chat_id = chat_id
        self._state = state
        self._writer = writer
        self._out = out
        self._maximum = maximum
        self.written = 0
        self.exhausted = False

    def _walk(self, start: int) -> Iterator[dict[str, Any]]:
        """Messages from `start` (0: the newest) back towards the beginning."""
        return paginate(history_pages(self._client, self._chat_id), start=start)

    def _counted(self) -> bool:
        """Count a message just written; False once this run's budget is spent.

        Called only after the write and the move of the resume point, in that
        order: a write cut short by Ctrl-C, a failed download or a full disk
        must not leave the checkpoint past a message that is not on disk. The
        worst an interruption between the two can do is repeat one message.
        """
        self.written += 1
        self._state["exported"] += 1
        if self.written % CHECKPOINT_EVERY == 0:
            self._checkpoint()
        if self._maximum is not None and self.written >= self._maximum:
            self.exhausted = True
            return False
        return True

    def _checkpoint(self) -> None:
        self._writer.flush()
        _save_state(self._out, self._state)

    def newer(self) -> None:
        """Everything above the newest message already saved.

        Walking down from the top, the gap only closes when the walk reaches
        what is saved, so an interrupted walk is kept as its own stretch,
        `upper`, and the next run resumes beneath it instead of starting over.
        """
        state = self._state
        saved = int(state["newest_id"])
        upper: dict[str, int] = dict(state.get("upper") or {})
        for message in self._walk(upper.get("low", 0)):
            mid = int(message.get("id") or 0)
            if mid <= saved:
                break
            if upper and mid >= upper["low"]:
                continue  # written by the interrupted run
            self._writer.write(message)
            upper.setdefault("high", mid)
            upper["low"] = mid
            state["upper"] = upper
            if not self._counted():
                return
        state["newest_id"] = upper.get("high", saved)
        state.pop("upper", None)

    def older(self, since_ts: int | None) -> None:
        """Everything below the oldest message saved, back to `since` or the start."""
        state = self._state
        for message in self._walk(state["oldest_id"] or 0):
            mid = int(message.get("id") or 0)
            if state["oldest_id"] is not None and mid >= state["oldest_id"]:
                continue
            date = message.get("date")
            if since_ts is not None and isinstance(date, int) and date < since_ts:
                state.update(complete=True, since=since_ts)
                return
            self._writer.write(message)
            if state["newest_id"] is None:
                state["newest_id"] = mid
            state["oldest_id"] = mid
            if not self._counted():
                return
        state.update(complete=True, since=None)


class _Writer:
    def __init__(
        self, client: TelegramClient, out: Path, chat: dict[str, Any], *, media: bool
    ) -> None:
        self._client = client
        self._out = out
        self._media = media
        self._protected = bool(chat.get("has_protected_content"))
        self._names = SenderNames(client)
        self._file = (out / MESSAGES).open("a", encoding="utf-8")
        self.media_saved = 0
        self.media_skipped = 0

    def write(self, message: dict[str, Any]) -> None:
        record = self._names.label(normalize.message_record(message))
        if self._media and record.get("media"):
            self._save_media(message, record)
        self._file.write(json.dumps(record, ensure_ascii=False) + "\n")

    def _save_media(self, message: dict[str, Any], record: dict[str, Any]) -> None:
        if self._protected or message.get("can_be_saved") is False:
            record["media_skipped"] = "protected content: this chat does not allow saving it"
            self.media_skipped += 1
            return
        found = record["media"]
        try:
            file = download(self._client, int(found["file_id"]))
        except (TelegramError, KeyError, TypeError, ValueError) as exc:
            record["media_error"] = str(exc)
            return
        local = file.get("local") or {}
        source = local.get("path")
        if not source or not local.get("is_downloading_completed"):
            record["media_error"] = "the download did not complete"
            return
        fallback = f"{found.get('kind', 'file')}{Path(source).suffix}"
        name = safe_name(found.get("file_name") or fallback)
        target = self._out / MEDIA / f"{record['message_id']}-{name}"
        target.parent.mkdir(exist_ok=True)
        shutil.copyfile(source, target)
        record["media_path"] = str(target.relative_to(self._out))
        self.media_saved += 1

    def flush(self) -> None:
        self._file.flush()

    def close(self) -> None:
        self._file.close()


def safe_name(name: str) -> str:
    """A file name that cannot climb out of the media directory or break a shell."""
    cleaned = re.sub(r"[^\w.\-]+", "_", name).lstrip(".")
    return cleaned[:120] or "file"
