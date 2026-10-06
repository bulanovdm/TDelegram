"""The MCP server: what it offers, what the gate holds back, and what stdout carries.

Tools are driven through the SDK's in-memory client against a FakeTransport, so a
request that does not match TDLib's schema fails the test like any other. The
property that matters most is the one CONTRIBUTING.md asks of every mutating
change: nothing reaches TDLib without confirmation.
"""

from __future__ import annotations

import asyncio
import json
import subprocess
import sys
import threading
import time
from typing import Any

import mcp
import pytest
from mcp.types import CallToolResult
from typer.testing import CliRunner

import tdelegram
from tdelegram import mcp_server
from tdelegram.cli import main as cli_main
from tdelegram.cli.context import Ctx
from tdelegram.client import TelegramClient
from tdelegram.mcp_server import ClientSession, NotAuthorized, Policy, build_server
from tdelegram.transport import FakeTransport

runner = CliRunner()

READ_ONLY = Policy()
WRITES = Policy(allow_write=True)
EVERYTHING = Policy(allow_write=True, allow_destructive=True)

# tool -> (arguments, the TDLib method that performs it)
WRITE_TOOLS: dict[str, tuple[dict[str, Any], str]] = {
    "set_draft": ({"chat": "42", "text": "hi"}, "setChatDraftMessage"),
    "send_message": ({"chat": "42", "text": "hi"}, "sendMessage"),
    "edit_message": ({"chat": "42", "message_id": 9, "text": "x"}, "editMessageText"),
    "forward_messages": (
        {"from_chat": "42", "to_chat": "42", "message_ids": [9]},
        "forwardMessages",
    ),
    "add_reaction": ({"chat": "42", "message_id": 9, "emoji": "\U0001f44d"}, "addMessageReaction"),
    "pin_message": ({"chat": "42", "message_id": 9}, "pinChatMessage"),
    "mark_chat_read": ({"chat": "42"}, "viewMessages"),
}
DESTRUCTIVE_TOOLS: dict[str, tuple[dict[str, Any], str]] = {
    "delete_messages": ({"chat": "42", "message_ids": [9]}, "deleteMessages"),
    "leave_chat": ({"chat": "42"}, "leaveChat"),
}

TEXT = {"@type": "formattedText", "text": "x", "entities": []}
SEND_REQUEST = {
    "@type": "sendMessage",
    "chat_id": 42,
    "input_message_content": {"@type": "inputMessageText", "text": TEXT},
}
DELETE_REQUEST = {"@type": "deleteMessages", "chat_id": 42, "message_ids": [9], "revoke": True}
VIEW_REQUEST = {"@type": "viewMessages", "chat_id": 42, "message_ids": [9]}


def _message(message_id: int, text: str = "hello", **extra: Any) -> dict[str, Any]:
    return {
        "@type": "message",
        "id": message_id,
        "chat_id": 42,
        "date": 1_700_000_000,
        "is_outgoing": False,
        "sender_id": {"@type": "messageSenderUser", "user_id": 7},
        "content": {
            "@type": "messageText",
            "text": {"@type": "formattedText", "text": text, "entities": []},
        },
        **extra,
    }


@pytest.fixture
def world(transport: FakeTransport) -> FakeTransport:
    """A private chat 42 and a group 43 with a few messages, and a user 7 who wrote them."""

    def chat(request: dict[str, Any]) -> dict[str, Any]:
        chat_id = request["chat_id"]
        kind = (
            {"@type": "chatTypePrivate", "user_id": 7}
            if chat_id == 42
            else {"@type": "chatTypeSupergroup", "supergroup_id": 5, "is_channel": False}
        )
        return {"@type": "chat", "id": chat_id, "title": "Friends", "type": kind}

    transport.add_response(lambda request: request.get("@type") == "getChat", chat)
    transport.add_simple_response(
        "getUser", {"@type": "user", "id": 7, "first_name": "Ada", "last_name": ""}
    )
    transport.add_simple_response(
        "getChatHistory",
        {"@type": "messages", "messages": [_message(3), _message(2), _message(1)]},
    )
    return transport


def server_for(client: TelegramClient, policy: Policy = READ_ONLY) -> Any:
    session = ClientSession(Ctx(), opener=lambda ctx: (client, lambda: None))
    return build_server(session, policy)


