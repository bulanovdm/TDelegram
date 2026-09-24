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


def test_chat_record_takes_names_and_counts_from_the_detail() -> None:
    from tdelegram import normalize

    chat = {"id": 5, "title": "t", "type": {"@type": "chatTypeSupergroup", "supergroup_id": 5}}
    supergroup = {
        "@type": "supergroup",
        "usernames": {"active_usernames": ["durov"]},
        "member_count": 0,
        "is_forum": False,
    }
    record = normalize.chat_record(chat, detail=supergroup)
    assert record["username"] == "durov"
    assert record["member_count"] is None, "0 means unknown for a supergroup, not empty"
    bare = normalize.chat_record(chat)
    assert bare["username"] is None and bare["usernames"] == []


def _message(**fields: object) -> dict:
    base: dict = {"@type": "message", "id": 9, "chat_id": -100, "date": 1700000000}
    base.update(fields)
    return base


def test_media_records_carry_the_file_id_download_needs() -> None:
    """Regression: records had no file id, so `media download` had nothing to take."""
    from tdelegram import normalize

    photo = _message(
        content={
            "@type": "messagePhoto",
            "photo": {
                "sizes": [
                    {"type": "s", "width": 90, "height": 90, "photo": {"id": 1, "size": 900}},
                    {"type": "y", "width": 1280, "height": 960, "photo": {"id": 2, "size": 90000}},
                ]
            },
            "caption": {"text": "sunset"},
        }
    )
    media = normalize.message_record(photo)["media"]
    assert media == {"kind": "photo", "file_id": 2, "size": 90000, "width": 1280, "height": 960}

    document = _message(
        content={
            "@type": "messageDocument",
            "document": {
                "file_name": "cv.pdf",
                "mime_type": "application/pdf",
                "document": {"id": 7, "size": 0, "expected_size": 1234},
            },
        }
    )
    record = normalize.message_record(document)
    assert record["media"]["file_id"] == 7 and record["media"]["size"] == 1234
    assert record["file_name"] == "cv.pdf", "the old top-level field still works"


def test_a_voice_note_file_is_found_and_its_transcript_kept() -> None:
    """A voice note keeps its file under `voice`, which the old walk never looked at."""
    from tdelegram import normalize

    voice = _message(
        content={
            "@type": "messageVoiceNote",
            "voice_note": {
                "duration": 14,
                "mime_type": "audio/ogg",
                "voice": {"id": 31, "size": 2048},
                "speech_recognition_result": {
                    "@type": "speechRecognitionResultText",
                    "text": "running ten minutes late",
                },
            },
        }
    )
    media = normalize.message_record(voice)["media"]
    assert media["kind"] == "voice_note" and media["file_id"] == 31
    assert media["transcript"] == "running ten minutes late"


def test_engagement_and_provenance_for_research() -> None:
    from tdelegram import normalize

    post = _message(
        is_channel_post=True,
        edit_date=1700000500,
        media_album_id="6012345678",
        author_signature="Desk",
        interaction_info={
            "view_count": 15000,
            "forward_count": 42,
            "reply_info": {"reply_count": 7},
            "reactions": {
                "reactions": [
                    {"type": {"@type": "reactionTypeEmoji", "emoji": "👍"}, "total_count": 90},
                    {"type": {"@type": "reactionTypeCustomEmoji", "custom_emoji_id": "55"},
                     "total_count": 3},
                    {"type": {"@type": "reactionTypePaid"}, "total_count": 1},
                ]
            },
        },
        forward_info={
            "date": 1690000000,
            "origin": {"@type": "messageOriginChannel", "chat_id": -1009, "message_id": 77},
        },
        content={"@type": "messageText", "text": {"text": "news"}},
    )
    record = normalize.message_record(post)
    assert (record["views"], record["forwards"], record["replies"]) == (15000, 42, 7)
    assert record["reactions"] == [
        {"reaction": "👍", "count": 90},
        {"reaction": "custom:55", "count": 3},
        {"reaction": "paid", "count": 1},
    ]
    origin = record["forwarded_from"]
    assert origin["type"] == "channel" and origin["message_id"] == 77
    assert origin["date"].startswith("2023-")
    assert record["album_id"] == "6012345678" and record["author_signature"] == "Desk"
    assert record["edit_date"] is not None
    plain = normalize.message_record(_message(content={"@type": "messageText"}))
    assert plain["views"] is None and plain["reactions"] == [] and plain["album_id"] is None


