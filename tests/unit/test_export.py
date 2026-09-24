"""Resumable export: what is on disk is never fetched twice, and nothing is lost."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from tdelegram.api import export
from tdelegram.client import TelegramClient
from tdelegram.transport import FakeTransport

CHAT_ID = -1001234


def _message(mid: int, *, date: int | None = None, **extra: Any) -> dict[str, Any]:
    return {
        "@type": "message",
        "id": mid,
        "chat_id": CHAT_ID,
        "date": date if date is not None else 1_700_000_000 + mid * 60,
        "sender_id": {"@type": "messageSenderUser", "user_id": 7},
        "content": {"@type": "messageText", "text": {"text": f"message {mid}"}},
        **extra,
    }


class History:
    """A chat whose history pages the way TDLib's does: from_message_id is
    inclusive, 0 means the newest, and pages come small."""

    def __init__(self, transport: FakeTransport, count: int, **chat: Any) -> None:
        self.messages = {mid: _message(mid) for mid in range(1, count + 1)}
        transport.add_simple_response(
            "searchPublicChat",
            {
                "@type": "chat",
                "id": CHAT_ID,
                "title": "Archive me",
                "type": {"@type": "chatTypeSupergroup", "supergroup_id": 1234},
                **chat,
            },
        )
        transport.add_response(lambda r: r.get("@type") == "getChatHistory", self._page)

    def add(self, *mids: int, **extra: Any) -> None:
        for mid in mids:
            self.messages[mid] = _message(mid, **extra)

    def _page(self, request: dict[str, Any]) -> dict[str, Any]:
        start = request["from_message_id"]
        ids = sorted((m for m in self.messages if start == 0 or m <= start), reverse=True)
        return {"@type": "messages", "messages": [self.messages[m] for m in ids[:3]]}


def _records(out: Path) -> list[dict[str, Any]]:
    lines = (out / export.MESSAGES).read_text(encoding="utf-8").splitlines()
    return [json.loads(line) for line in lines]


def _ids(out: Path) -> list[int]:
    return [record["message_id"] for record in _records(out)]


def test_a_first_export_takes_the_whole_history(
    client: TelegramClient, transport: FakeTransport, tmp_path: Path
) -> None:
    History(transport, 7)
    summary = export.export_chat(client, "archive", tmp_path)
    assert summary["written"] == 7 and summary["complete"] is True
    assert sorted(_ids(tmp_path)) == list(range(1, 8))
    state = json.loads((tmp_path / export.STATE).read_text())
    assert (state["newest_id"], state["oldest_id"]) == (7, 1)
    assert json.loads((tmp_path / export.CHAT).read_text())["title"] == "Archive me"


def test_a_rerun_fetches_only_what_is_new(
    client: TelegramClient, transport: FakeTransport, tmp_path: Path
) -> None:
    history = History(transport, 5)
    export.export_chat(client, "archive", tmp_path)
    history.add(6, 7)
    summary = export.export_chat(client, "archive", tmp_path)
    assert summary["written"] == 2 and summary["exported"] == 7
    assert sorted(_ids(tmp_path)) == list(range(1, 8)), "nothing twice, nothing missing"
    assert export.export_chat(client, "archive", tmp_path)["written"] == 0


def test_a_stopped_export_carries_on_where_it_stopped(
    client: TelegramClient, transport: FakeTransport, tmp_path: Path
) -> None:
    History(transport, 8)
    first = export.export_chat(client, "archive", tmp_path, maximum=3)
    assert first["written"] == 3 and first["complete"] is False
    second = export.export_chat(client, "archive", tmp_path)
    assert second["written"] == 5 and second["complete"] is True
    assert sorted(_ids(tmp_path)) == list(range(1, 9))


def test_an_interrupted_catch_up_does_not_leave_a_gap(
    client: TelegramClient, transport: FakeTransport, tmp_path: Path
) -> None:
    """Walking down to what is saved, the gap only closes at the bottom.

    If the run stops halfway, marking the newest message as saved would lose
    everything between it and the old newest for good.
    """
    history = History(transport, 4)
    export.export_chat(client, "archive", tmp_path)
    history.add(5, 6, 7, 8, 9)
    stopped = export.export_chat(client, "archive", tmp_path, maximum=2)
    assert stopped["written"] == 2 and stopped["newest_id"] == 4, "the gap is still open"
    resumed = export.export_chat(client, "archive", tmp_path)
    assert resumed["written"] == 3 and resumed["newest_id"] == 9
    assert sorted(_ids(tmp_path)) == list(range(1, 10))


def test_since_bounds_the_walk_and_a_wider_window_reopens_it(
    client: TelegramClient, transport: FakeTransport, tmp_path: Path
) -> None:
    history = History(transport, 0)
    for mid, day in [(5, 30), (4, 20), (3, 10), (2, 5), (1, 1)]:
        history.add(mid, date=day * 86400)
    narrow = export.export_chat(client, "archive", tmp_path, since=15 * 86400)
    assert narrow["complete"] is True and sorted(_ids(tmp_path)) == [4, 5]
    assert export.export_chat(client, "archive", tmp_path, since=25 * 86400)["written"] == 0
    wider = export.export_chat(client, "archive", tmp_path)
    assert wider["written"] == 3 and sorted(_ids(tmp_path)) == [1, 2, 3, 4, 5]


def test_media_is_saved_beside_the_records(
    client: TelegramClient, transport: FakeTransport, tmp_path: Path
) -> None:
    history = History(transport, 1)
    history.add(
        2,
        content={
            "@type": "messageDocument",
            "document": {"file_name": "../../etc/report.pdf", "document": {"id": 55}},
        },
    )
    cached = tmp_path / "tdlib-cache.bin"
    cached.write_bytes(b"%PDF-1.7")
    local = {"path": str(cached), "is_downloading_completed": True}
    transport.add_simple_response("downloadFile", {"@type": "file", "id": 55, "local": local})
    out = tmp_path / "export"
    summary = export.export_chat(client, "archive", out, media=True)
    assert summary["media_saved"] == 1
    record = next(r for r in _records(out) if r["message_id"] == 2)
    saved = out / record["media_path"]
    assert saved.read_bytes() == b"%PDF-1.7"
    assert saved.parent == out / export.MEDIA, "a file name must not climb out of media/"


@pytest.mark.parametrize(
    "chat,message",
    [({"has_protected_content": True}, {}), ({}, {"can_be_saved": False})],
    ids=["protected chat", "unsaveable message"],
)
def test_media_the_chat_forbids_saving_is_not_saved(
    client: TelegramClient,
    transport: FakeTransport,
    tmp_path: Path,
    chat: dict[str, Any],
    message: dict[str, Any],
) -> None:
    history = History(transport, 0, **chat)
    history.add(
        1,
        content={"@type": "messageVideo", "video": {"file_name": "v.mp4", "video": {"id": 9}}},
        **message,
    )
    summary = export.export_chat(client, "archive", tmp_path, media=True)
    assert summary["media_saved"] == 0 and summary["media_skipped"] == 1
    assert "protected" in _records(tmp_path)[0]["media_skipped"]
    assert not any(req.get("@type") == "downloadFile" for _, req in transport.sent)


def test_one_directory_holds_one_chat(
    client: TelegramClient, transport: FakeTransport, tmp_path: Path
) -> None:
    History(transport, 2)
    export.export_chat(client, "archive", tmp_path)
    state = json.loads((tmp_path / export.STATE).read_text())
    state["chat_id"] = 42
    (tmp_path / export.STATE).write_text(json.dumps(state))
    with pytest.raises(ValueError, match="its own --out"):
        export.export_chat(client, "archive", tmp_path)


def test_safe_names() -> None:
    assert export.safe_name("../../etc/passwd") == "_.._etc_passwd"
    assert export.safe_name("отчёт 2026.pdf") == "отчёт_2026.pdf"
    assert export.safe_name("") == "file"