def call(server: Any, tool: str, /, **arguments: Any) -> CallToolResult:
    # Positional-only, so a tool argument may itself be called `name` or `tool`.
    async def go() -> CallToolResult:
        async with mcp.Client(server) as session:
            return await session.call_tool(tool, arguments)

    return asyncio.run(go())


def listed(server: Any) -> dict[str, Any]:
    async def go() -> dict[str, Any]:
        async with mcp.Client(server) as session:
            return {tool.name: tool for tool in (await session.list_tools()).tools}

    return asyncio.run(go())


def sent(transport: FakeTransport) -> list[str]:
    return [str(request.get("@type")) for _, request in transport.sent]


def payload(result: CallToolResult) -> dict[str, Any]:
    assert result.structured_content is not None
    return result.structured_content


# -- what is offered -------------------------------------------------------


def test_a_server_started_with_no_flags_offers_reads_and_nothing_that_writes(
    client: TelegramClient,
) -> None:
    names = set(listed(server_for(client)))
    assert {"list_chats", "get_chat_history", "get_unread", "search_messages"} <= names
    assert {"tdelegram_call", "tdelegram_describe", "auth_status"} <= names
    assert not names & set(WRITE_TOOLS)
    assert not names & set(DESTRUCTIVE_TOOLS)


def test_allow_write_adds_writes_but_not_destructive_tools(client: TelegramClient) -> None:
    names = set(listed(server_for(client, WRITES)))
    assert set(WRITE_TOOLS) <= names
    assert not names & set(DESTRUCTIVE_TOOLS)


def test_allow_destructive_adds_the_destructive_tools(client: TelegramClient) -> None:
    names = set(listed(server_for(client, EVERYTHING)))
    assert set(WRITE_TOOLS) | set(DESTRUCTIVE_TOOLS) <= names


def test_destructive_without_write_is_refused() -> None:
    with pytest.raises(ValueError, match="--allow-write"):
        Policy(allow_destructive=True)


def test_policy_permits() -> None:
    assert READ_ONLY.permits("read") and not READ_ONLY.permits("write")
    assert WRITES.permits("write") and not WRITES.permits("destructive")
    assert EVERYTHING.permits("destructive")


def test_tools_say_what_they_do_to_the_client(client: TelegramClient) -> None:
    tools = listed(server_for(client, EVERYTHING))
    for name in ("list_chats", "get_chat_history", "get_unread", "wait_for_messages"):
        assert tools[name].annotations.read_only_hint is True
    for name in WRITE_TOOLS:
        assert tools[name].annotations.read_only_hint is False
        assert tools[name].annotations.destructive_hint is False
    for name in DESTRUCTIVE_TOOLS:
        assert tools[name].annotations.destructive_hint is True
    # The raw call is as strong as the policy behind it.
    assert tools["tdelegram_call"].annotations.destructive_hint is True
    read_only_raw = listed(server_for(client))["tdelegram_call"].annotations
    assert read_only_raw.read_only_hint is True and read_only_raw.destructive_hint is False


# -- the gate --------------------------------------------------------------


@pytest.mark.parametrize("name", WRITE_TOOLS)
def test_a_write_previews_without_confirm_and_never_reaches_tdlib(
    world: FakeTransport, client: TelegramClient, name: str
) -> None:
    arguments, method = WRITE_TOOLS[name]
    result = call(server_for(client, EVERYTHING), name, **arguments)
    body = payload(result)
    assert not result.is_error
    assert body["status"] == "confirmation_required"
    assert body["method"] == method
    assert body["verdict"] == "write"
    assert "confirm=true" in body["next"]
    assert "schema_problems" not in body, "the preview must show what would really be sent"
    assert method not in sent(world), f"{method} reached TDLib without confirm"


@pytest.mark.parametrize("name", WRITE_TOOLS)
def test_a_write_runs_once_confirmed(
    world: FakeTransport, client: TelegramClient, name: str
) -> None:
    arguments, method = WRITE_TOOLS[name]
    result = call(server_for(client, WRITES), name, **arguments, confirm=True)
    assert not result.is_error, result.content
    assert "status" not in payload(result)
    assert method in sent(world)


