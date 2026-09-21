"""Broad coverage: every module exercised on FakeTransport (no TDLib)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def _client_with(**rules: Any):
    from tdelegram.client import TelegramClient
    from tdelegram.transport import FakeTransport

    transport = FakeTransport()
    for method, response in rules.items():
        transport.add_simple_response(method, response)
    client = TelegramClient(transport)
    return client, transport


def test_errors_mapping() -> None:
    from tdelegram.errors import (
        AuthError,
        FloodWait,
        InvalidRequest,
        NotFoundError,
        RetryPolicy,
        TelegramError,
        TelegramPermissionError,
        from_td_error,
        parse_retry_after,
        should_retry_read,
    )

    assert isinstance(from_td_error({"code": 401, "message": "x"}), AuthError)
    assert isinstance(from_td_error({"code": 403, "message": "x"}), TelegramPermissionError)
    assert isinstance(from_td_error({"code": 404, "message": "x"}), NotFoundError)
    assert isinstance(from_td_error({"code": 400, "message": "x"}), InvalidRequest)
    assert isinstance(
        from_td_error({"code": 429, "message": "FLOOD_WAIT retry after 3"}), FloodWait
    )
    assert isinstance(from_td_error({"code": 420, "message": "SLOWMODE_WAIT wait 2"}), FloodWait)
    assert isinstance(from_td_error({"code": 500, "message": "boom"}), TelegramError)
    assert parse_retry_after("retry after 7") == 7
    assert parse_retry_after("please wait 9 seconds") == 9
    assert parse_retry_after("no hint") == 0
    policy = RetryPolicy()
    assert should_retry_read(FloodWait(429, "flood", "m", 1), 0, policy)
    assert not should_retry_read(FloodWait(429, "flood", "m", 1), 99, policy)
    assert not should_retry_read(TelegramError(500, "x"), 0, policy)
    assert policy.delay_for(0, 5) == 5.0
    from tdelegram.errors import sleep_retry

    sleep_retry(0.0)


def test_credentials_chain(tmp_path: Path, monkeypatch: Any) -> None:
    from tdelegram import credentials as creds

    # explicit wins
    assert creds.resolve_secret(account="a", env_name="NOPE_X", explicit="v", save=False) == "v"
    # env wins
    monkeypatch.setenv("TDELEGRAM_TEST_SECRET_X", "envval")
    assert (
        creds.resolve_secret(account="a", env_name="TDELEGRAM_TEST_SECRET_X", save=False)
        == "envval"
    )
    # prompt + file fallback when OS store unavailable
    monkeypatch.delenv("TDELEGRAM_TEST_SECRET_X")
    monkeypatch.setattr(creds, "os_store_get", lambda s, a: None)
    monkeypatch.setattr(creds, "os_store_set", lambda s, a, v: False)
    value = creds.resolve_secret(
        account="promptacct",
        env_name="TDELEGRAM_TEST_SECRET_X",
        base_dir=tmp_path,
        prompt_fn=lambda p, s: "  typed  ",
        save=True,
    )
    assert value == "typed"
    assert creds.file_get(tmp_path, "promptacct") == "typed"
    # file hit on second resolve (no prompt)
    value2 = creds.resolve_secret(
        account="promptacct",
        env_name="TDELEGRAM_TEST_SECRET_X",
        base_dir=tmp_path,
        prompt_fn=lambda p, s: (_ for _ in ()).throw(AssertionError("should not prompt")),
    )
    assert value2 == "typed"
    # empty rejected
    import pytest

    with pytest.raises(RuntimeError):
        creds.resolve_secret(
            account="e",
            env_name="NOPE_X",
            base_dir=tmp_path,
            prompt_fn=lambda p, s: "  ",
            save=False,
        )

    # EOF mapping
    def _eof(p: str, s: bool) -> str:
        raise EOFError

    with pytest.raises(RuntimeError):
        creds.resolve_secret(account="e", env_name="NOPE_X", prompt_fn=_eof, save=False)
    # default_prompt EOF path
    monkeypatch.setattr("builtins.input", lambda p: (_ for _ in ()).throw(EOFError))
    with pytest.raises(RuntimeError):
        creds.default_prompt("p:", False)
    assert creds.file_secret_path(tmp_path, "a/b").name == "a_b.secret"
    assert creds.file_get(tmp_path, "missing") is None
    # os store getters return None gracefully on unknown platform
    monkeypatch.setattr(creds.sys, "platform", "plan9")
    assert creds.os_store_get("s", "a") is None
    assert creds.os_store_set("s", "a", "v") is False


def test_config_discovery(tmp_path: Path, monkeypatch: Any) -> None:
    from tdelegram import config

    sentinel = tmp_path / "libtdjson.so"
    sentinel.write_text("x")
    assert config.discover_library(explicit=str(sentinel)) == str(sentinel)
    monkeypatch.setenv("TDELEGRAM_TDJSON", str(sentinel))
    assert config.discover_library() == str(sentinel)
    monkeypatch.delenv("TDELEGRAM_TDJSON")
    monkeypatch.setattr("ctypes.util.find_library", lambda name: "/found/libtdjson.so")
    assert config.discover_library() == "/found/libtdjson.so"
    monkeypatch.setattr("ctypes.util.find_library", lambda name: None)
    monkeypatch.setattr(config.platform, "system", lambda: "UnknownOS9")
    try:
        config.discover_library()
        raise AssertionError("should raise")
    except RuntimeError as exc:
        assert "Could not find libtdjson" in str(exc)
    assert config.default_base_dir().name == ".tdelegram"
    cfg = config.ClientConfig(api_id=1, api_hash="h", profile="p", base_dir=tmp_path)
    assert cfg.session_dir == tmp_path / "profiles" / "p"
    assert cfg.db_dir.name == "tdlib"
    # session lock
    lock = config.SessionLock(tmp_path / "sess")
    lock.acquire()
    lock2 = config.SessionLock(tmp_path / "sess")
    try:
        lock2.acquire()
        raise AssertionError("second acquire should fail")
    except RuntimeError as exc:
        assert "Another tdelegram" in str(exc)
    finally:
        lock.release()
        lock.release()


def test_transport_fake_details() -> None:
    from tdelegram.transport import FakeTransport

    transport = FakeTransport()
    assert transport.create_client_id() != transport.create_client_id()
    assert transport.receive(timeout=0.01) is None
    # callable response + matcher exception tolerance
    transport.add_response(
        lambda r: 1 / 0 if False else r.get("@type") == "m1", lambda req: {"@type": "r1"}
    )
    transport.add_response(lambda r: (_ for _ in ()).throw(ValueError()), {"@type": "never"})
    transport.send(1, json.dumps({"@type": "m1"}))
    assert json.loads(transport.receive(1.0) or "")["@type"] == "r1"
    # fallback ok
    transport.send(1, json.dumps({"@type": "unknownMethod9"}))
    assert json.loads(transport.receive(1.0) or "")["@type"] == "ok"
    assert transport.sent
    # execute branches
    assert (
        json.loads(
            transport.execute(
                json.dumps(
                    {
                        "@type": "parseTextEntities",
                        "text": "<b>hi</b>",
                        "parse_mode": {"@type": "textParseModeHTML"},
                    }
                )
            )
            or ""
        )["text"]
        == "hi"
    )
    assert (
        json.loads(
            transport.execute(
                json.dumps(
                    {
                        "@type": "parseTextEntities",
                        "text": "**hi**",
                        "parse_mode": {"@type": "textParseModeMarkdown"},
                    }
                )
            )
            or ""
        )["text"]
        == "hi"
    )
    assert json.loads(transport.execute(json.dumps({"@type": "other"})) or "")["@type"] == "ok"


def test_loop_extras() -> None:
    import time

    from tdelegram.loop import abandoned_count, loop_for, pending_count, reset_loops
    from tdelegram.transport import FakeTransport

    transport = FakeTransport()
    loop = loop_for(transport)
    assert pending_count(loop) == 0 and abandoned_count(loop) == 0
    loop.start()
    loop.start()  # idempotent
    sub = loop.ensure_client(7)
    assert loop.ensure_client(7) is sub
    # invalid JSON ignored
    transport._inbox.put("not-json{{{")
    # non-dict ignored
    transport._inbox.put("[1,2]")
    # broadcast without @client_id reaches subscriber
    transport._inbox.put(json.dumps({"@type": "updateX"}))
    time.sleep(0.3)
    # handler exception tolerated
    sub.handlers.append(lambda e: (_ for _ in ()).throw(RuntimeError("boom")))
    transport.add_update({"@type": "updateY"}, client_id=7)
    time.sleep(0.5)
    loop.stop()
    # loop_for shared + reset
    assert loop_for(transport) is loop
    reset_loops()
    assert loop_for(transport) is not loop


def test_client_extras() -> None:
    from tdelegram.client import TelegramClient
    from tdelegram.transport import FakeTransport

    transport = FakeTransport()
    client = TelegramClient(transport)
    try:
        assert client.transport is transport
        assert isinstance(client.client_id, int)
        client.disable_retry()
        # execute paths
        assert client.execute({"@type": "parseTextEntities", "text": "hi"})["@type"] in (
            "ok",
            "formattedText",
        )
        # on_update subscribe/unsubscribe
        seen: list[dict] = []
        sub = client.on_update(seen.append)
        transport.add_update({"@type": "updateNewMessage", "x": 1}, client_id=client.client_id)
        import time

        time.sleep(0.5)
        assert seen
        sub.unsubscribe()
        sub.unsubscribe()
        assert client.dropped_updates() >= 0
        # send_request low-level
        assert client.send_request({"@type": "getMe"})["@type"] == "ok"
        # context manager + double close
        with client as c:
            assert c is client
        client.close()
        client.close()
    finally:
        try:
            client.close()
        except Exception:
            pass
    from tdelegram.loop import reset_loops

    reset_loops()


def test_client_execute_errors() -> None:
    import json as _json

    import pytest

    from tdelegram.client import TelegramClient
    from tdelegram.errors import TelegramError
    from tdelegram.transport import FakeTransport

    class EmptyExec(FakeTransport):
        def execute(self, request: str) -> str | None:
            return None

    client = TelegramClient(EmptyExec())
    try:
        with pytest.raises(TelegramError):
            client.execute({"@type": "x"})
    finally:
        client.close()

    class BadExec(FakeTransport):
        def execute(self, request: str) -> str | None:
            return "not json"

    client2 = TelegramClient(BadExec())
    try:
        with pytest.raises(TelegramError):
            client2.execute({"@type": "x"})
    finally:
        client2.close()

    class ErrExec(FakeTransport):
        def execute(self, request: str) -> str | None:
            return _json.dumps({"@type": "error", "code": 400, "message": "bad"})

    client3 = TelegramClient(ErrExec())
    try:
        with pytest.raises(TelegramError):
            client3.execute({"@type": "x"})
    finally:
        client3.close()
    from tdelegram.loop import reset_loops

    reset_loops()


def test_normalize_extras() -> None:
    from tdelegram import normalize

    assert normalize.iso_date("nope") is None
    assert normalize.iso_date(10**20) is None
    assert normalize.formatted_text(None) == ""
    assert normalize.entities_of(None) == []
    assert normalize.entities_of({"entities": "nope"}) == []
    # url entity type
    value = {
        "text": "visit here",
        "entities": [{"offset": 6, "length": 4, "type": {"@type": "textEntityTypeUrl"}}],
    }
    assert normalize.links_of(value) == ["here"]
    assert (
        normalize.links_of(
            {
                "text": "x",
                "entities": [{"offset": 99, "length": 5, "type": {"@type": "textEntityTypeUrl"}}],
            }
        )
        == []
    )
    assert normalize.topic_id_of({}) is None
    assert normalize.sender_of({}) is None
    assert normalize.message_text({"content": {"@type": "messageX"}}) == ""
    assert normalize.message_entities({"content": {"@type": "messageX"}}) == []
    assert normalize.message_links({"content": {"@type": "messageX"}}) == []
    user = {
        "id": 1,
        "first_name": "a",
        "type": {"@type": "userTypeBot"},
        "status": {"@type": "userStatusOnline"},
    }
    rec = normalize.user_record(user)
    assert rec["is_bot"] is True and rec["status"] == "userStatusOnline"
    user2 = {"id": 2, "username": "u", "type": "x", "status": "x"}
    assert normalize.user_record(user2)["username"] == "u"
    assert normalize.user_record(user, include_raw=True)["raw"] == user
    assert normalize.chat_record({"id": 1}, include_raw=True)["raw"] == {"id": 1}
    f = {
        "id": 9,
        "size": 3,
        "local": {"path": "/tmp/x", "is_downloading_active": False, "downloaded_size": 3},
        "remote": {"id": "r"},
    }
    assert normalize.file_record(f)["path"] == "/tmp/x"


def test_entities_extras() -> None:
    import pytest

    from tdelegram.entities import parse_entities, render_entities
    from tdelegram.transport import FakeTransport

    with pytest.raises(ValueError):
        parse_entities(FakeTransport(), "hi", "bbcode")

    class NoneExec(FakeTransport):
        def execute(self, request: str) -> str | None:
            return None

    assert parse_entities(NoneExec(), "hi", "markdown")["text"] == "hi"

    class BadExec(FakeTransport):
        def execute(self, request: str) -> str | None:
            return "[[["

    assert parse_entities(BadExec(), "hi", "markdown")["text"] == "hi"

    class ErrExec(FakeTransport):
        def execute(self, request: str) -> str | None:
            return json.dumps({"@type": "error", "code": 400, "message": "bad"})

    assert parse_entities(ErrExec(), "hi", "html")["text"] == "hi"
    assert render_entities({"text": "hi", "entities": []}) == "hi"
    formatted: dict[str, Any] = {
        "text": "hello world",
        "entities": [
            {"offset": 0, "length": 5, "type": {"@type": "textEntityTypeItalic"}},
            {"offset": 6, "length": 5, "type": {"@type": "textEntityTypeCode"}},
            {"offset": 0, "length": 5, "type": {"@type": "textEntityTypePre"}},
            {
                "offset": 0,
                "length": 5,
                "type": {"@type": "textEntityTypeTextUrl", "url": "https://e.com"},
            },
            {"offset": 0, "length": 5, "type": {"@type": "textEntityTypeMention"}},
            {"offset": -1, "length": 5, "type": {"@type": "textEntityTypeBold"}},
            {"offset": 0, "length": 99, "type": {"@type": "textEntityTypeBold"}},
            {"offset": "x", "length": 1, "type": {"@type": "textEntityTypeBold"}},
            {"offset": 0, "length": 1, "type": {"@type": "textEntityTypeUnknown"}},
        ],
    }
    out = render_entities(formatted, "html")
    assert "<i>" in out and "<code>" in out


def test_dates_invalid() -> None:
    import pytest

    from tdelegram.dates import parse_date

    with pytest.raises(ValueError):
        parse_date("not-a-date")


def test_paging_helpers() -> None:
    from tdelegram.paging import history_pages, search_pages

    client, _ = _client_with(
        getChatHistory={"@type": "messages", "messages": [{"id": 5}, {"id": 4}]},
        searchChatMessages={
            "@type": "messages",
            "messages": [{"id": 6}],
            "next_from_message_id": 0,
        },
        getChat={"@type": "chat", "id": 1},
        searchPublicChat={"@type": "chat", "id": 1},
    )
    try:
        fetch = history_pages(client, 1)
        items, nxt = fetch(0)
        assert items and nxt == 4
        fetch2 = search_pages(client, 1, "q")
        items2, _ = fetch2(0)
        assert items2
    finally:
        client.close()
    from tdelegram.loop import reset_loops

    reset_loops()


def test_api_chats() -> None:
    from tdelegram.api import chats

    client, _ = _client_with(
        getChats={"@type": "chats", "chat_ids": [1, 1, 2]},
        getChat={"@type": "chat", "id": 1, "title": "t"},
        searchPublicChat={"@type": "chat", "id": 3, "title": "u"},
        createNewSupergroupChat={"@type": "chat", "id": 9},
        createNewBasicGroupChat={"@type": "chat", "id": 8},
        joinChat={"@type": "ok"},
        joinChatByInviteLink={"@type": "chat", "id": 1},
        leaveChat={"@type": "ok"},
        addChatToList={"@type": "ok"},
        toggleChatIsPinned={"@type": "ok"},
        openChat={"@type": "ok"},
        getChatHistory={"@type": "messages", "messages": [{"id": 1}]},
        viewMessages={"@type": "ok"},
        setChatNotificationSettings={"@type": "ok"},
        createChatInviteLink={"@type": "chatInviteLink", "invite_link": "x"},
        getSupergroupMembers={"@type": "chatMembers", "members": []},
    )
    try:
        assert chats.resolve_id(client, "123") == 1
        assert chats.resolve_id(client, "@user") == 3
        try:
            chats.resolve(client, "   ")
            raise AssertionError()
        except ValueError:
            pass
        assert chats.info(client, "123")["chat_id"] == 1
        assert len(chats.list_all(client, scope="main", maximum=5)) >= 1
        assert chats.create_supergroup(client, "t", allow_write=True)["id"] == 9
        assert chats.create_basic_group(client, [1], "t", allow_write=True)["id"] == 8
        assert chats.join(client, "123", allow_write=True)["@type"] == "ok"
        assert chats.join_by_invite(client, "https://t.me/x", allow_write=True)["id"] == 1
        assert chats.leave(client, "123", allow_write=True, allow_destructive=True)["@type"] == "ok"
        assert chats.archive(client, "123", allow_write=True)["@type"] == "ok"
        assert chats.set_pinned(client, "123", allow_write=True)["@type"] == "ok"
        assert chats.mark_read(client, "123", allow_write=True)["chat_id"] == 1
        assert chats.set_notification_mute(client, "123", allow_write=True)["@type"] == "ok"
        assert chats.invite_link(client, "123", allow_write=True)["invite_link"] == "x"
        assert chats.members(client, "123")["@type"] == "chatMembers"
        assert list(chats.iter_all(client))
    finally:
        client.close()
    from tdelegram.loop import reset_loops

    reset_loops()


def test_api_messages() -> None:
    from tdelegram.api import messages

    client, _ = _client_with(
        getChat={"@type": "chat", "id": 1},
        getMessage={"@type": "message", "id": 5, "chat_id": 1},
        getChatHistory={
            "@type": "messages",
            "messages": [
                {
                    "id": 5,
                    "date": 1700000000,
                    "content": {"@type": "messageText", "text": {"text": "hello"}},
                }
            ],
        },
        sendMessage={"@type": "message", "id": 6},
        editMessageText={"@type": "message", "id": 5},
        deleteMessages={"@type": "ok"},
        forwardMessages={"@type": "messages", "messages": []},
        pinChatMessage={"@type": "ok"},
        unpinChatMessage={"@type": "ok"},
        getMessageLink={"@type": "messageLink", "link": "https://t.me/x"},
        addMessageReaction={"@type": "ok"},
    )
    try:
        assert messages.get(client, "1", 5)["message_id"] == 5
        assert len(messages.history(client, "1", maximum=5)) == 1
        assert messages.send(client, "1", "hi", allow_write=True)["id"] == 6
        assert (
            messages.send(
                client, "1", "**hi**", parse_mode="markdown", reply_to=5, allow_write=True
            )["id"]
            == 6
        )
        assert messages.reply(client, "1", 5, "yo", allow_write=True)["id"] == 6
        assert messages.edit(client, "1", 5, "new", allow_write=True)["id"] == 5
        assert (
            messages.edit(client, "1", 5, "**n**", parse_mode="markdown", allow_write=True)["id"]
            == 5
        )
        assert (
            messages.delete(client, "1", [5], allow_write=True, allow_destructive=True)["@type"]
            == "ok"
        )
        assert messages.forward(client, "1", "1", [5], allow_write=True)["@type"] == "messages"
        assert messages.pin(client, "1", 5, allow_write=True)["@type"] == "ok"
        assert messages.unpin(client, "1", allow_write=True)["@type"] == "ok"
        assert messages.link(client, "1", 5)["@type"] == "messageLink"
        assert messages.react(client, "1", 5, "👍", allow_write=True)["@type"] == "ok"
        # filtered history
        assert messages.history(client, "1", sender_id=999, maximum=5) == []
        assert messages.history(client, "1", contains=["zzz"], maximum=5) == []
    finally:
        client.close()
    from tdelegram.loop import reset_loops

    reset_loops()


def test_api_media_users_misc() -> None:
    from tdelegram.api import contacts, users
    from tdelegram.api import media as media_api
    from tdelegram.api import search as search_api

    client, _ = _client_with(
        getChat={"@type": "chat", "id": 1},
        getMessage={
            "@type": "message",
            "id": 5,
            "content": {
                "document": {"file_name": "a.pdf", "document": {"@type": "file", "id": 77}}
            },
        },
        downloadFile={
            "@type": "file",
            "id": 77,
            "local": {"path": "/tmp/a", "is_downloading_completed": True},
        },
        sendMessage={"@type": "message", "id": 6},
        getUser={"@type": "user", "id": 2},
        getMe={"@type": "user", "id": 1},
        getUserFullInfo={"@type": "userFullInfo"},
        searchContacts={"@type": "users", "user_ids": []},
        getContacts={"@type": "users", "user_ids": [1]},
        addContact={"@type": "ok"},
        importContacts={"@type": "importedContacts"},
        removeContacts={"@type": "ok"},
        searchMessages={"@type": "messages", "messages": [], "next_from_message_id": 0},
        searchChatMessages={"@type": "messages", "messages": [], "next_from_message_id": 0},
        searchPublicChats={"@type": "chats", "chat_ids": []},
    )
    try:
        assert media_api.download(client, 77)["id"] == 77
        assert media_api.download_message_file(client, "1", 5)["id"] == 77
        assert media_api.send_photo(client, "1", "/tmp/x.jpg", allow_write=True)["id"] == 6
        assert media_api.send_video(client, "1", "/tmp/x.mp4", allow_write=True)["id"] == 6
        assert media_api.send_voice(client, "1", "/tmp/x.ogg", allow_write=True)["id"] == 6
        assert media_api.send_sticker(client, "1", 9, allow_write=True)["id"] == 6
        assert media_api.upload(client, "1", "/tmp/x.jpg")["status"] == "pending"
        assert users.get_user(client, 2)["user_id"] == 2
        assert users.me(client)["user_id"] == 1
        assert users.full_info(client, 2)["@type"] == "userFullInfo"
        assert users.search_users(client, "q")["@type"] == "users"
        assert contacts.list_contacts(client)["@type"] == "users"
        assert contacts.add_contact(client, 2, allow_write=True)["@type"] == "ok"
        assert (
            contacts.import_contacts(client, ["+1000"], allow_write=True)["@type"]
            == "importedContacts"
        )
        assert (
            contacts.remove_contacts(client, [2], allow_write=True, allow_destructive=True)["@type"]
            == "ok"
        )
        assert search_api.search_messages(client, "q", chat_ref="1")["@type"] == "messages"
        assert search_api.search_messages(client, "q")["@type"] == "messages"
        assert search_api.search_public_chats(client, "q")["@type"] == "chats"
        assert list(search_api.iter_global_search(client, "q", maximum=5)) == []
        try:
            media_api.download_message_file(client, "1", 5 if False else 5)
        except Exception:
            pass
    finally:
        client.close()
    from tdelegram.loop import reset_loops

    reset_loops()


def test_api_admin_account_topics_reactions() -> None:
    from tdelegram.api import account, admin, drafts, folders, polls, topics
    from tdelegram.api import reactions as rx

    client, _ = _client_with(
        getChat={"@type": "chat", "id": 1},
        setChatMemberStatus={"@type": "ok"},
        banChatMember={"@type": "ok"},
        setChatSlowModeDelay={"@type": "ok"},
        createChatInviteLink={"@type": "chatInviteLink"},
        revokeChatInviteLink={"@type": "ok"},
        getMe={"@type": "user", "id": 1},
        getUserFullInfo={"@type": "userFullInfo"},
        setName={"@type": "ok"},
        setBio={"@type": "ok"},
        getActiveSessions={"@type": "sessions", "sessions": []},
        terminateSession={"@type": "ok"},
        terminateAllOtherSessions={"@type": "ok"},
        getUserPrivacySettingRules={"@type": "userPrivacySettingRules"},
        getForumTopics={"@type": "forumTopics", "topics": []},
        createForumTopic={"@type": "forumTopic", "info": {}},
        editForumTopic={"@type": "ok"},
        toggleForumTopicIsClosed={"@type": "ok"},
        deleteForumTopic={"@type": "ok"},
        addMessageReaction={"@type": "ok"},
        removeMessageReaction={"@type": "ok"},
        getMessageAddedReactions={"@type": "messageReactions"},
        setPollAnswer={"@type": "ok"},
        stopPoll={"@type": "ok"},
        getPollVoters={"@type": "messageSenders", "senders": []},
        setChatDraftMessage={"@type": "ok"},
        clearAllDraftMessages={"@type": "ok"},
        getChatFolders={"@type": "chatFolders"},
        getChatFolder={"@type": "chatFolder"},
        createChatFolder={"@type": "chatFolderInfo"},
        deleteChatFolder={"@type": "ok"},
    )
    ok = {"allow_write": True, "allow_destructive": True}
    try:
        assert admin.promote(client, "1", 2, **ok)["@type"] == "ok"
        assert admin.demote(client, "1", 2, **ok)["@type"] == "ok"
        assert admin.ban(client, "1", 2, **ok)["@type"] == "ok"
        assert admin.restrict(client, "1", 2, **ok)["@type"] == "ok"
        assert admin.unrestrict(client, "1", 2, **ok)["@type"] == "ok"
        assert admin.slowmode(client, "1", 10, allow_write=True)["@type"] == "ok"
        assert admin.create_invite(client, "1", allow_write=True)["@type"] == "chatInviteLink"
        assert (
            admin.revoke_invite(
                client, "1", "https://t.me/x", allow_write=True, allow_destructive=True
            )["@type"]
            == "ok"
        )
        assert account.info(client)["@type"] == "user"
        assert account.full_info(client, 2)["@type"] == "userFullInfo"
        assert (
            account.set_profile(client, first_name="a", bio="b", allow_write=True)["name"]["@type"]
            == "ok"
        )
        assert account.sessions(client)["@type"] == "sessions"
        assert (
            account.terminate_session(client, 3, allow_write=True, allow_destructive=True)["@type"]
            == "ok"
        )
        assert (
            account.terminate_others(client, allow_write=True, allow_destructive=True)["@type"]
            == "ok"
        )
        assert topics.list_topics(client, "1")["@type"] == "forumTopics"
        assert topics.create_topic(client, "1", "n", allow_write=True)["@type"] == "forumTopic"
        assert topics.edit_topic(client, "1", 7, "n", allow_write=True)["@type"] == "ok"
        assert topics.close_topic(client, "1", 7, allow_write=True)["@type"] == "ok"
        assert (
            topics.delete_topic(client, "1", 7, allow_write=True, allow_destructive=True)["@type"]
            == "ok"
        )
        assert rx.add_reaction(client, "1", 5, "👍", allow_write=True)["@type"] == "ok"
        assert rx.remove_reaction(client, "1", 5, "👍", allow_write=True)["@type"] == "ok"
        assert rx.added_reactions(client, "1", 5)["@type"] == "messageReactions"
        assert polls.answer_poll(client, "1", 5, [0], allow_write=True)["@type"] == "ok"
        assert polls.stop_poll(client, "1", 5, allow_write=True)["@type"] == "ok"
        assert polls.poll_voters(client, "1", 5, 0)["@type"] == "messageSenders"
        assert drafts.set_draft(client, "1", "hi", allow_write=True)["@type"] == "ok"
        cleared = drafts.clear_drafts(client, allow_write=True, allow_destructive=True)
        assert cleared["@type"] == "ok"
        assert folders.list_folders(client)["@type"] in ("chatFolders", "chatFolder", "ok")
        created = folders.create_folder(client, "f", [1], allow_write=True)
        assert created["@type"] == "chatFolderInfo"
        assert (
            folders.delete_folder(client, 1, allow_write=True, allow_destructive=True)["@type"]
            == "ok"
        )
    finally:
        client.close()
    from tdelegram.loop import reset_loops

    reset_loops()


def test_api_bots_proxies_updates_files() -> None:
    from tdelegram.api import bots, proxies, secret, stories
    from tdelegram.api import updates as updates_api
    from tdelegram.files import download, send_file, wait_for_send

    client, transport = _client_with(
        getChat={"@type": "chat", "id": 1},
        searchPublicChat={"@type": "chat", "id": 2},
        sendBotStartMessage={"@type": "ok"},
        getInlineQueryResults={"@type": "inlineQueryResults"},
        sendInlineQueryResultMessage={"@type": "ok"},
        answerCallbackQuery={"@type": "ok"},
        answerInlineQuery={"@type": "ok"},
        canPostStory={"@type": "ok"},
        getChatArchivedStories={"@type": "stories"},
        deleteStory={"@type": "ok"},
        createNewSecretChat={"@type": "secretChat"},
        closeSecretChat={"@type": "ok"},
        searchSecretMessages={"@type": "foundChatMessages"},
        getProxies={"@type": "proxies"},
        addProxy={"@type": "proxy"},
        enableProxy={"@type": "ok"},
        disableProxy={"@type": "ok"},
        pingProxy={"@type": "seconds"},
        downloadFile={
            "@type": "file",
            "id": 1,
            "local": {"path": "/tmp/x", "is_downloading_completed": True},
        },
        sendMessage={"@type": "message", "id": 6},
    )
    try:
        assert bots.start_bot(client, "2", "1", allow_write=True)["@type"] == "ok"
        inline = bots.inline_results(client, 2, "q", allow_write=True)
        assert inline["@type"] == "inlineQueryResults"
        assert bots.send_inline_result(client, "1", "r1", allow_write=True)["@type"] == "ok"
        assert bots.answer_callback(client, 9, allow_write=True)["@type"] == "ok"
        assert bots.answer_inline(client, 9, [], allow_write=True)["@type"] == "ok"
        assert stories.can_post_story(client)["@type"] == "ok"
        assert stories.list_archived_stories(client, "1")["@type"] == "stories"
        assert (
            stories.delete_story(client, 4, allow_write=True, allow_destructive=True)["@type"]
            == "ok"
        )
        assert secret.create_secret(client, 2, allow_write=True)["@type"] == "secretChat"
        assert (
            secret.close_secret(client, 3, allow_write=True, allow_destructive=True)["@type"]
            == "ok"
        )
        assert secret.search_secret(client, "1", "q")["@type"] == "foundChatMessages"
        assert proxies.list_proxies(client)["@type"] == "proxies"
        assert proxies.add_proxy(client, "127.0.0.1", 1080, allow_write=True)["@type"] == "proxy"
        assert proxies.enable_proxy(client, 1, allow_write=True)["@type"] == "ok"
        assert proxies.disable_proxy(client, allow_write=True)["@type"] == "ok"
        assert proxies.ping_proxy(client, 1)["@type"] == "seconds"
        assert download(client, 1)["id"] == 1
        assert send_file(client, 1, "/tmp/x.pdf")["@type"] == "message"
        # wait_for_send with a queued success update
        transport.add_update(
            {
                "@type": "updateMessageSendSucceeded",
                "old_message_id": 0,
                "message": {"chat_id": 1, "id": 7},
            },
            client_id=client.client_id,
        )
        assert wait_for_send(client, 0, 1, timeout=3.0)["status"] == "sent"
        transport.add_update(
            {"@type": "updateMessageSendFailed", "old_message_id": 4242, "error": {"code": 400}},
            client_id=client.client_id,
        )
        assert wait_for_send(client, 4242, 1, timeout=3.0)["status"] == "failed"
        assert wait_for_send(client, 9999, 1, timeout=0.3)["status"] == "pending"
        # updates follow generators (bounded)
        transport.add_update({"@type": "updateNewMessage"}, client_id=client.client_id)
        gen = updates_api.follow_filtered(client, ["updateNewMessage"], timeout=2.0)
        first = next(gen)
        assert first["@type"] == "updateNewMessage"
    finally:
        client.close()
    from tdelegram.loop import reset_loops

    reset_loops()


def test_cli_output_and_context() -> None:
    from tdelegram.cli import context as ctxmod
    from tdelegram.cli.output import emit, emit_many, error_envelope, warn

    assert error_envelope(1, "x")["ok"] is False
    warn("hello-stderr")
    emit({"a": 1}, fmt="jsonl")
    emit({"a": 1}, fmt="json")
    emit({"a": 1, "raw": {}}, fmt="table")
    emit({"a": 1}, fmt="jsonl", out="/tmp/tdelegram-test-out.jsonl")
    emit_many([{"a": 1}], fmt="json")
    emit_many([{"a": 1}], fmt="jsonl", out="/tmp/tdelegram-test-out.jsonl")

    @ctxmod.handle_errors
    def _boom() -> None:
        from tdelegram.errors import TelegramError

        raise TelegramError(500, "boom", "m")

    try:
        _boom()
        raise AssertionError()
    except SystemExit as exc:
        assert exc.code == 1

    @ctxmod.handle_errors
    def _confirm() -> None:
        from tdelegram.errors import WriteConfirmationRequired

        raise WriteConfirmationRequired("sendMessage", {"@type": "sendMessage"})

    try:
        _confirm()
        raise AssertionError()
    except SystemExit as exc:
        assert exc.code == 2


def test_auth_provider_and_params() -> None:
    import pytest

    from tdelegram import credentials as creds
    from tdelegram.auth import ConsoleCredentialProvider, run_auth, tdlib_parameters
    from tdelegram.errors import TelegramTimeoutError

    params = tdlib_parameters(api_id=1, api_hash="h", database_directory="/d", files_directory="/f")
    assert params["@type"] == "setTdlibParameters"
    provider = ConsoleCredentialProvider(base_dir=None)
    orig = creds.resolve_secret
    creds.resolve_secret = lambda **kw: {"TELEGRAM_API_ID": "42"}.get(kw.get("env_name", ""), "v")  # type: ignore[assignment]
    try:
        assert provider.get_api_id() == 42
        assert provider.get_api_hash() == "v"
        assert provider.get_phone() == "v"
        assert provider.get_code() == "v"
        assert provider.get_password() == "v"
        assert provider.get_email() == "v"
        assert provider.get_email_code() == "v"
        assert provider.get_database_key() == "v"
    finally:
        creds.resolve_secret = orig  # type: ignore[assignment]
    creds.resolve_secret = lambda **kw: "not-an-int"  # type: ignore[assignment]
    try:
        with pytest.raises(RuntimeError):
            provider.get_api_id()
    finally:
        creds.resolve_secret = orig  # type: ignore[assignment]
    # unknown state + timeout + terminal states
    from tdelegram.auth import response_for_state

    class P:
        def get_api_id(self) -> int:
            return 1

        def get_api_hash(self) -> str:
            return "h"

        def get_database_key(self) -> str:
            return ""

        def get_phone(self) -> str:
            return "p"

        def get_code(self) -> str:
            return "c"

        def get_password(self) -> str:
            return "pw"

        def get_email(self) -> str:
            return "e"

        def get_email_code(self) -> str:
            return "ec"

    with pytest.raises(RuntimeError):
        response_for_state("bogus", {}, P(), database_directory="/d", files_directory="/f")  # type: ignore[arg-type]
    with pytest.raises(RuntimeError):
        response_for_state(
            "authorizationStateClosed", {}, P(), database_directory="/d", files_directory="/f"
        )  # type: ignore[arg-type]

    class EmptyClient:
        def next_update(self, timeout: float = 1.0) -> None:
            return None

        def send_request(self, request: dict) -> dict:
            return {"@type": "ok"}

    with pytest.raises(TelegramTimeoutError):
        run_auth(EmptyClient(), P(), timeout=0.1, database_directory="/d", files_directory="/f")  # type: ignore[arg-type]


def test_safety_helpers() -> None:
    from tdelegram import safety

    assert safety.needs_confirmation("sendMessage") is True
    assert safety.needs_confirmation("getMe") is False
    assert safety.is_destructive("deleteMessages") is True
    assert safety.preview_request("getMe", {})["verdict"] == "read"
    assert safety.reason("getMe") != ""


def test_iter_topics_emits_each_topic_once() -> None:
    """Regression: dedup read info.topic_id, but the field is forum_topic_id.

    The set never filled, so every page was yielded again and a caller
    counting topics got double.
    """
    from tdelegram.api import topics
    from tdelegram.client import TelegramClient
    from tdelegram.loop import reset_loops
    from tdelegram.transport import FakeTransport

    transport = FakeTransport()
    transport.add_simple_response("searchPublicChat", {"@type": "chat", "id": -100})
    page = {
        "@type": "forumTopics",
        "topics": [
            {"info": {"forum_topic_id": 1, "name": "General"}, "last_message_date": 10},
            {"info": {"forum_topic_id": 2, "name": "Vacancies"}, "last_message_date": 20},
        ],
    }
    # TDLib keeps returning the same page when the offset does not advance.
    transport.add_simple_response("getForumTopics", page)
    client = TelegramClient(transport)
    try:
        got = [t["info"]["forum_topic_id"] for t in topics.iter_topics(client, "somechat")]
    finally:
        client.close()
        reset_loops()
    assert got == [1, 2], f"each topic must appear once, got {got}"