def test_inline_buttons_are_listed() -> None:
    from tdelegram import normalize

    message = _message(
        reply_markup={
            "@type": "replyMarkupInlineKeyboard",
            "rows": [
                [
                    {"text": "Yes", "type": {"@type": "inlineKeyboardButtonTypeCallback",
                                             "data": "eWVz"}},
                    {"text": "Site", "type": {"@type": "inlineKeyboardButtonTypeUrl",
                                              "url": "https://example.org"}},
                ]
            ],
        }
    )
    assert normalize.message_record(message)["buttons"] == [
        {"text": "Yes", "type": "callback"},
        {"text": "Site", "type": "url", "url": "https://example.org"},
    ]


def test_display_names() -> None:
    from tdelegram import normalize

    assert normalize.display_name({"first_name": "Ada", "last_name": "Lovelace"}) == "Ada Lovelace"
    assert normalize.display_name({"usernames": {"active_usernames": ["ada"]}}) == "@ada"
    assert normalize.display_name({"type": {"@type": "userTypeDeleted"}}) == "Deleted Account"
    assert normalize.display_name({}) is None


def test_parse_entities_via_execute() -> None:
    from tdelegram.entities import parse_entities
    from tdelegram.transport import FakeTransport

    transport = FakeTransport()
    formatted = parse_entities(transport, "**hi**", "markdown")
    assert formatted["@type"] == "formattedText"
    assert "**" not in formatted["text"]


def test_render_entities_uses_markdown_v2() -> None:
    """parse_entities asks TDLib for MarkdownV2, so the renderer must match it.

    This previously asserted `**hello**`, which V2 parses as plain text with
    no entity at all -- the renderer's output could not be read back.
    """
    from tdelegram.entities import render_entities

    def rendered(etype: str, mode: str = "markdown") -> str:
        return render_entities(
            {"text": "hello", "entities": [{"offset": 0, "length": 5, "type": {"@type": etype}}]},
            mode,
        )

    assert rendered("textEntityTypeBold") == "*hello*"
    assert rendered("textEntityTypeItalic") == "_hello_"
    assert rendered("textEntityTypeUnderline") == "__hello__"
    assert rendered("textEntityTypeStrikethrough") == "~hello~"
    assert rendered("textEntityTypeCode") == "`hello`"
    assert rendered("textEntityTypeBold", "html") == "<b>hello</b>"
    assert rendered("textEntityTypeUnderline", "html") == "<u>hello</u>"


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
                {"info": {"forum_topic_id": 7}, "last_message_date": 1, "last_message": {"id": 9}}
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


def test_rich_message_text_is_not_lost() -> None:
    """Regression: instant-view posts flattened to an empty string.

    `messageRichMessage` keeps its words in nested page blocks, so a sweep
    filtering on `text` dropped whole posts with no sign anything was skipped.
    """
    from tdelegram import normalize

    message = {
        "content": {
            "@type": "messageRichMessage",
            "message": {
                "@type": "richMessage",
                "blocks": [
                    {"@type": "pageBlockTitle", "text": {"@type": "richTextPlain",
                                                         "text": "Senior Go Engineer"}},
                    {"@type": "pageBlockParagraph", "text": {
                        "@type": "richTexts",
                        "texts": [
                            {"@type": "richTextPlain", "text": "Remote,"},
                            {"@type": "richTextBold", "text": {"@type": "richTextPlain",
                                                               "text": "$5000"}},
                        ],
                    }},
                ],
            },
        }
    }
    text = normalize.message_text(message)
    assert "Senior Go Engineer" in text
    assert "$5000" in text, "nested rich text must be reached, not just the top level"


def test_text_bearing_contents_are_not_lost() -> None:
    """Gift, premium-code and poll-option contents carry their own `text`."""
    from tdelegram import normalize

    for content_type in ("messageGift", "messageGiftedPremium", "messagePollOptionAdded"):
        message = {
            "content": {
                "@type": content_type,
                "text": {"@type": "formattedText", "text": "happy birthday", "entities": []},
            }
        }
        assert normalize.message_text(message) == "happy birthday", content_type


def test_captions_still_win_when_there_is_no_text() -> None:
    from tdelegram import normalize

    message = {
        "content": {
            "@type": "messageDocument",
            "caption": {"@type": "formattedText", "text": "see attached", "entities": []},
        }
    }
    assert normalize.message_text(message) == "see attached"