@pytest.mark.parametrize("name", DESTRUCTIVE_TOOLS)
def test_a_destructive_call_needs_confirm_and_the_method_name(
    world: FakeTransport, client: TelegramClient, name: str
) -> None:
    arguments, method = DESTRUCTIVE_TOOLS[name]
    server = server_for(client, EVERYTHING)

    nothing = call(server, name, **arguments)
    assert payload(nothing)["status"] == "confirmation_required"
    assert payload(nothing)["verdict"] == "destructive"

    # confirm alone is not enough: the second layer asks for the method itself.
    confirmed = call(server, name, **arguments, confirm=True)
    assert payload(confirmed)["status"] == "confirmation_required"
    assert method in payload(confirmed)["next"]

    wrong = call(server, name, **arguments, confirm=True, confirm_method="sendMessage")
    assert payload(wrong)["status"] == "confirmation_required"

    typed_without_confirm = call(server, name, **arguments, confirm_method=method)
    assert payload(typed_without_confirm)["status"] == "confirmation_required"

    assert method not in sent(world), f"{method} reached TDLib without both"
    done = call(server, name, **arguments, confirm=True, confirm_method=method)
    assert not done.is_error and "status" not in payload(done)
    assert method in sent(world)


def test_a_viewed_message_is_a_write_to_this_server(
    world: FakeTransport, client: TelegramClient
) -> None:
    """The registry says viewMessages is a read; the sender sees a read receipt."""
    refused = call(server_for(client), "tdelegram_call", request=VIEW_REQUEST)
    assert refused.is_error and payload(refused)["error"]["code"] == 403

    server = server_for(client, WRITES)
    preview = payload(call(server, "tdelegram_call", request=VIEW_REQUEST))
    assert preview["status"] == "confirmation_required"
    assert "read receipt" in preview["note"]
    assert "viewMessages" not in sent(world)

    call(server, "tdelegram_call", request=VIEW_REQUEST, confirm=True)
    assert "viewMessages" in sent(world)


# -- the raw call ----------------------------------------------------------


def test_a_raw_read_runs_at_once(transport: FakeTransport, client: TelegramClient) -> None:
    transport.add_simple_response("getMe", {"@type": "user", "id": 7, "first_name": "Ada"})
    result = call(server_for(client), "tdelegram_call", request={"@type": "getMe"})
    assert not result.is_error and payload(result)["id"] == 7


def test_a_raw_write_is_refused_by_a_read_only_server_before_a_session_opens(
    transport: FakeTransport, client: TelegramClient
) -> None:
    opened: list[int] = []

    def opener(ctx: Ctx) -> Any:
        opened.append(1)
        return client, lambda: None

    server = build_server(ClientSession(Ctx(), opener=opener), READ_ONLY)
    result = call(server, "tdelegram_call", request=SEND_REQUEST, confirm=True)
    assert result.is_error
    error = payload(result)["error"]
    assert error["code"] == 403 and "--allow-write" in error["message"]
    assert not opened and "sendMessage" not in sent(transport)


def test_a_raw_destructive_call_needs_its_own_flag_and_then_the_method_name(
    world: FakeTransport, client: TelegramClient
) -> None:
    refused = call(
        server_for(client, WRITES), "tdelegram_call", request=DELETE_REQUEST, confirm=True
    )
    assert "--allow-destructive" in payload(refused)["error"]["message"]

    server = server_for(client, EVERYTHING)
    stopped = call(server, "tdelegram_call", request=DELETE_REQUEST, confirm=True)
    assert payload(stopped)["status"] == "confirmation_required"
    assert "deleteMessages" not in sent(world)
    call(
        server,
        "tdelegram_call",
        request=DELETE_REQUEST,
        confirm=True,
        confirm_method="deleteMessages",
    )
    assert "deleteMessages" in sent(world)


def test_a_raw_write_previews_then_runs(world: FakeTransport, client: TelegramClient) -> None:
    server = server_for(client, WRITES)
    assert payload(call(server, "tdelegram_call", request=SEND_REQUEST))["status"] == (
        "confirmation_required"
    )
    assert "sendMessage" not in sent(world)
    call(server, "tdelegram_call", request=SEND_REQUEST, confirm=True)
    assert "sendMessage" in sent(world)


