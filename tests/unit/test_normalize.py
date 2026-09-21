"""Normalization, entities, dates, paging, errors."""

from __future__ import annotations


def test_message_record_links_and_entities() -> None:
    from tdelegram import normalize

    message = {
        "chat_id": 1,
        "id": 2,
        "date": 1700000000,
        "sender_id": {"@type": "messageSenderUser", "user_id": 3},
        "content": {
            "@type": "messageText",
            "text": {
                "@type": "formattedText",
                "text": "hi link",
                "entities": [
                    {
                        "offset": 3,
                        "length": 4,
                        "type": {"@type": "textEntityTypeTextUrl", "url": "https://example.com"},
                    }
                ],
            },
        },
    }
    record = normalize.message_record(message)
    assert record["text"] == "hi link"
    assert record["links"] == ["https://example.com"]
    assert record["date"] is not None
    # include_raw pressure valve
    record_raw = normalize.message_record(message, include_raw=True)
    assert record_raw["raw"] == message
    assert "raw" not in record


def test_chat_record_username_fallback() -> None:
    from tdelegram import normalize

    chat = {
        "id": 5,
        "title": "t",
        "usernames": {"active_usernames": ["durov"]},
        "type": {"@type": "chatTypeSupergroup"},
    }
    assert normalize.chat_record(chat)["username"] == "durov"


def test_parse_entities_via_execute() -> None:
    from tdelegram.entities import parse_entities
    from tdelegram.transport import FakeTransport

    transport = FakeTransport()
    formatted = parse_entities(transport, "**hi**", "markdown")
    assert formatted["@type"] == "formattedText"
    assert "**" not in formatted["text"]


def test_render_entities_roundtrip() -> None:
    from tdelegram.entities import render_entities

    formatted = {
        "text": "hello",
        "entities": [{"offset": 0, "length": 5, "type": {"@type": "textEntityTypeBold"}}],
    }
    assert render_entities(formatted, "markdown") == "**hello**"


def test_parse_date_relative_and_iso() -> None:
    from tdelegram.dates import parse_date

    assert parse_date(None) is None
    assert isinstance(parse_date("7d"), int)
    assert parse_date("2024-01-02") == parse_date("2024-01-02T00:00:00+00:00")


def test_paginate_dedup_and_stop() -> None:
    from tdelegram.paging import paginate

    pages = [[{"id": 3}, {"id": 2}], [{"id": 2}, {"id": 1}]]

    def _fetch(cursor: int) -> tuple[list[dict], int | None]:
        if cursor >= len(pages):
            return [], None
        nxt = cursor + 1 if cursor + 1 < len(pages) else None
        return pages[cursor], nxt

    assert [i["id"] for i in paginate(_fetch)] == [3, 2, 1]


def test_forum_offset_triple_stops() -> None:
    from tdelegram.api import topics
    from tdelegram.client import TelegramClient
    from tdelegram.transport import FakeTransport

    transport = FakeTransport()

    def _topics(req: dict) -> dict:
        return {
            "@type": "forumTopics",
            "topics": [
                {"info": {"topic_id": 7}, "last_message_date": 1, "last_message": {"id": 9}}
            ],
        }

    transport.add_response(lambda r: r.get("@type") == "getForumTopics", _topics)
    transport.add_simple_response("searchPublicChat", {"@type": "chat", "id": 10})
    transport.add_simple_response("getChat", {"@type": "chat", "id": 10, "title": "t"})
    client = TelegramClient(transport)
    try:
        seen = list(topics.iter_topics(client, "10"))
        assert len(seen) == 1  # second page repeats triple -> stop
    finally:
        client.close()


def test_flood_retry_reads_only() -> None:
    from tdelegram.client import TelegramClient
    from tdelegram.transport import FakeTransport

    transport = FakeTransport()
    calls = {"n": 0}

    def _flaky(req: dict) -> dict:
        calls["n"] += 1
        if calls["n"] == 1:
            return {"@type": "error", "code": 429, "message": "FLOOD_WAIT retry after 0"}
        return {"@type": "user", "id": 1}

    transport.add_response(lambda r: r.get("@type") == "getMe", _flaky)
    client = TelegramClient(transport)
    try:
        assert client.call("getMe", {})["@type"] == "user"
        assert calls["n"] == 2
    finally:
        client.close()


def test_flood_never_retries_writes() -> None:
    import pytest

    from tdelegram.client import TelegramClient
    from tdelegram.errors import FloodWait
    from tdelegram.transport import FakeTransport

    transport = FakeTransport()
    transport.add_response(
        lambda r: r.get("@type") == "sendMessage",
        {"@type": "error", "code": 429, "message": "FLOOD_WAIT retry after 5"},
    )
    client = TelegramClient(transport)
    try:
        with pytest.raises(FloodWait):
            client.call("sendMessage", {"chat_id": 1}, allow_write=True)
    finally:
        client.close()