def test_a_raw_request_that_does_not_match_the_schema_is_refused(
    world: FakeTransport, client: TelegramClient
) -> None:
    bad = {"@type": "sendMessage", "chat_id": 42, "text": "hi"}
    result = call(server_for(client, WRITES), "tdelegram_call", request=bad, confirm=True)
    assert result.is_error
    body = payload(result)
    assert body["error"]["code"] == 400
    assert any("no field 'text'" in problem for problem in body["schema_problems"])
    assert "sendMessage" not in sent(world)


def test_an_unknown_method_fails_closed(world: FakeTransport, client: TelegramClient) -> None:
    result = call(server_for(client, EVERYTHING), "tdelegram_call", request={"@type": "noSuch"})
    assert result.is_error and "Unknown TDLib method" in payload(result)["error"]["message"]
    assert "noSuch" not in sent(world)


def test_a_raw_request_without_a_type_is_refused(client: TelegramClient) -> None:
    result = call(server_for(client), "tdelegram_call", request={"chat_id": 1})
    assert result.is_error and "@type" in payload(result)["error"]["message"]


def test_describe_gives_the_shape_and_the_verdict(client: TelegramClient) -> None:
    server = server_for(client)
    body = payload(call(server, "tdelegram_describe", name="sendMessage"))
    assert body["kind"] == "function" and body["verdict"] == "write"
    assert "input_message_content" in body["params"]
    missing = call(server, "tdelegram_describe", name="sendMesage")
    assert missing.is_error and "sendMessage" in payload(missing)["error"]["message"]


# -- the read tools --------------------------------------------------------


def test_every_read_tool_builds_requests_tdlib_accepts(
    world: FakeTransport, client: TelegramClient
) -> None:
    """Each tool end to end. A misnamed argument would only show up when one ran."""
    world.add_simple_response("getMe", {"@type": "user", "id": 1, "first_name": "Me"})
    world.add_simple_response("getChats", {"@type": "chats", "chat_ids": [42]})
    world.add_simple_response("getMessage", _message(9))
    world.add_simple_response("getContacts", {"@type": "users", "user_ids": [7]})
    # TDLib has no getChatFolders: the list arrives once, as an update.
    world.add_update(
        {
            "@type": "updateChatFolders",
            "chat_folders": [
                {
                    "@type": "chatFolderInfo",
                    "id": 1,
                    "name": {
                        "@type": "chatFolderName",
                        "text": {"@type": "formattedText", "text": "Work", "entities": []},
                    },
                    "icon": {"@type": "chatFolderIcon", "name": "Work"},
                }
            ],
            "main_chat_list_position": 0,
            "are_tags_enabled": False,
        }
    )
    time.sleep(0.3)  # let the reader thread deliver it
    server = server_for(client)
    calls: dict[str, dict[str, Any]] = {
        "auth_status": {},
        "get_me": {},
        "list_chats": {"scope": "all", "limit": 5, "unread_only": False},
        "get_chat": {"chat": "42"},
        "get_chat_members": {"chat": "43"},
        "get_chat_history": {"chat": "42", "limit": 2, "since": "7d", "contains": ["hello"]},
        "get_message": {"chat": "42", "message_id": 9},
        "get_message_link": {"chat": "42", "message_id": 9},
        "search_messages": {"query": "hello"},
        "get_unread": {"chats": 1, "per_chat": 1},
        "get_user": {"user": "7"},
        "list_contacts": {},
        "list_topics": {"chat": "42"},
        "list_folders": {},
    }
    for name, arguments in calls.items():
        result = call(server, name, **arguments)
        assert not result.is_error, f"{name}: {result.content}"
    # A search of one chat and a search of every chat take different filters.
    chat_search = call(server, "search_messages", query="hi", chat="42", sender_id=7)
    assert not chat_search.is_error, chat_search.content
    folders = payload(call(server, "list_folders"))
    assert [folder["name"] for folder in folders["items"]] == ["Work"]


def test_a_search_will_not_mix_the_two_sets_of_filters(
    world: FakeTransport, client: TelegramClient
) -> None:
    server = server_for(client)
    in_chat = call(server, "search_messages", query="x", chat="42", since="7d")
    assert in_chat.is_error and "since" in payload(in_chat)["error"]["message"]
    everywhere = call(server, "search_messages", query="x", sender_id=7)
    assert everywhere.is_error and "chat" in payload(everywhere)["error"]["message"]


def test_a_list_says_when_it_left_something_out(
    world: FakeTransport, client: TelegramClient
) -> None:
    server = server_for(client)
    short = payload(call(server, "get_chat_history", chat="42", limit=2))
    assert [m["message_id"] for m in short["items"]] == [3, 2]
    assert short["truncated"] is True and short["count"] == 2
    whole = payload(call(server, "get_chat_history", chat="42", limit=50))
    assert whole["count"] == 3 and whole["truncated"] is False


def test_a_download_returns_the_file_or_says_there_is_none(
    world: FakeTransport, client: TelegramClient
) -> None:
    server = server_for(client)
    plain = call(server, "download_media", chat="42", message_id=9)
    assert plain.is_error and "no downloadable file" in payload(plain)["error"]["message"]

    document = _message(9)
    document["content"] = {
        "@type": "messageDocument",
        "document": {
            "@type": "document",
            "file_name": "a.txt",
            "mime_type": "text/plain",
            "document": {"@type": "file", "id": 5, "size": 3},
        },
        "caption": {"@type": "formattedText", "text": "", "entities": []},
    }
    world.add_simple_response("getMessage", document)
    world.add_simple_response(
        "downloadFile",
        {
            "@type": "file",
            "id": 5,
            "size": 3,
            "local": {"@type": "localFile", "path": "/tmp/a.txt", "is_downloading_completed": True},
        },
    )
    done = call(server, "download_media", chat="42", message_id=9)
    assert not done.is_error, done.content
    assert "downloadFile" in sent(world)


def test_a_result_too_large_to_use_says_so() -> None:
    result = mcp_server._result({"text": "a" * 300_000})
    body = payload(result)
    assert body["truncated"] is True and body["size_chars"] > mcp_server.MAX_RESULT_CHARS
    assert len(body["head"]) == mcp_server.HEAD_CHARS


# -- waiting for messages --------------------------------------------------


def _new_message(message_id: int, text: str) -> dict[str, Any]:
    return {"@type": "updateNewMessage", "message": _message(message_id, text)}


def test_waiting_returns_a_message_that_arrives_afterwards_and_not_an_old_one(
    world: FakeTransport, client: TelegramClient
) -> None:
    world.add_update(_new_message(1, "before the call"))
    time.sleep(0.3)  # let the reader thread deliver it into the client's queue
    timer = threading.Timer(0.6, lambda: world.add_update(_new_message(2, "after")))
    timer.start()
    try:
        result = call(
            server_for(client), "wait_for_messages", chats=["42"], timeout_seconds=10, count=1
        )
    finally:
        timer.cancel()
    body = payload(result)
    assert [m["message_id"] for m in body["items"]] == [2]
    assert body["timed_out"] is False
    # The chat was opened to hear it and closed again.
    assert sent(world).count("openChat") == 1 and sent(world).count("closeChat") == 1


def test_waiting_times_out_and_only_one_wait_runs_at_a_time(
    world: FakeTransport, client: TelegramClient
) -> None:
    server = server_for(client)

    async def two() -> list[CallToolResult]:
        async with mcp.Client(server) as session:
            return list(
                await asyncio.gather(
                    session.call_tool("wait_for_messages", {"timeout_seconds": 1}),
                    session.call_tool("wait_for_messages", {"timeout_seconds": 1}),
                )
            )

    results = asyncio.run(two())
    errors = [r for r in results if r.is_error]
    waited = [r for r in results if not r.is_error]
    assert len(errors) == 1 and len(waited) == 1
    assert "Another wait_for_messages" in payload(errors[0])["error"]["message"]
    assert payload(waited[0]) == {"items": [], "count": 0, "timed_out": True}


def test_a_bad_pattern_is_refused_before_waiting(client: TelegramClient) -> None:
    result = call(server_for(client), "wait_for_messages", match="(")
    assert result.is_error and "Invalid match pattern" in payload(result)["error"]["message"]


# -- failures --------------------------------------------------------------


def test_a_tdlib_error_comes_back_as_an_envelope(
    transport: FakeTransport, client: TelegramClient
) -> None:
    transport.add_simple_response(
        "getChat", {"@type": "error", "code": 404, "message": "Chat not found"}
    )
    result = call(server_for(client), "get_chat", chat="42")
    assert result.is_error
    assert payload(result) == {
        "ok": False,
        "error": {"code": 404, "message": "Chat not found", "method": "getChat"},
    }


def test_a_profile_that_is_not_logged_in_is_reported_and_retried_next_time() -> None:
    state = {"@type": "authorizationStateWaitPhoneNumber", "needs": "a phone number"}
    attempts: list[int] = []

    def opener(ctx: Ctx) -> Any:
        attempts.append(1)
        raise NotAuthorized(state)

    server = build_server(ClientSession(Ctx(profile="work"), opener=opener), READ_ONLY)
    status = payload(call(server, "auth_status"))
    assert status["authorized"] is False and status["profile"] == "work"
    assert status["needs"] == "a phone number" and "tdelegram auth login" in status["hint"]

    failed = call(server, "get_me")
    assert failed.is_error and payload(failed)["error"]["code"] == 401
    assert "tdelegram auth login" in payload(failed)["error"]["message"]
    assert len(attempts) == 2, "nothing is cached after a failure, so logging in later works"


def test_a_logged_in_profile_is_reported_and_opened_once(client: TelegramClient) -> None:
    opened: list[int] = []

    def opener(ctx: Ctx) -> Any:
        opened.append(1)
        return client, lambda: None

    server = build_server(ClientSession(Ctx(), opener=opener), READ_ONLY)
    assert payload(call(server, "auth_status")) == {"authorized": True, "profile": "default"}
    call(server, "get_me")
    assert len(opened) == 1


def test_the_session_releases_its_profile_once() -> None:
    released: list[int] = []
    session = ClientSession(Ctx(), opener=lambda ctx: (object(), lambda: released.append(1)))  # type: ignore[arg-type,return-value]
    session.close()  # never opened: nothing to release
    session.client()
    session.close()
    session.close()
    assert released == [1]


class _Lock:
    released = False

    def release(self) -> None:
        self.released = True


def test_a_profile_that_is_not_logged_in_gives_back_its_lock(
    monkeypatch: pytest.MonkeyPatch, client: TelegramClient
) -> None:
    lock = _Lock()
    monkeypatch.setattr(mcp_server, "make_client", lambda ctx, login: (client, lock))
    monkeypatch.setattr(
        mcp_server,
        "open_profile",
        lambda ctx, c: {"@type": "authorizationStateWaitCode", "authorized": False, "needs": "x"},
    )
    with pytest.raises(NotAuthorized):
        mcp_server.open_authorized_client(Ctx())
    assert lock.released, "a person could not log in from a terminal while the server held it"


def test_a_logged_in_profile_keeps_its_lock_until_released(
    monkeypatch: pytest.MonkeyPatch, client: TelegramClient
) -> None:
    lock = _Lock()
    monkeypatch.setattr(mcp_server, "make_client", lambda ctx, login: (client, lock))
    monkeypatch.setattr(
        mcp_server, "open_profile", lambda ctx, c: {"@type": "x", "authorized": True}
    )
    opened, release = mcp_server.open_authorized_client(Ctx())
    assert opened is client and not lock.released
    release()
    assert lock.released


def test_serve_releases_the_session_when_the_client_goes_away(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    closed: list[int] = []

    class Session:
        def __init__(self, ctx: Ctx) -> None:
            pass

        def close(self) -> None:
            closed.append(1)

    class Server:
        def run(self, transport: str) -> None:
            assert transport == "stdio"
            raise KeyboardInterrupt

    monkeypatch.setattr(mcp_server, "ClientSession", Session)
    monkeypatch.setattr(mcp_server, "build_server", lambda session, policy: Server())
    with pytest.raises(KeyboardInterrupt):
        mcp_server.serve(Ctx(), READ_ONLY)
    assert closed == [1]


# -- the command -----------------------------------------------------------


def test_the_command_passes_the_profile_and_the_flags_on(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, Any] = {}
    monkeypatch.setattr(mcp_server, "serve", lambda ctx, policy: seen.update(ctx=ctx, p=policy))
    result = runner.invoke(
        cli_main.app,
        ["--profile", "work", "mcp", "--allow-write", "--allow-destructive"],
    )
    assert result.exit_code == 0, result.output
    assert seen["p"] == EVERYTHING and seen["ctx"].profile == "work"

    seen.clear()
    assert runner.invoke(cli_main.app, ["mcp"]).exit_code == 0
    assert seen["p"] == READ_ONLY


def test_the_command_refuses_destructive_without_write(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(mcp_server, "serve", lambda ctx, policy: pytest.fail("must not serve"))
    result = runner.invoke(cli_main.app, ["mcp", "--allow-destructive"])
    assert result.exit_code == 2 and "--allow-write" in result.output


def test_the_global_yes_does_not_grant_an_mcp_server_anything(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(mcp_server, "serve", lambda ctx, policy: pytest.fail("must not serve"))
    result = runner.invoke(cli_main.app, ["--yes", "mcp"])
    assert result.exit_code == 2 and "--allow-write" in result.output


def test_the_command_says_how_to_install_a_missing_sdk(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delattr(tdelegram, "mcp_server")
    monkeypatch.delitem(sys.modules, "tdelegram.mcp_server")
    # Python caches each submodule under its full name, so hiding `mcp` alone is not enough.
    for loaded in [name for name in sys.modules if name == "mcp" or name.startswith("mcp.")]:
        monkeypatch.setitem(sys.modules, loaded, None)
    result = runner.invoke(cli_main.app, ["mcp"])
    assert result.exit_code == 1
    assert "pip install 'tdelegram[mcp]'" in result.output


# -- the protocol channel --------------------------------------------------


def _speak(*flags: str) -> tuple[list[dict[str, Any]], str, int]:
    """Launch `tdelegram mcp` for real, list its tools, close stdin, and report what it wrote.

    Returns every line the server put on stdout (each must be JSON), what it put on
    stderr, and its exit code.
    """
    process = subprocess.Popen(
        [sys.executable, "-m", "tdelegram.cli.main", "mcp", *flags],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    assert process.stdin is not None and process.stdout is not None and process.stderr is not None
    stdin, stdout = process.stdin, process.stdout
    # A server that hangs must fail the test, not the suite.
    watchdog = threading.Timer(60, process.kill)
    watchdog.start()

    def send(request: dict[str, Any]) -> None:
        stdin.write(json.dumps(request) + "\n")
        stdin.flush()

    def parse(line: str) -> dict[str, Any]:
        try:
            parsed: dict[str, Any] = json.loads(line)
        except json.JSONDecodeError:
            pytest.fail(f"stdout carried something that is not JSON: {line!r}")
        return parsed

    try:
        send(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-06-18",
                    "capabilities": {},
                    "clientInfo": {"name": "test", "version": "0"},
                },
            }
        )
        seen = [parse(stdout.readline())]
        send({"jsonrpc": "2.0", "method": "notifications/initialized"})
        send({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
        seen.append(parse(stdout.readline()))
        stdin.close()
        seen.extend(parse(line) for line in stdout.read().splitlines() if line.strip())
        err = process.stderr.read()
        code = process.wait(timeout=30)
    finally:
        watchdog.cancel()
        if process.poll() is None:
            process.kill()
    return seen, err, code


@pytest.mark.parametrize(
    "flags, offers, withholds",
    [
        ((), "get_chat_history", "send_message"),
        (("--allow-write",), "send_message", "delete_messages"),
        (("--allow-write", "--allow-destructive"), "delete_messages", None),
    ],
)
def test_stdout_carries_only_the_protocol_and_the_flags_set_the_tools(
    flags: tuple[str, ...], offers: str, withholds: str | None
) -> None:
    messages, err, code = _speak(*flags)
    assert code == 0, err
    assert all(m.get("jsonrpc") == "2.0" for m in messages), "anything else corrupts the stream"
    tools = next(m for m in messages if m.get("id") == 2)["result"]["tools"]
    names = {tool["name"] for tool in tools}
    assert offers in names
    if withholds:
        assert withholds not in names
    assert err == "", "the server logged to stderr on a clean run"
