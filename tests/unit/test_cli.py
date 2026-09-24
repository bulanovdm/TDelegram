"""CLI behaviour through Typer's runner, on a FakeTransport.

The CLI layer went untested and unmeasured for a long time, and both
launch-blocking bugs lived here: `auth login` never sent a bootstrap
request, and no data command replayed the TDLib handshake. These tests
exercise the command bodies rather than just importing them.
"""

from __future__ import annotations

import json
import re
from typing import Any

import pytest
from typer.testing import CliRunner

from tdelegram.cli import context as cli_context
from tdelegram.cli import main as cli_main
from tdelegram.client import TelegramClient
from tdelegram.loop import reset_loops
from tdelegram.transport import FakeTransport

runner = CliRunner()


@pytest.fixture
def cli(monkeypatch: pytest.MonkeyPatch) -> Any:
    """Wire the CLI to a scripted transport instead of a real TDLib."""
    # Keep the run hermetic: without these the credential chain falls through
    # to an interactive prompt and the suite blocks on stdin.
    monkeypatch.setenv("TELEGRAM_API_ID", "1")
    monkeypatch.setenv("TELEGRAM_API_HASH", "testhash")
    monkeypatch.setenv("TELEGRAM_PHONE", "+10000000000")
    monkeypatch.setenv("TELEGRAM_DB_KEY", "")
    monkeypatch.setenv("TELEGRAM_CODE", "11111")

    transport = FakeTransport()
    # An authorized session answers the handshake probe immediately.
    transport.add_simple_response("getAuthorizationState", {"@type": "authorizationStateReady"})
    client = TelegramClient(transport)

    def _fake_make_client(ctx: Any, **kwargs: Any) -> tuple[TelegramClient, None]:
        if kwargs.get("login", True):
            cli_context.ensure_login(client, ctx)
        return client, None

    monkeypatch.setattr(cli_context, "make_client", _fake_make_client)
    monkeypatch.setattr(client, "close", lambda: None)
    # The CLI keeps global option state on a module-level Ctx.
    monkeypatch.setattr(cli_main, "_state", cli_main.Ctx())
    yield transport
    reset_loops()


def _lines(result: Any) -> list[dict[str, Any]]:
    out = []
    for line in result.stdout.splitlines():
        line = line.strip()
        if line.startswith("{"):
            out.append(json.loads(line))
    return out


def _sent(transport: FakeTransport) -> list[str]:
    return [str(req.get("@type")) for _, req in transport.sent]


def test_version() -> None:
    result = runner.invoke(cli_main.app, ["version"])
    assert result.exit_code == 0
    assert result.stdout.strip()


def test_account_info_emits_a_record(cli: FakeTransport) -> None:
    cli.add_simple_response("getMe", {"@type": "user", "id": 7, "first_name": "Ada"})
    result = runner.invoke(cli_main.app, ["account", "info"])
    assert result.exit_code == 0
    assert _lines(result)[0]["id"] == 7


def test_data_command_performs_the_handshake(cli: FakeTransport) -> None:
    """Regression: TDLib params are per-process, so every command replays them.

    Scripts what a saved session looks like to a fresh process -- waiting for
    its parameters -- so the handshake has to do real work rather than
    answering "ready" on the first probe.
    """
    # FakeTransport matches the first rule that fits, so the fixture's
    # already-authorized answer has to go before a different one is scripted.
    cli._rules.clear()
    cli.add_simple_response(
        "getAuthorizationState", {"@type": "authorizationStateWaitTdlibParameters"}
    )

    def _opened(request: dict[str, Any]) -> dict[str, Any]:
        cli.add_update(
            {
                "@type": "updateAuthorizationState",
                "authorization_state": {"@type": "authorizationStateReady"},
            }
        )
        return {"@type": "ok"}

    cli.add_response(lambda r: r.get("@type") == "setTdlibParameters", _opened)
    cli.add_simple_response("getMe", {"@type": "user", "id": 7})

    result = runner.invoke(cli_main.app, ["account", "info"])
    assert result.exit_code == 0
    sent = _sent(cli)
    assert sent[0] == "getAuthorizationState", "handshake must be probed before any call"
    assert "setTdlibParameters" in sent, "handshake must actually be driven"
    assert sent.index("setTdlibParameters") < sent.index("getMe")


def test_auth_status_reports_authorized(cli: FakeTransport) -> None:
    """An authorized session answers the probe and needs nothing further."""
    result = runner.invoke(cli_main.app, ["auth", "status"])
    assert result.exit_code == 0
    status = _lines(result)[0]
    assert status["authorized"] is True
    assert status["needs"] is None
    assert status["@type"] == "authorizationStateReady"


def test_auth_status_clears_the_steps_stored_secrets_can_clear(cli: FakeTransport) -> None:
    """A saved session still starts at WaitTdlibParameters in a fresh process.

    Reporting that verbatim would be useless, so status replays the steps it
    can before answering.
    """
    cli._rules.clear()
    states = iter(
        [
            {"@type": "authorizationStateWaitTdlibParameters"},
            {"@type": "authorizationStateReady"},
        ]
    )
    cli.add_response(lambda r: r.get("@type") == "getAuthorizationState", lambda r: next(states))
    result = runner.invoke(cli_main.app, ["auth", "status"])
    assert result.exit_code == 0
    status = _lines(result)[0]
    assert status["authorized"] is True
    assert "setTdlibParameters" in _sent(cli), "status must clear what it can"


def test_auth_status_says_what_is_missing(cli: FakeTransport) -> None:
    cli._rules.clear()
    cli.add_simple_response(
        "getAuthorizationState", {"@type": "authorizationStateWaitPhoneNumber"}
    )
    result = runner.invoke(cli_main.app, ["auth", "status"])
    assert result.exit_code == 0
    status = _lines(result)[0]
    assert status["authorized"] is False
    assert status["needs"] == "a phone number"


def test_auth_status_never_prompts(
    cli: FakeTransport, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Regression: `status` must not block on stdin when secrets are absent."""
    for var in ("TELEGRAM_API_ID", "TELEGRAM_API_HASH", "TELEGRAM_DB_KEY"):
        monkeypatch.delenv(var, raising=False)
    # Any prompt attempt is a failure, not a hang.
    import tdelegram.credentials as creds

    monkeypatch.setattr(
        creds, "default_prompt", lambda *a, **k: pytest.fail("status prompted for a secret")
    )
    monkeypatch.setattr(creds, "os_store_get", lambda service, account: None)
    cli._rules.clear()
    cli.add_simple_response(
        "getAuthorizationState", {"@type": "authorizationStateWaitTdlibParameters"}
    )
    result = runner.invoke(cli_main.app, ["auth", "status"])
    assert result.exit_code == 0
    status = _lines(result)[0]
    assert status["authorized"] is False
    # `needs` now names the credential that actually failed to resolve rather
    # than whatever the state requires first, so assert the property, not a
    # fixed string.
    assert status["needs"], "an unauthorized session must say what is missing"
    assert re.search(r"api|id|hash", status["needs"], re.I), status["needs"]


def test_chat_list_streams_normalized_records(cli: FakeTransport) -> None:
    cli.add_simple_response("getChats", {"@type": "chats", "chat_ids": [10, 11]})
    cli.add_response(
        lambda r: r.get("@type") == "getChat",
        lambda r: {"@type": "chat", "id": r["chat_id"], "title": f"Chat {r['chat_id']}"},
    )
    result = runner.invoke(cli_main.app, ["chat", "list"])
    assert result.exit_code == 0
    records = _lines(result)
    assert [r["title"] for r in records] == ["Chat 10", "Chat 11"]
    assert records[0]["chat_list"] == "main"


def test_chat_list_respects_limit(cli: FakeTransport) -> None:
    cli.add_simple_response("getChats", {"@type": "chats", "chat_ids": [1, 2, 3, 4]})
    cli.add_response(
        lambda r: r.get("@type") == "getChat",
        lambda r: {"@type": "chat", "id": r["chat_id"], "title": "t"},
    )
    result = runner.invoke(cli_main.app, ["chat", "list", "--limit", "2"])
    assert len(_lines(result)) == 2


def test_json_format_emits_one_array(cli: FakeTransport) -> None:
    cli.add_simple_response("getChats", {"@type": "chats", "chat_ids": [10]})
    cli.add_response(
        lambda r: r.get("@type") == "getChat",
        lambda r: {"@type": "chat", "id": r["chat_id"], "title": "Solo"},
    )
    result = runner.invoke(cli_main.app, ["--format", "json", "chat", "list"])
    assert result.exit_code == 0
    assert json.loads(result.stdout)[0]["title"] == "Solo"


def test_bad_format_is_rejected() -> None:
    result = runner.invoke(cli_main.app, ["--format", "yaml", "version"])
    assert result.exit_code != 0


def test_raw_call_previews_a_write_and_refuses(cli: FakeTransport) -> None:
    result = runner.invoke(
        cli_main.app, ["call", "--request", json.dumps({"@type": "sendMessage", "chat_id": 1})]
    )
    assert result.exit_code == 2
    assert "sendMessage" not in _sent(cli), "a previewed write must never reach TDLib"


def test_raw_call_previews_a_destructive_and_refuses(cli: FakeTransport) -> None:
    request = json.dumps({"@type": "deleteChatHistory", "chat_id": 1})
    result = runner.invoke(cli_main.app, ["call", "--request", request])
    assert result.exit_code == 2
    assert "deleteChatHistory" not in _sent(cli)


def test_raw_call_performs_a_write_with_yes(cli: FakeTransport) -> None:
    cli.add_simple_response("sendMessage", {"@type": "message", "id": 99})
    request = json.dumps({"@type": "sendMessage", "chat_id": 1})
    result = runner.invoke(cli_main.app, ["--yes", "call", "--request", request])
    assert result.exit_code == 0
    assert "sendMessage" in _sent(cli)
    assert _lines(result)[0]["id"] == 99


def test_raw_call_rejects_unknown_method(cli: FakeTransport) -> None:
    """The registry fails closed, so an unknown method cannot slip through."""
    request = json.dumps({"@type": "notARealTdlibMethod"})
    result = runner.invoke(cli_main.app, ["--yes", "call", "--request", request])
    assert result.exit_code == 2
    assert "notARealTdlibMethod" not in _sent(cli)


def test_raw_call_rejects_malformed_json(cli: FakeTransport) -> None:
    result = runner.invoke(cli_main.app, ["call", "--request", "{not json"])
    assert result.exit_code == 2


def test_raw_call_requires_a_type(cli: FakeTransport) -> None:
    result = runner.invoke(cli_main.app, ["call", "--request", '{"chat_id": 1}'])
    assert result.exit_code == 2


def test_telegram_error_becomes_an_envelope(cli: FakeTransport) -> None:
    cli.add_simple_response("getMe", {"@type": "error", "code": 401, "message": "Unauthorized"})
    result = runner.invoke(cli_main.app, ["account", "info"])
    assert result.exit_code == 1
    envelope = _lines(result)[0]
    assert envelope["ok"] is False
    assert envelope["error"]["code"] == 401
    assert envelope["error"]["method"] == "getMe"


def test_chat_history_applies_filters(cli: FakeTransport) -> None:
    messages = [
        {"@type": "message", "id": 3, "chat_id": 5, "date": 2_000_000_000,
         "content": {"@type": "messageText", "text": {"text": "keep this"}}},
        {"@type": "message", "id": 2, "chat_id": 5, "date": 2_000_000_000,
         "content": {"@type": "messageText", "text": {"text": "drop"}}},
    ]
    cli.add_simple_response("searchPublicChat", {"@type": "chat", "id": 5})
    cli.add_response(
        lambda r: r.get("@type") == "getChatHistory",
        lambda r: {"@type": "messages", "messages": messages if r["from_message_id"] == 0 else []},
    )
    result = runner.invoke(
        cli_main.app, ["chat", "history", "--chat", "somechat", "--contains", "keep"]
    )
    assert result.exit_code == 0
    records = _lines(result)
    assert [r["text"] for r in records] == ["keep this"]


def test_updates_follow_filters_by_type(cli: FakeTransport) -> None:
    from tdelegram.api import updates as updates_api

    events = [
        {"@type": "updateNewMessage"},
        {"@type": "updateChatTitle"},
        {"@type": "updateNewMessage"},
    ]

    def _fake_follow(client: Any, types: list[str], **kwargs: Any) -> Any:
        return (e for e in events if not types or e["@type"] in types)

    original = updates_api.follow_filtered
    updates_api.follow_filtered = _fake_follow  # type: ignore[assignment]
    try:
        result = runner.invoke(
            cli_main.app, ["updates", "follow", "--types", "updateNewMessage"]
        )
    finally:
        updates_api.follow_filtered = original  # type: ignore[assignment]
    assert result.exit_code == 0
    assert len(_lines(result)) == 2


# Every mutating command, the TDLib method it must NOT reach without --yes,
# and how to invoke it. This is the product's headline claim, so it is
# asserted across the whole surface rather than on one sample command.
MUTATING_COMMANDS = [
    ("createNewSupergroupChat", ["chat", "create", "New Group"]),
    ("leaveChat", ["chat", "leave", "somechat"]),
    ("sendMessage", ["msg", "send", "--chat", "somechat", "--text", "hi"]),
    ("editMessageText", ["msg", "edit", "--chat", "somechat", "--id", "5", "--text", "hi"]),
    ("deleteMessages", ["msg", "delete", "--chat", "somechat", "--id", "5"]),
    ("addMessageReaction", ["msg", "react", "--chat", "somechat", "--id", "5"]),
    ("setPollAnswer", ["msg", "poll", "--chat", "somechat", "--id", "5", "--option", "0"]),
    ("banChatMember", ["admin", "ban", "--chat", "somechat", "--user", "2"]),
    ("setChatMemberStatus", ["admin", "promote", "--chat", "somechat", "--user", "2"]),
    ("setChatDraftMessage", ["draft", "set", "--chat", "somechat", "--text", "hi"]),
    ("createNewSecretChat", ["secret", "create", "2"]),
    # Sent the file without --yes: send_file granted itself allow_write=True.
    ("sendMessage", ["media", "upload", "--chat", "somechat", "--path", "/tmp/cv.pdf"]),
    ("addProxy", ["proxy", "add", "socks5://127.0.0.1:9050"]),
    ("enableProxy", ["proxy", "enable", "1"]),
    ("disableProxy", ["proxy", "disable"]),
    ("removeProxy", ["proxy", "remove", "1"]),
    ("getInlineQueryResults", ["bot", "inline", "--bot", "1", "--query", "x"]),
    ("logOut", ["auth", "logout"]),
]


@pytest.mark.parametrize("method,argv", MUTATING_COMMANDS, ids=[m for m, _ in MUTATING_COMMANDS])
def test_mutating_command_previews_without_yes(
    cli: FakeTransport, method: str, argv: list[str]
) -> None:
    """No mutating CLI command may reach TDLib without an explicit --yes."""
    cli.add_simple_response("searchPublicChat", {"@type": "chat", "id": 5})
    result = runner.invoke(cli_main.app, argv)
    assert result.exit_code == 2, f"{' '.join(argv)} should preview and exit 2"
    assert method not in _sent(cli), f"{method} reached TDLib without --yes"


def test_gate_does_not_depend_on_the_call_site(cli: FakeTransport) -> None:
    """run_call must consult the registry, not a per-command annotation.

    `bot inline` reached TDLib unconfirmed because its call site declared no
    "this is a write" hint, and run_call granted permission whenever the hint
    was absent. The registry is the only thing that may decide.
    """
    from tdelegram.cli.context import run_call

    with pytest.raises(SystemExit) as exc:
        run_call(cli_main.Ctx(), "sendMessage", {"chat_id": 1})
    assert exc.value.code == 2
    assert "sendMessage" not in _sent(cli)


DELETE = ["msg", "delete", "--chat", "somechat", "--id", "5"]


def _scripted_delete(cli: FakeTransport) -> None:
    cli.add_simple_response("searchPublicChat", {"@type": "chat", "id": 5})
    cli.add_simple_response("deleteMessages", {"@type": "ok"})


def test_destructive_needs_more_than_yes_without_a_terminal(
    cli: FakeTransport, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Regression: --yes alone performed every destructive call.

    The docs, the skill and the changelog all describe the typed method name
    as a second layer on top of --yes. It was an alternative instead, so a
    script or an agent holding --yes deleted messages with nobody asked.
    """
    _scripted_delete(cli)
    monkeypatch.setattr(cli_context, "interactive", lambda: False)
    result = runner.invoke(cli_main.app, ["--yes", *DELETE])
    assert result.exit_code == 2
    assert "deleteMessages" not in _sent(cli), "a destructive call ran on --yes alone"
    assert "interactive terminal" in result.stderr


def test_destructive_runs_once_its_name_is_typed(
    cli: FakeTransport, monkeypatch: pytest.MonkeyPatch
) -> None:
    _scripted_delete(cli)
    monkeypatch.setattr(cli_context, "interactive", lambda: True)
    result = runner.invoke(cli_main.app, ["--yes", *DELETE], input="deleteMessages\n")
    assert result.exit_code == 0
    assert "deleteMessages" in _sent(cli)
    # The prompt is a diagnostic, so it must stay out of the data stream.
    assert "Type deleteMessages" in result.stderr
    assert "Type deleteMessages" not in result.stdout


def test_destructive_refuses_a_mistyped_name(
    cli: FakeTransport, monkeypatch: pytest.MonkeyPatch
) -> None:
    _scripted_delete(cli)
    monkeypatch.setattr(cli_context, "interactive", lambda: True)
    result = runner.invoke(cli_main.app, ["--yes", *DELETE], input="deleteMessage\n")
    assert result.exit_code == 2
    assert "deleteMessages" not in _sent(cli)


def test_typing_the_name_is_not_a_substitute_for_yes(
    cli: FakeTransport, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Regression: at a terminal, typing the name performed without --yes."""
    _scripted_delete(cli)
    monkeypatch.setattr(cli_context, "interactive", lambda: True)
    result = runner.invoke(cli_main.app, DELETE, input="deleteMessages\n")
    assert result.exit_code == 2
    assert "deleteMessages" not in _sent(cli)
    assert "Type deleteMessages" not in result.stderr, "no --yes means no prompt at all"


def test_raw_call_holds_destructive_to_the_same_rule(
    cli: FakeTransport, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(cli_context, "interactive", lambda: False)
    request = json.dumps({"@type": "deleteChatHistory", "chat_id": 1})
    result = runner.invoke(cli_main.app, ["--yes", "call", "--request", request])
    assert result.exit_code == 2
    assert "deleteChatHistory" not in _sent(cli)


def test_gate_opens_only_with_yes(cli: FakeTransport) -> None:
    cli.add_simple_response("sendMessage", {"@type": "message", "id": 1})
    ctx = cli_main.Ctx()
    ctx.yes = True
    assert run_call_ok(ctx)


def run_call_ok(ctx: Any) -> bool:
    from tdelegram.cli.context import run_call

    return run_call(ctx, "sendMessage", {"chat_id": 1})["@type"] == "message"


# --- read commands that were never exercised ------------------------------


def test_chat_info_reads_what_the_chat_object_does_not_carry(cli: FakeTransport) -> None:
    """Regression: username, member count and the forum flag were always null.

    TDLib keeps them on the supergroup (or user, or basic group), not on the
    chat, and the record read them from the chat.
    """
    cli.add_simple_response(
        "searchPublicChat",
        {
            "@type": "chat",
            "id": -1001246902558,
            "title": "Group",
            "type": {"@type": "chatTypeSupergroup", "supergroup_id": 1246902558},
        },
    )
    cli.add_simple_response(
        "getSupergroup",
        {
            "@type": "supergroup",
            "id": 1246902558,
            "usernames": {"@type": "usernames", "active_usernames": ["cyprusithr", "cyit"]},
            "member_count": 5400,
            "is_forum": True,
        },
    )
    result = runner.invoke(cli_main.app, ["chat", "info", "somechat"])
    assert result.exit_code == 0
    record = _lines(result)[0]
    assert record["title"] == "Group"
    assert record["is_forum"] is True
    assert record["username"] == "cyprusithr" and record["usernames"] == ["cyprusithr", "cyit"]
    assert record["member_count"] == 5400


def test_chat_resolve_emits_the_raw_chat(cli: FakeTransport) -> None:
    cli.add_simple_response("searchPublicChat", {"@type": "chat", "id": -100, "title": "Group"})
    result = runner.invoke(cli_main.app, ["chat", "resolve", "somechat"])
    assert result.exit_code == 0
    assert _lines(result)[0]["@type"] == "chat"


@pytest.mark.parametrize(
    "argv",
    [
        ["chat", "resolve", "-1001246902558"],
        ["chat", "info", "-1001246902558"],
        ["chat", "members", "-1001246902558"],
        ["topic", "list", "-1001246902558"],
    ],
    ids=lambda a: " ".join(a[:2]),
)
def test_negative_chat_ids_are_accepted_positionally(
    cli: FakeTransport, argv: list[str]
) -> None:
    """Group and channel ids are negative, and were parsed as option clusters.

    `tdelegram chat info -1001246902558` exited 2 with no output, so the
    ordinary identifier for a supergroup could not be passed at all without
    the `--` escape.
    """
    cli.add_simple_response(
        "getChat",
        {
            "@type": "chat",
            "id": -1001246902558,
            "title": "By id",
            "type": {"@type": "chatTypeSupergroup", "supergroup_id": 1246902558},
        },
    )
    cli.add_simple_response("getSupergroupMembers", {"@type": "chatMembers", "total_count": 0})
    cli.add_simple_response("getForumTopics", {"@type": "forumTopics", "topics": []})
    result = runner.invoke(cli_main.app, argv)
    assert result.exit_code == 0, f"{' '.join(argv)} was refused by the parser"
    assert "searchPublicChat" not in _sent(cli), "a numeric ref must not be a username lookup"


def test_usernames_still_resolve(cli: FakeTransport) -> None:
    cli.add_simple_response("searchPublicChat", {"@type": "chat", "id": -100, "title": "By name"})
    result = runner.invoke(cli_main.app, ["chat", "resolve", "cyprusithr"])
    assert result.exit_code == 0
    assert "searchPublicChat" in _sent(cli)


def test_help_survives_the_permissive_parser(cli: FakeTransport) -> None:
    result = runner.invoke(cli_main.app, ["chat", "info", "--help"])
    assert result.exit_code == 0
    assert "Usage" in result.stdout


def test_chat_search_emits_the_records_history_does(cli: FakeTransport) -> None:
    """Regression: search emitted raw TDLib messages, history flat records.

    The same message had two shapes depending on how it was found, so `.text`
    was null on one of them -- the bug `msg get` had.
    """
    found = {
        "@type": "message",
        "id": 1,
        "chat_id": 5,
        "content": {"@type": "messageText", "text": {"@type": "formattedText", "text": "hi there"}},
    }
    cli.add_simple_response("searchPublicChat", {"@type": "chat", "id": 5})
    cli.add_response(
        lambda r: r.get("@type") == "searchChatMessages",
        lambda r: {
            "@type": "foundChatMessages",
            "messages": [] if r.get("from_message_id") else [found],
            "next_from_message_id": 0,
        },
    )
    result = runner.invoke(
        cli_main.app,
        ["chat", "search", "--chat", "somechat", "--query", "hi", "--sender", "7"],
    )
    assert result.exit_code == 0
    record = _lines(result)[0]
    assert record["message_id"] == 1 and record["text"] == "hi there"
    call = next(req for _, req in cli.sent if req.get("@type") == "searchChatMessages")
    assert call["sender_id"] == {"@type": "messageSenderUser", "user_id": 7}


def test_chat_members_asks_for_the_supergroup_not_the_chat(cli: FakeTransport) -> None:
    """Regression: the chat id went where TDLib wants the supergroup id.

    -1001246902558 is supergroup 1246902558. The command passed the unresolved
    reference itself, so neither a username nor a numeric id could work.
    """
    cli.add_simple_response(
        "searchPublicChat",
        {
            "@type": "chat",
            "id": -1001246902558,
            "type": {"@type": "chatTypeSupergroup", "supergroup_id": 1246902558},
        },
    )
    cli.add_simple_response("getSupergroupMembers", {"@type": "chatMembers", "total_count": 2})
    result = runner.invoke(cli_main.app, ["chat", "members", "somechat"])
    assert result.exit_code == 0
    assert _lines(result)[0]["total_count"] == 2
    call = next(req for _, req in cli.sent if req.get("@type") == "getSupergroupMembers")
    assert call["supergroup_id"] == 1246902558


def test_chat_members_reads_a_basic_group_from_its_full_info(cli: FakeTransport) -> None:
    cli.add_simple_response(
        "searchPublicChat",
        {"@type": "chat", "id": -42, "type": {"@type": "chatTypeBasicGroup", "basic_group_id": 42}},
    )
    member = {"@type": "chatMember", "member_id": {"@type": "messageSenderUser", "user_id": 7}}
    cli.add_simple_response(
        "getBasicGroupFullInfo", {"@type": "basicGroupFullInfo", "members": [member, member]}
    )
    result = runner.invoke(cli_main.app, ["chat", "members", "somegroup", "--limit", "1"])
    assert result.exit_code == 0
    assert _lines(result)[0]["total_count"] == 2
    assert len(_lines(result)[0]["members"]) == 1


def test_chat_members_of_a_private_chat_is_a_clear_error(cli: FakeTransport) -> None:
    cli.add_simple_response(
        "searchPublicChat", {"@type": "chat", "id": 7, "type": {"@type": "chatTypePrivate"}}
    )
    result = runner.invoke(cli_main.app, ["chat", "members", "someone"])
    assert result.exit_code == 1
    assert "no member list" in _lines(result)[0]["error"]["message"]


def test_msg_get_fetches_one(cli: FakeTransport) -> None:
    cli.add_simple_response("searchPublicChat", {"@type": "chat", "id": 5})
    cli.add_simple_response("getMessage", {"@type": "message", "id": 42, "chat_id": 5})
    result = runner.invoke(cli_main.app, ["msg", "get", "--chat", "somechat", "--id", "42"])
    assert result.exit_code == 0
    assert _lines(result)[0]["message_id"] == 42


def test_msg_search_streams_global_results(cli: FakeTransport) -> None:
    cli.add_response(
        lambda r: r.get("@type") == "searchMessages",
        lambda r: {
            "@type": "messages",
            "messages": [] if r.get("from_message_id") else [
                {"@type": "message", "id": 7, "chat_id": 5,
                 "content": {"@type": "messageText", "text": {"text": "found"}}}
            ],
            "next_from_message_id": 0,
        },
    )
    result = runner.invoke(cli_main.app, ["msg", "search", "--query", "found"])
    assert result.exit_code == 0
    assert _lines(result)[0]["text"] == "found"


def test_topic_list_streams(cli: FakeTransport) -> None:
    cli.add_simple_response("searchPublicChat", {"@type": "chat", "id": 5})
    cli.add_simple_response(
        "getForumTopics",
        {"@type": "forumTopics", "topics": [{"@type": "forumTopic", "info": {"name": "General"}}]},
    )
    result = runner.invoke(cli_main.app, ["topic", "list", "somechat"])
    assert result.exit_code == 0


def test_account_sessions(cli: FakeTransport) -> None:
    cli.add_simple_response("getActiveSessions", {"@type": "sessions", "sessions": []})
    result = runner.invoke(cli_main.app, ["account", "sessions"])
    assert result.exit_code == 0
    assert _lines(result)[0]["@type"] == "sessions"


def test_auth_logout_is_destructive(cli: FakeTransport) -> None:
    result = runner.invoke(cli_main.app, ["auth", "logout"])
    assert result.exit_code == 2
    assert "logOut" not in _sent(cli)


def test_media_download_is_permitted_as_a_read(cli: FakeTransport) -> None:
    """downloadFile writes to local disk only, so it is deliberately a read."""
    cli.add_simple_response(
        "downloadFile",
        {"@type": "file", "id": 3, "local": {"path": "/tmp/f", "is_downloading_completed": True}},
    )
    result = runner.invoke(cli_main.app, ["media", "download", "3"])
    assert result.exit_code == 0
    assert "downloadFile" in _sent(cli)
    # The documented recipe reads `.path`; the raw file object keeps it under
    # `.local.path`, so the recipe printed null.
    record = _lines(result)[0]
    assert record["path"] == "/tmp/f" and record["completed"] is True


# --- --session-dir semantics ----------------------------------------------


def test_session_dir_is_the_profile_directory(tmp_path: Any) -> None:
    """No path sniffing: the flag names the profile dir, whatever it is called."""
    from tdelegram.cli.context import Ctx, base_dir

    for name in ("profiles-backup/data", "profiles/work", "session"):
        target = tmp_path / name
        assert base_dir(Ctx(session_dir=str(target))) == target


def test_base_dir_defaults_to_the_home(monkeypatch: pytest.MonkeyPatch, tmp_path: Any) -> None:
    from tdelegram.cli.context import Ctx, base_dir

    monkeypatch.setenv("TDELEGRAM_HOME", str(tmp_path / "home"))
    assert base_dir(Ctx()) == tmp_path / "home"


# Commands that take a chat reference and must resolve it before calling.
# These passed `--chat` straight into `chat_id`, so TDLib answered a username
# with "Can't parse as an integer string" and only numeric ids worked.
CHAT_REF_COMMANDS = [
    ("getMessageLink", ["msg", "link", "--chat", "somechat", "--id", "5"]),
    ("getChatArchivedStories", ["story", "list", "somechat"]),
    ("joinChat", ["--yes", "chat", "join", "somechat"]),
    ("leaveChat", ["--yes", "chat", "leave", "somechat"]),
    ("editMessageText", ["--yes", "msg", "edit", "--chat", "somechat", "--id", "5", "--text", "x"]),
    ("deleteMessages", ["--yes", "msg", "delete", "--chat", "somechat", "--id", "5"]),
    ("addMessageReaction", ["--yes", "msg", "react", "--chat", "somechat", "--id", "5"]),
    ("setPollAnswer", ["--yes", "msg", "poll", "--chat", "somechat", "--id", "5", "--option", "0"]),
    ("banChatMember", ["--yes", "admin", "ban", "--chat", "somechat", "--user", "2"]),
    ("setChatMemberStatus", ["--yes", "admin", "promote", "--chat", "somechat", "--user", "2"]),
    ("setChatDraftMessage", ["--yes", "draft", "set", "--chat", "somechat", "--text", "x"]),
    ("forwardMessages", ["--yes", "msg", "forward", "--from", "somechat", "--to", "somechat",
                         "--id", "5"]),
]


@pytest.mark.parametrize("method,argv", CHAT_REF_COMMANDS, ids=[m for m, _ in CHAT_REF_COMMANDS])
def test_chat_references_are_resolved_before_the_call(
    cli: FakeTransport, method: str, argv: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    cli.add_simple_response("searchPublicChat", {"@type": "chat", "id": -100123})
    cli.add_simple_response(method, {"@type": "ok"})
    # Destructive commands also want their method name typed at a terminal;
    # writes never read it.
    monkeypatch.setattr(cli_context, "interactive", lambda: True)
    result = runner.invoke(cli_main.app, argv, input=f"{method}\n")
    assert result.exit_code == 0, f"{' '.join(argv)} failed"

    sent = _sent(cli)
    assert "searchPublicChat" in sent, "the username was never resolved"
    call = next(req for _, req in cli.sent if req.get("@type") == method)
    for field in ("chat_id", "from_chat_id"):
        if field in call:
            assert isinstance(call[field], int), (
                f"{method} got {field}={call[field]!r}; TDLib needs an integer"
            )


@pytest.mark.parametrize("ref", ["me", "self", "saved", "Me", "@me"])
def test_self_reference_resolves_to_saved_messages(cli: FakeTransport, ref: str) -> None:
    """`me` names Saved Messages, the private chat with yourself.

    The README's quickstart used `--chat me` from the start; it resolved as a
    username and came back USERNAME_INVALID.
    """
    cli.add_simple_response("getMe", {"@type": "user", "id": 777})
    cli.add_simple_response("getChat", {"@type": "chat", "id": 777, "title": "Saved Messages"})
    result = runner.invoke(cli_main.app, ["chat", "info", ref])
    assert result.exit_code == 0
    assert _lines(result)[0]["chat_id"] == 777
    assert "searchPublicChat" not in _sent(cli), f"{ref!r} must not be looked up as a username"


def test_msg_get_returns_the_same_shape_as_chat_history(cli: FakeTransport) -> None:
    """Regression: `msg get` returned the raw TDLib object, so `.text` was null.

    The same message had two shapes depending on how it was fetched, which made
    a documented recipe (`msg get ... | jq -r '.text'`) silently yield null.
    """
    raw = {
        "@type": "message",
        "id": 42,
        "chat_id": 5,
        "date": 1700000000,
        "content": {"@type": "messageText",
                    "text": {"@type": "formattedText", "text": "hello there", "entities": []}},
    }
    cli.add_simple_response("searchPublicChat", {"@type": "chat", "id": 5})
    cli.add_simple_response("getMessage", raw)
    result = runner.invoke(cli_main.app, ["msg", "get", "--chat", "somechat", "--id", "42"])
    assert result.exit_code == 0
    record = _lines(result)[0]
    assert record["text"] == "hello there", "text must be flat, as chat history returns it"
    assert record["message_id"] == 42
    assert "date" in record and isinstance(record["date"], str), "date normalized to ISO-8601"


def test_optional_secrets_resolve_to_blank_when_unaskable(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Any
) -> None:
    """Regression: an absent database key made a usable session look broken.

    The key is optional -- an unencrypted database has none -- but the
    non-interactive provider raised rather than answering blank, so
    `auth status` reported the session unauthorized and blamed `api_id`,
    which had resolved perfectly well. This is what a container without a
    keychain looks like.
    """
    from tdelegram import credentials as creds
    from tdelegram.auth import NonInteractiveCredentialProvider, SecretUnavailable

    for var in ("TELEGRAM_DB_KEY", "TELEGRAM_API_HASH"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setattr(creds, "os_store_get", lambda service, account: None)

    provider = NonInteractiveCredentialProvider(tmp_path)
    assert provider.get_database_key() == "", "an optional secret must answer blank"

    # A required one must still refuse, or the failure would be silent.
    with pytest.raises(SecretUnavailable):
        provider.get_api_hash()


def test_auth_status_names_the_secret_that_actually_blocked(cli: FakeTransport) -> None:
    """`needs` was inferred from the state, so it named the wrong credential."""
    from tdelegram import credentials as creds

    cli._rules.clear()
    cli.add_simple_response(
        "getAuthorizationState", {"@type": "authorizationStateWaitTdlibParameters"}
    )
    import os

    os.environ.pop("TELEGRAM_API_HASH", None)
    original = creds.os_store_get
    creds.os_store_get = lambda service, account: None  # type: ignore[assignment]
    try:
        result = runner.invoke(cli_main.app, ["auth", "status"])
    finally:
        creds.os_store_get = original  # type: ignore[assignment]
    assert result.exit_code == 0
    status = _lines(result)[0]
    assert status["authorized"] is False
    assert "hash" in (status["needs"] or "").lower(), (
        f"needs should name the credential that failed, got {status['needs']!r}"
    )


# --- requests TDLib would have run with their arguments dropped -----------


def _request(cli: FakeTransport, method: str) -> dict[str, Any]:
    return next(req for _, req in cli.sent if req.get("@type") == method)


def test_raw_call_refuses_what_tdlib_would_silently_drop(cli: FakeTransport) -> None:
    """TDLib ignores an unknown field, so this would react with nothing."""
    request = json.dumps(
        {"@type": "addMessageReaction", "chat_id": 1, "message_id": 5, "emoji": "👍"}
    )
    result = runner.invoke(cli_main.app, ["--yes", "call", "--request", request])
    assert result.exit_code == 2
    assert "addMessageReaction" not in _sent(cli)
    assert "reaction_type" in result.stderr, "the refusal should say what the fields are"


def test_raw_call_validation_can_be_waived_for_a_newer_tdlib(cli: FakeTransport) -> None:
    request = json.dumps({"@type": "getChat", "chat_id": 1, "future_field": True})
    cli.add_simple_response("getChat", {"@type": "chat", "id": 1})
    refused = runner.invoke(cli_main.app, ["call", "--request", request])
    assert refused.exit_code == 2
    # The fake still records the field as unknown to the pinned schema, so
    # only the transport-level check is switched off here.
    cli._validate = False
    waived = runner.invoke(cli_main.app, ["call", "--no-validate", "--request", request])
    assert waived.exit_code == 0
    assert _lines(waived)[0]["id"] == 1


def test_describe_shows_parameters_and_the_verdict(cli: FakeTransport) -> None:
    result = runner.invoke(cli_main.app, ["describe", "deleteMessages"])
    assert result.exit_code == 0
    entry = _lines(result)[0]
    assert entry["params"] == {"chat_id": "int53", "message_ids": "vector<int53>", "revoke": "Bool"}
    assert entry["verdict"] == "destructive"
    assert cli.sent == [], "describe reads the schema, not TDLib"


def test_describe_suggests_close_names(cli: FakeTransport) -> None:
    result = runner.invoke(cli_main.app, ["describe", "getChatFolders"])
    assert result.exit_code == 1
    assert "getChatFolder" in _lines(result)[0]["error"]["message"]


def test_scheduling_goes_inside_the_send_options(cli: FakeTransport) -> None:
    """Regression: scheduling_state sat at the top level of sendMessage.

    sendMessage has no such field there, so TDLib ignored it and a message
    meant for tomorrow went out at once.
    """
    cli.add_simple_response("searchPublicChat", {"@type": "chat", "id": 5})
    cli.add_simple_response("sendMessage", {"@type": "message", "id": 9})
    argv = ["--yes", "msg", "send", "--chat", "somechat", "--text", "later"]
    result = runner.invoke(cli_main.app, [*argv, "--schedule", "2h", "--silent"])
    assert result.exit_code == 0
    call = _request(cli, "sendMessage")
    assert "scheduling_state" not in call
    options = call["options"]
    assert options["scheduling_state"]["@type"] == "messageSchedulingStateSendAtDate"
    assert options["disable_notification"] is True


def test_a_schedule_in_the_past_is_refused(cli: FakeTransport) -> None:
    argv = ["--yes", "msg", "send", "--chat", "somechat", "--text", "x"]
    result = runner.invoke(cli_main.app, [*argv, "--schedule", "2001-01-01"])
    assert result.exit_code == 1
    assert "sendMessage" not in _sent(cli)


def test_msg_delete_deletes_for_everyone_unless_told_otherwise(
    cli: FakeTransport, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Regression: revoke was never set, so a private chat kept every message."""
    _scripted_delete(cli)
    monkeypatch.setattr(cli_context, "interactive", lambda: True)
    argv = ["--yes", *DELETE, "--id", "6"]
    result = runner.invoke(cli_main.app, argv, input="deleteMessages\n")
    assert result.exit_code == 0
    call = _request(cli, "deleteMessages")
    assert call["revoke"] is True and call["message_ids"] == [5, 6]
    cli.sent.clear()
    result = runner.invoke(cli_main.app, [*argv, "--only-for-me"], input="deleteMessages\n")
    assert _request(cli, "deleteMessages")["revoke"] is False


def test_promote_grants_the_rights_asked_for(
    cli: FakeTransport, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Regression: the status carried a title and no rights, granting nothing."""
    cli.add_simple_response("searchPublicChat", {"@type": "chat", "id": -100123})
    monkeypatch.setattr(cli_context, "interactive", lambda: True)
    argv = ["--yes", "admin", "promote", "--chat", "somechat", "--user", "2"]
    result = runner.invoke(
        cli_main.app,
        [*argv, "--right", "delete_messages", "--right", "can_pin_messages", "--title", "Mod"],
        input="setChatMemberStatus\n",
    )
    assert result.exit_code == 0
    rights = _request(cli, "setChatMemberStatus")["status"]["rights"]
    assert rights["can_delete_messages"] is True and rights["can_pin_messages"] is True
    assert "can_promote_members" not in rights, "only what was asked for"
    assert _request(cli, "setChatMemberTag")["tag"] == "Mod"


def test_promote_rejects_a_misspelled_right_before_anything_is_sent(cli: FakeTransport) -> None:
    argv = ["--yes", "admin", "promote", "--chat", "x", "--user", "2", "--right", "delete_msgs"]
    result = runner.invoke(cli_main.app, argv)
    assert result.exit_code == 1
    assert "delete_messages" in _lines(result)[0]["error"]["message"]
    assert cli.sent == []


def test_folder_list_reads_the_update_tdlib_pushes(cli: FakeTransport) -> None:
    """Regression: it asked for getChatFolders, which TDLib does not have."""
    cli.add_update(
        {
            "@type": "updateChatFolders",
            "chat_folders": [
                {"@type": "chatFolderInfo", "id": 2, "name": {"text": {"text": "News"}}},
                {"@type": "chatFolderInfo", "id": 3, "name": {"text": {"text": "Work"}}},
            ],
        }
    )
    result = runner.invoke(cli_main.app, ["folder", "list"])
    assert result.exit_code == 0
    assert [(r["folder_id"], r["name"]) for r in _lines(result)] == [(2, "News"), (3, "Work")]


def test_a_preview_flags_a_request_tdlib_would_not_honour(
    capsys: pytest.CaptureFixture[str],
) -> None:
    from tdelegram.errors import WriteConfirmationRequired

    request = {"@type": "addMessageReaction", "emoji": "x"}
    cli_context.show_preview(WriteConfirmationRequired("addMessageReaction", request))
    body = json.loads(capsys.readouterr().err)["confirmation_required"]
    assert body["schema_problems"], "approving the preview would approve a different call"


def test_history_names_each_sender_once(cli: FakeTransport) -> None:
    """Records name their senders, looked up once per sender, not per message."""
    said = [
        {"@type": "message", "id": 3, "chat_id": 5, "date": 2_000_000_000,
         "sender_id": {"@type": "messageSenderUser", "user_id": 42},
         "content": {"@type": "messageText", "text": {"text": "second"}}},
        {"@type": "message", "id": 2, "chat_id": 5, "date": 2_000_000_000,
         "sender_id": {"@type": "messageSenderUser", "user_id": 42},
         "content": {"@type": "messageText", "text": {"text": "first"}}},
        {"@type": "message", "id": 1, "chat_id": 5, "date": 2_000_000_000,
         "sender_id": {"@type": "messageSenderChat", "chat_id": -1009},
         "content": {"@type": "messageText", "text": {"text": "as the channel"}}},
    ]
    cli.add_simple_response("searchPublicChat", {"@type": "chat", "id": 5})
    cli.add_response(
        lambda r: r.get("@type") == "getChatHistory",
        lambda r: {"@type": "messages", "messages": said if r["from_message_id"] == 0 else []},
    )
    cli.add_simple_response("getUser", {"@type": "user", "id": 42, "first_name": "Ada"})
    cli.add_simple_response("getChat", {"@type": "chat", "id": -1009, "title": "Newsroom"})
    result = runner.invoke(cli_main.app, ["chat", "history", "--chat", "somechat"])
    assert result.exit_code == 0
    assert [r["sender_name"] for r in _lines(result)] == ["Ada", "Ada", "Newsroom"]
    assert _sent(cli).count("getUser") == 1


# --- proxies, which have to work before there is a login -----------------

SECRET = "ee1603010200010001fc030386e24c3add6e2e616b616d61692e636f6d"
PROXY_LINK = f"https://t.me/proxy?server=proxy.example.org&port=443&secret={SECRET}"


def _stored(cli: FakeTransport, *, enabled: bool = True) -> None:
    cli.add_simple_response(
        "getProxies",
        {
            "@type": "addedProxies",
            "proxies": [
                {
                    "@type": "addedProxy",
                    "id": 1,
                    "is_enabled": enabled,
                    "last_used_date": 1_700_000_000,
                    "proxy": {
                        "@type": "proxy",
                        "server": "proxy.example.org",
                        "port": 443,
                        "type": {"@type": "proxyTypeMtproto", "secret": SECRET},
                    },
                }
            ],
        },
    )


def test_a_proxy_can_be_added_before_logging_in(cli: FakeTransport) -> None:
    """Behind a block, the proxy has to exist before a login can reach Telegram."""
    cli._rules.clear()
    cli.add_simple_response(
        "getAuthorizationState", {"@type": "authorizationStateWaitPhoneNumber"}
    )
    cli.add_response(
        lambda r: r.get("@type") == "addProxy",
        lambda r: {"@type": "addedProxy", "id": 1, "is_enabled": True, "proxy": r["proxy"]},
    )
    result = runner.invoke(cli_main.app, ["--yes", "proxy", "add", PROXY_LINK])
    assert result.exit_code == 0
    sent = _sent(cli)
    assert "setAuthenticationPhoneNumber" not in sent, "adding a proxy must not start a login"
    call = _request(cli, "addProxy")
    assert call["enable"] is True
    assert call["proxy"]["type"] == {"@type": "proxyTypeMtproto", "secret": SECRET}
    record = _lines(result)[0]
    assert record["proxy_id"] == 1 and record["type"] == "mtproto"
    assert SECRET not in result.stdout, "the secret must not be echoed"


def test_proxy_add_previews_the_parsed_proxy_without_yes(cli: FakeTransport) -> None:
    result = runner.invoke(cli_main.app, ["proxy", "add", "socks5://127.0.0.1:9050"])
    assert result.exit_code == 2
    assert "addProxy" not in _sent(cli)
    assert "127.0.0.1" in result.stderr


def test_proxy_add_refuses_a_link_that_is_not_a_proxy(cli: FakeTransport) -> None:
    result = runner.invoke(cli_main.app, ["--yes", "proxy", "add", "tg://resolve?domain=x"])
    assert result.exit_code == 1
    assert cli.sent == [], "a bad link must fail before the profile is opened"


def test_proxy_list_leaves_secrets_out(cli: FakeTransport) -> None:
    _stored(cli)
    result = runner.invoke(cli_main.app, ["proxy", "list"])
    assert result.exit_code == 0
    record = _lines(result)[0]
    assert record["proxy_id"] == 1 and record["is_enabled"] is True
    assert record["has_credentials"] is True
    assert SECRET not in result.stdout


def test_proxy_ping_looks_the_proxy_up_by_id(cli: FakeTransport) -> None:
    _stored(cli)
    cli.add_simple_response("pingProxy", {"@type": "seconds", "seconds": 0.21})
    result = runner.invoke(cli_main.app, ["proxy", "ping", "1"])
    assert result.exit_code == 0
    assert _lines(result)[0] == {"proxy_id": 1, "seconds": 0.21}
    assert _request(cli, "pingProxy")["proxy"]["server"] == "proxy.example.org"
    cli.sent.clear()
    direct = runner.invoke(cli_main.app, ["proxy", "ping"])
    assert direct.exit_code == 0 and _request(cli, "pingProxy")["proxy"] is None


def test_proxy_check_reports_a_proxy_that_cannot_connect(cli: FakeTransport) -> None:
    _stored(cli)
    cli.add_simple_response("testProxy", {"@type": "error", "code": 400, "message": "timeout"})
    result = runner.invoke(cli_main.app, ["proxy", "check", "1"])
    assert result.exit_code == 1
    assert _lines(result)[0]["error"]["method"] == "testProxy"


def test_proxy_commands_say_what_the_profile_needs(
    cli: FakeTransport, monkeypatch: pytest.MonkeyPatch
) -> None:
    import tdelegram.credentials as creds

    for var in ("TELEGRAM_API_ID", "TELEGRAM_API_HASH"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setattr(creds, "os_store_get", lambda service, account: None)
    cli._rules.clear()
    cli.add_simple_response(
        "getAuthorizationState", {"@type": "authorizationStateWaitTdlibParameters"}
    )
    result = runner.invoke(cli_main.app, ["proxy", "list"])
    assert result.exit_code == 1
    assert "TELEGRAM_API_ID" in _lines(result)[0]["error"]["message"]


# --- inbox: what the account has not read, without reading it ------------


def _message_in(chat_id: int, mid: int, text: str, *, outgoing: bool = False) -> dict[str, Any]:
    return {
        "@type": "message",
        "id": mid,
        "chat_id": chat_id,
        "date": 1_700_000_000 + mid,
        "is_outgoing": outgoing,
        "sender_id": {"@type": "messageSenderUser", "user_id": 7},
        "content": {"@type": "messageText", "text": {"@type": "formattedText", "text": text}},
    }


def _script_inbox(cli: FakeTransport) -> None:
    unmuted = {"use_default_mute_for": False, "mute_for": 0}
    chats = {
        10: {"title": "Friends", "unread_count": 3, "last_read_inbox_message_id": 100,
             "type": {"@type": "chatTypePrivate", "user_id": 7},
             "notification_settings": unmuted},
        11: {"title": "Read up", "unread_count": 0, "type": {"@type": "chatTypePrivate"}},
        12: {"title": "Muted noise", "unread_count": 5, "last_read_inbox_message_id": 1,
             "type": {"@type": "chatTypeBasicGroup", "basic_group_id": 12},
             "notification_settings": {"use_default_mute_for": False, "mute_for": 999_999}},
        13: {"title": "Muted channel that mentions you", "unread_count": 2,
             "unread_mention_count": 1, "last_read_inbox_message_id": 49,
             "type": {"@type": "chatTypeSupergroup", "supergroup_id": 13, "is_channel": True},
             "notification_settings": {"use_default_mute_for": True}},
    }
    histories = {
        10: [_message_in(10, 104, "see you at 8"), _message_in(10, 103, "mine", outgoing=True),
             _message_in(10, 102, "dinner?"), _message_in(10, 101, "hey"),
             _message_in(10, 100, "already read")],
        12: [_message_in(12, 9, "spam")],
        13: [_message_in(13, 51, "@you look"), _message_in(13, 50, "update"),
             _message_in(13, 49, "old")],
    }
    cli.add_simple_response("getChats", {"@type": "chats", "chat_ids": list(chats)})
    cli.add_response(
        lambda r: r.get("@type") == "getChat",
        lambda r: {"@type": "chat", "id": r["chat_id"], **chats[r["chat_id"]]},
    )
    cli.add_response(
        lambda r: r.get("@type") == "getChatHistory",
        lambda r: {
            "@type": "messages",
            "messages": histories.get(r["chat_id"], []) if r["from_message_id"] == 0 else [],
        },
    )
    cli.add_simple_response(
        "getScopeNotificationSettings", {"@type": "scopeNotificationSettings", "mute_for": 3600}
    )
    cli.add_simple_response("getUser", {"@type": "user", "id": 7, "first_name": "Sam"})


def test_inbox_lists_unread_messages_oldest_first(cli: FakeTransport) -> None:
    _script_inbox(cli)
    result = runner.invoke(cli_main.app, ["inbox"])
    assert result.exit_code == 0
    got = [(r["chat_title"], r["text"]) for r in _lines(result)]
    assert got == [
        ("Friends", "hey"),
        ("Friends", "dinner?"),
        ("Friends", "see you at 8"),
        ("Muted channel that mentions you", "update"),
        ("Muted channel that mentions you", "@you look"),
    ], "outgoing, already-read and muted-without-mention messages stay out"
    assert _lines(result)[0]["sender_name"] == "Sam"


def test_inbox_never_marks_anything_read(cli: FakeTransport) -> None:
    """Senders see read receipts. A digest must not send any."""
    _script_inbox(cli)
    runner.invoke(cli_main.app, ["inbox", "--include-muted"])
    marking = {"openChat", "viewMessages", "readAllChatMentions", "readAllChatReactions"}
    assert not marking & set(_sent(cli))


def test_inbox_keeps_the_newest_when_capped(cli: FakeTransport) -> None:
    _script_inbox(cli)
    result = runner.invoke(cli_main.app, ["inbox", "--per-chat", "2", "--chats", "1"])
    assert [r["text"] for r in _lines(result)] == ["dinner?", "see you at 8"]
    assert _lines(result)[0]["chat_unread_count"] == 3, "so a digest can say what it left out"


def test_inbox_can_include_muted_chats(cli: FakeTransport) -> None:
    _script_inbox(cli)
    result = runner.invoke(cli_main.app, ["inbox", "--include-muted"])
    assert "spam" in [r["text"] for r in _lines(result)]


def test_chat_list_can_show_only_unread_chats(cli: FakeTransport) -> None:
    _script_inbox(cli)
    result = runner.invoke(cli_main.app, ["chat", "list", "--unread", "--limit", "2"])
    assert [r["title"] for r in _lines(result)] == ["Friends", "Muted noise"]


def test_chat_export_reports_the_run(cli: FakeTransport, tmp_path: Any) -> None:
    cli.add_simple_response("searchPublicChat", {"@type": "chat", "id": 5, "title": "Kept"})
    cli.add_response(
        lambda r: r.get("@type") == "getChatHistory",
        lambda r: {
            "@type": "messages",
            "messages": [_message_in(5, 2, "b"), _message_in(5, 1, "a")]
            if r["from_message_id"] == 0
            else [],
        },
    )
    out = tmp_path / "kept"
    result = runner.invoke(cli_main.app, ["chat", "export", "--chat", "kept", "--out", str(out)])
    assert result.exit_code == 0
    summary = _lines(result)[0]
    assert summary["written"] == 2 and summary["complete"] is True
    assert (out / "messages.jsonl").exists() and (out / "state.json").exists()


# --- deleting your own messages, in bulk, with a count first --------------

ME = 777


def _script_own_messages(cli: FakeTransport, count: int, *, others: int = 0) -> None:
    mine = [
        {**_message_in(5, 1000 - i, f"mine {i}"),
         "sender_id": {"@type": "messageSenderUser", "user_id": ME}}
        for i in range(count)
    ]
    theirs = [_message_in(5, 2000 + i, "theirs") for i in range(others)]
    found = sorted(mine + theirs, key=lambda m: m["id"], reverse=True)
    cli.add_simple_response("searchPublicChat", {"@type": "chat", "id": 5})
    cli.add_simple_response("getChat", {"@type": "chat", "id": 5})
    cli.add_simple_response("getMe", {"@type": "user", "id": ME})

    def _page(r: dict[str, Any]) -> dict[str, Any]:
        start = r["from_message_id"]
        page = [m for m in found if start == 0 or m["id"] < start][:100]
        return {"@type": "foundChatMessages", "messages": page, "next_from_message_id": 0}

    cli.add_response(lambda r: r.get("@type") == "searchChatMessages", _page)


def test_delete_mine_counts_first_and_deletes_nothing_without_yes(cli: FakeTransport) -> None:
    _script_own_messages(cli, 3)
    result = runner.invoke(cli_main.app, ["msg", "delete-mine", "--chat", "somechat"])
    assert result.exit_code == 2
    assert "deleteMessages" not in _sent(cli)
    assert '"count": 3' in result.stderr
    search = _request(cli, "searchChatMessages")
    assert search["sender_id"] == {"@type": "messageSenderUser", "user_id": ME}


def test_delete_mine_deletes_for_everyone_in_batches(
    cli: FakeTransport, monkeypatch: pytest.MonkeyPatch
) -> None:
    _script_own_messages(cli, 150, others=4)
    monkeypatch.setattr(cli_context, "interactive", lambda: True)
    argv = ["--yes", "msg", "delete-mine", "--chat", "somechat"]
    result = runner.invoke(cli_main.app, argv, input="deleteMessages\n")
    assert result.exit_code == 0
    batches = [req for _, req in cli.sent if req.get("@type") == "deleteMessages"]
    assert [len(b["message_ids"]) for b in batches] == [100, 50]
    assert all(b["revoke"] is True for b in batches)
    assert all(mid < 2000 for b in batches for mid in b["message_ids"]), "only your own"
    assert _lines(result)[-1]["deleted"] == 150
    assert result.stderr.count("Type deleteMessages") == 1, "one confirmation for the batch"


def test_delete_mine_refuses_without_a_terminal(
    cli: FakeTransport, monkeypatch: pytest.MonkeyPatch
) -> None:
    _script_own_messages(cli, 2)
    monkeypatch.setattr(cli_context, "interactive", lambda: False)
    result = runner.invoke(cli_main.app, ["--yes", "msg", "delete-mine", "--chat", "somechat"])
    assert result.exit_code == 2
    assert "deleteMessages" not in _sent(cli)


def test_delete_mine_steps_around_a_message_that_cannot_go(
    cli: FakeTransport, monkeypatch: pytest.MonkeyPatch
) -> None:
    _script_own_messages(cli, 3)

    def _delete(r: dict[str, Any]) -> dict[str, Any]:
        if 999 in r["message_ids"]:
            return {"@type": "error", "code": 400, "message": "MESSAGE_DELETE_FORBIDDEN"}
        return {"@type": "ok"}

    cli.add_response(lambda r: r.get("@type") == "deleteMessages", _delete)
    monkeypatch.setattr(cli_context, "interactive", lambda: True)
    argv = ["--yes", "msg", "delete-mine", "--chat", "somechat"]
    result = runner.invoke(cli_main.app, argv, input="deleteMessages\n")
    assert result.exit_code == 0
    summary = _lines(result)[-1]
    assert summary["deleted"] == 2 and summary["skipped"] == [999]


def test_delete_mine_with_nothing_to_delete_asks_nothing(cli: FakeTransport) -> None:
    _script_own_messages(cli, 0, others=2)
    result = runner.invoke(cli_main.app, ["--yes", "msg", "delete-mine", "--chat", "somechat"])
    assert result.exit_code == 0
    assert _lines(result)[0]["deleted"] == 0
    assert "Type deleteMessages" not in result.stderr


# --- watch: an alert feed of new messages --------------------------------


def _arrives(cli: FakeTransport, chat_id: int, mid: int, text: str, **extra: Any) -> None:
    message = {**_message_in(chat_id, mid, text), **extra}
    cli.add_update({"@type": "updateNewMessage", "message": message})


def test_watch_streams_matching_messages_as_records(cli: FakeTransport) -> None:
    cli.add_simple_response("searchPublicChat", {"@type": "chat", "id": 5, "title": "Jobs"})
    cli.add_response(
        lambda r: r.get("@type") == "getChat",
        lambda r: {"@type": "chat", "id": r["chat_id"], "title": f"chat {r['chat_id']}"},
    )
    _arrives(cli, 6, 1, "Hiring a Rust developer")  # another chat
    _arrives(cli, 5, 2, "lunch?")  # no keyword
    _arrives(cli, 5, 3, "We are HIRING: Python", is_outgoing=True)  # our own
    _arrives(cli, 5, 4, "Vacancy: senior Python engineer")
    _arrives(cli, 5, 5, "hiring again")
    argv = ["watch", "--chat", "jobs", "--contains", "hiring", "--contains", "vacancy"]
    result = runner.invoke(cli_main.app, [*argv, "--count", "2"])
    assert result.exit_code == 0
    records = _lines(result)
    assert [r["text"] for r in records] == ["Vacancy: senior Python engineer", "hiring again"]
    assert records[0]["chat_title"] == "chat 5" and records[0]["message_id"] == 4


def test_watch_takes_a_regular_expression(cli: FakeTransport) -> None:
    _arrives(cli, 5, 1, "code 1234")
    _arrives(cli, 5, 2, "your code is 98765")
    result = runner.invoke(cli_main.app, ["watch", "--match", r"\b\d{5}\b", "--count", "1"])
    assert [r["text"] for r in _lines(result)] == ["your code is 98765"]


def test_watch_stops_when_its_time_is_up(cli: FakeTransport) -> None:
    result = runner.invoke(cli_main.app, ["watch", "--for", "1s"])
    assert result.exit_code == 0 and _lines(result) == []


def test_watch_rejects_a_broken_pattern_up_front(cli: FakeTransport) -> None:
    result = runner.invoke(cli_main.app, ["watch", "--match", "([unclosed"])
    assert result.exit_code == 1
    assert cli.sent == []


@pytest.mark.parametrize(
    "argv",
    [
        ["msg", "send", "--chat", "me", "--text", "hi", "--yes"],
        ["chat", "list", "--format", "json"],
    ],
)
def test_a_late_global_flag_says_where_it_goes(cli: FakeTransport, argv: list[str]) -> None:
    """The README's own "performs" example put --yes last, and exited 2.

    The parser stays strict -- accepting --yes anywhere would let a message
    text of "--yes" grant permission -- but the error now says what to do.
    """
    result = runner.invoke(cli_main.app, argv)
    assert result.exit_code == 2
    assert "goes before the command" in result.output
    assert cli.sent == []


def test_a_message_reading_yes_grants_nothing(cli: FakeTransport) -> None:
    """The text is the text: it previews like any other send."""
    cli.add_simple_response("getMe", {"@type": "user", "id": 777})
    cli.add_simple_response("getChat", {"@type": "chat", "id": 777})
    result = runner.invoke(cli_main.app, ["msg", "send", "--chat", "me", "--text", "--yes"])
    assert result.exit_code == 2
    assert "sendMessage" not in _sent(cli)
    assert '"text": "--yes"' in result.stderr


# --- --format text: plain lines, in reading order ------------------------


def test_text_format_reads_a_message_as_one_plain_line() -> None:
    from tdelegram.cli.output import as_text

    record = {
        "message_id": 4,
        "content_type": "messageVoiceNote",
        "date": "2026-09-24T14:02:11+00:00",
        "sender_name": "Ada",
        "chat_title": "Friends",
        "text": "",
        "media": {"kind": "voice_note", "duration": 14, "transcript": "running late"},
    }
    assert as_text(record) == (
        '2026-09-24 14:02, Ada in Friends: [voice message, 14 seconds] transcript: "running late"'
    )


def test_text_format_keeps_each_message_on_a_line_of_its_own() -> None:
    from tdelegram.cli.output import as_text

    record = {
        "message_id": 5,
        "content_type": "messageText",
        "date": "2026-09-24T14:03:00+00:00",
        "is_outgoing": True,
        "text": "first line\nsecond line",
    }
    assert as_text(record) == "2026-09-24 14:03, you: first line\n    second line"


def test_text_format_for_chats_and_everything_else() -> None:
    from tdelegram.cli.output import as_text

    chat = {"chat_id": -1, "title": "News", "username": "news", "unread_count": 3}
    assert as_text(chat) == "News (@news), 3 unread"
    assert as_text({"proxy_id": 1, "server": "h", "comment": None}) == "proxy_id: 1; server: h"


def test_text_format_through_the_cli(cli: FakeTransport) -> None:
    cli.add_simple_response("searchPublicChat", {"@type": "chat", "id": 5})
    cli.add_response(
        lambda r: r.get("@type") == "getChatHistory",
        lambda r: {
            "@type": "messages",
            "messages": [_message_in(5, 1, "hello")] if r["from_message_id"] == 0 else [],
        },
    )
    cli.add_simple_response("getUser", {"@type": "user", "id": 7, "first_name": "Sam"})
    result = runner.invoke(cli_main.app, ["--format", "text", "chat", "history", "--chat", "x"])
    assert result.exit_code == 0
    assert result.stdout.strip().endswith(", Sam: hello")


# --- transcripts of voice messages ---------------------------------------


def _voice(transcript: str | None = None) -> dict[str, Any]:
    note: dict[str, Any] = {"duration": 9, "voice": {"@type": "file", "id": 3}}
    if transcript is not None:
        note["speech_recognition_result"] = {
            "@type": "speechRecognitionResultText",
            "text": transcript,
        }
    return {"@type": "message", "id": 8, "chat_id": 5,
            "content": {"@type": "messageVoiceNote", "voice_note": note}}


TRANSCRIBE = ["msg", "transcribe", "--chat", "somechat", "--id", "8"]


def test_a_transcript_already_made_is_free(cli: FakeTransport) -> None:
    cli.add_simple_response("searchPublicChat", {"@type": "chat", "id": 5})
    cli.add_simple_response("getChat", {"@type": "chat", "id": 5})
    cli.add_simple_response("getMessage", _voice("see you at eight"))
    result = runner.invoke(cli_main.app, TRANSCRIBE)
    assert result.exit_code == 0, "reading a kept transcript spends nothing, so needs no --yes"
    assert _lines(result)[0]["transcript"] == "see you at eight"
    assert "recognizeSpeech" not in _sent(cli)


def test_transcribing_spends_quota_so_it_needs_yes(cli: FakeTransport) -> None:
    cli.add_simple_response("searchPublicChat", {"@type": "chat", "id": 5})
    cli.add_simple_response("getChat", {"@type": "chat", "id": 5})
    cli.add_simple_response("getMessage", _voice())
    result = runner.invoke(cli_main.app, TRANSCRIBE)
    assert result.exit_code == 2
    assert "recognizeSpeech" not in _sent(cli)


def test_transcribe_waits_for_the_text_to_arrive(cli: FakeTransport) -> None:
    cli.add_simple_response("searchPublicChat", {"@type": "chat", "id": 5})
    cli.add_simple_response("getChat", {"@type": "chat", "id": 5})
    cli.add_simple_response("getMessage", _voice())

    def _recognize(request: dict[str, Any]) -> dict[str, Any]:
        cli.add_update({"@type": "updateSpeechRecognitionTrial", "left_count": 1})
        for partial in ("speechRecognitionResultPending", "speechRecognitionResultText"):
            cli.add_update(
                {
                    "@type": "updateMessageContent",
                    "chat_id": 5,
                    "message_id": 8,
                    "new_content": {
                        "@type": "messageVoiceNote",
                        "voice_note": {
                            "speech_recognition_result": {"@type": partial, "text": "on my way"}
                        },
                    },
                }
            )
        return {"@type": "ok"}

    cli.add_response(lambda r: r.get("@type") == "recognizeSpeech", _recognize)
    result = runner.invoke(cli_main.app, ["--yes", *TRANSCRIBE])
    assert result.exit_code == 0
    record = _lines(result)[0]
    assert record["transcript"] == "on my way" and record["cached"] is False
    assert record["free_left"] == 1


def test_transcribe_refuses_what_is_not_speech(cli: FakeTransport) -> None:
    cli.add_simple_response("searchPublicChat", {"@type": "chat", "id": 5})
    cli.add_simple_response("getChat", {"@type": "chat", "id": 5})
    cli.add_simple_response("getMessage", _message_in(5, 8, "just text"))
    result = runner.invoke(cli_main.app, ["--yes", *TRANSCRIBE])
    assert result.exit_code == 1
    assert "recognizeSpeech" not in _sent(cli)


# --- bots, users and contacts, as a user account can use them ------------


def _bot_message(cli: FakeTransport) -> None:
    cli.add_simple_response("searchPublicChat", {"@type": "chat", "id": 55})
    cli.add_simple_response(
        "getMessage",
        {
            "@type": "message",
            "id": 9,
            "chat_id": 55,
            "reply_markup": {
                "@type": "replyMarkupInlineKeyboard",
                "rows": [
                    [
                        {"@type": "inlineKeyboardButton", "text": "Confirm",
                         "type": {"@type": "inlineKeyboardButtonTypeCallback", "data": "b2s="}},
                        {"@type": "inlineKeyboardButton", "text": "Docs",
                         "type": {"@type": "inlineKeyboardButtonTypeUrl",
                                  "url": "https://example.org"}},
                    ]
                ],
            },
        },
    )


PRESS = ["bot", "press", "--chat", "somebot", "--id", "9", "--button"]


def test_bot_press_presses_the_button_by_its_label(cli: FakeTransport) -> None:
    """Replaces `bot callback`, which answered queries -- something only a bot can do."""
    _bot_message(cli)
    cli.add_simple_response(
        "getCallbackQueryAnswer", {"@type": "callbackQueryAnswer", "text": "Done!"}
    )
    refused = runner.invoke(cli_main.app, [*PRESS, "confirm"])
    assert refused.exit_code == 2, "the bot sees the press, so it is a write"
    assert "getCallbackQueryAnswer" not in _sent(cli)
    result = runner.invoke(cli_main.app, ["--yes", *PRESS, "confirm"])
    assert result.exit_code == 0
    assert _lines(result)[0]["answer"] == "Done!"
    payload = _request(cli, "getCallbackQueryAnswer")["payload"]
    assert payload == {"@type": "callbackQueryPayloadData", "data": "b2s="}


def test_bot_press_hands_back_a_link_button_unpressed(cli: FakeTransport) -> None:
    _bot_message(cli)
    result = runner.invoke(cli_main.app, [*PRESS, "Docs"])
    assert result.exit_code == 0
    assert _lines(result)[0] == {
        "chat_id": 55, "message_id": 9, "button": "Docs", "pressed": False,
        "url": "https://example.org",
    }


def test_bot_press_names_the_buttons_there_are(cli: FakeTransport) -> None:
    _bot_message(cli)
    result = runner.invoke(cli_main.app, ["--yes", *PRESS, "Cancel"])
    assert result.exit_code == 1
    assert "'Confirm', 'Docs'" in _lines(result)[0]["error"]["message"]


def test_user_info_takes_a_username(cli: FakeTransport) -> None:
    cli.add_simple_response(
        "searchPublicChat",
        {"@type": "chat", "id": 42, "type": {"@type": "chatTypePrivate", "user_id": 42}},
    )
    cli.add_simple_response(
        "getUser",
        {"@type": "user", "id": 42, "first_name": "Ada",
         "usernames": {"active_usernames": ["ada"]}, "type": {"@type": "userTypeRegular"}},
    )
    result = runner.invoke(cli_main.app, ["user", "info", "@ada"])
    assert result.exit_code == 0
    record = _lines(result)[0]
    assert record["user_id"] == 42 and record["username"] == "ada"


def test_user_info_refuses_a_channel(cli: FakeTransport) -> None:
    cli.add_simple_response(
        "searchPublicChat",
        {"@type": "chat", "id": -100, "type": {"@type": "chatTypeSupergroup", "is_channel": True}},
    )
    result = runner.invoke(cli_main.app, ["user", "info", "@news"])
    assert result.exit_code == 1


def test_contact_list_emits_people_not_ids(cli: FakeTransport) -> None:
    cli.add_simple_response("getContacts", {"@type": "users", "total_count": 2, "user_ids": [1, 2]})
    cli.add_response(
        lambda r: r.get("@type") == "getUser",
        lambda r: {"@type": "user", "id": r["user_id"], "first_name": f"P{r['user_id']}"},
    )
    result = runner.invoke(cli_main.app, ["contact", "list"])
    assert [(r["user_id"], r["first_name"]) for r in _lines(result)] == [(1, "P1"), (2, "P2")]


def test_inbox_resolves_default_mute_settings_per_scope(cli: FakeTransport) -> None:
    """A chat on "use the default" is muted when its scope's default mutes it."""
    defaults = {
        "notificationSettingsScopePrivateChats": 0,
        "notificationSettingsScopeGroupChats": 0,
        "notificationSettingsScopeChannelChats": 86400,
    }
    chats = {
        20: {"title": "A person", "type": {"@type": "chatTypePrivate", "user_id": 7}},
        21: {"title": "A group", "type": {"@type": "chatTypeSupergroup", "is_channel": False}},
        22: {"title": "A channel", "type": {"@type": "chatTypeSupergroup", "is_channel": True}},
        23: {"title": "Another channel", "type": {"@type": "chatTypeSupergroup",
                                                   "is_channel": True}},
    }
    for chat in chats.values():
        chat.update(unread_count=1, last_read_inbox_message_id=0,
                    notification_settings={"use_default_mute_for": True})
    cli.add_simple_response("getChats", {"@type": "chats", "chat_ids": list(chats)})
    cli.add_response(
        lambda r: r.get("@type") == "getChat",
        lambda r: {"@type": "chat", "id": r["chat_id"], **chats[r["chat_id"]]},
    )
    cli.add_response(
        lambda r: r.get("@type") == "getScopeNotificationSettings",
        lambda r: {"@type": "scopeNotificationSettings",
                   "mute_for": defaults[r["scope"]["@type"]]},
    )
    cli.add_response(
        lambda r: r.get("@type") == "getChatHistory",
        lambda r: {"@type": "messages",
                   "messages": [_message_in(r["chat_id"], 1, "hi")]
                   if r["from_message_id"] == 0 else []},
    )
    result = runner.invoke(cli_main.app, ["inbox"])
    assert [r["chat_title"] for r in _lines(result)] == ["A person", "A group"]
    scopes = [req["scope"]["@type"] for _, req in cli.sent
              if req.get("@type") == "getScopeNotificationSettings"]
    assert sorted(scopes) == sorted(defaults), "each scope's default is fetched once"


def test_delete_mine_steps_around_a_forbidden_message_too(
    cli: FakeTransport, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Telegram refuses some deletions with a 403, not a 400.

    Only a 400 was stepped around, so one forbidden message stopped every run
    at the same batch and nothing older was ever deleted.
    """
    _script_own_messages(cli, 150)

    def _delete(r: dict[str, Any]) -> dict[str, Any]:
        if 925 in r["message_ids"]:
            return {"@type": "error", "code": 403, "message": "MESSAGE_DELETE_FORBIDDEN"}
        return {"@type": "ok"}

    cli.add_response(lambda r: r.get("@type") == "deleteMessages", _delete)
    monkeypatch.setattr(cli_context, "interactive", lambda: True)
    argv = ["--yes", "msg", "delete-mine", "--chat", "somechat"]
    result = runner.invoke(cli_main.app, argv, input="deleteMessages\n")
    assert result.exit_code == 0
    summary = _lines(result)[-1]
    assert summary["deleted"] == 149 and summary["skipped"] == [925]


def test_delete_mine_stops_when_nothing_at_all_can_be_deleted(
    cli: FakeTransport, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Refused wholesale -- no rights in the chat -- it says so after one batch
    instead of trying every message one at a time."""
    _script_own_messages(cli, 250)
    cli.add_simple_response(
        "deleteMessages", {"@type": "error", "code": 403, "message": "CHAT_ADMIN_REQUIRED"}
    )
    monkeypatch.setattr(cli_context, "interactive", lambda: True)
    argv = ["--yes", "msg", "delete-mine", "--chat", "somechat"]
    result = runner.invoke(cli_main.app, argv, input="deleteMessages\n")
    assert result.exit_code == 1
    assert _lines(result)[-1]["error"]["code"] == 403
    assert _sent(cli).count("deleteMessages") == 101, "one batch, then each of its messages"


def test_contact_list_survives_a_contact_with_no_active_username(cli: FakeTransport) -> None:
    """user_record indexed [0] into an empty active_usernames list and crashed."""
    cli.add_simple_response("getContacts", {"@type": "users", "user_ids": [1]})
    cli.add_simple_response(
        "getUser",
        {"@type": "user", "id": 1, "first_name": "Kim",
         "usernames": {"@type": "usernames", "active_usernames": [],
                       "disabled_usernames": ["oldkim"]}},
    )
    result = runner.invoke(cli_main.app, ["contact", "list"])
    assert result.exit_code == 0
    assert _lines(result)[0]["username"] is None


def test_watch_opens_the_chats_it_watches_and_closes_them(cli: FakeTransport) -> None:
    """TDLib receives every update of a channel only while the chat is open."""
    cli.add_simple_response("searchPublicChat", {"@type": "chat", "id": -1009, "title": "Alerts"})
    _arrives(cli, -1009, 1, "siren")
    result = runner.invoke(cli_main.app, ["watch", "--chat", "alerts", "--count", "1"])
    assert result.exit_code == 0 and _lines(result)[0]["text"] == "siren"
    sent = _sent(cli)
    assert _request(cli, "openChat")["chat_id"] == -1009
    assert sent.index("closeChat") > sent.index("openChat")
    assert "viewMessages" not in sent, "watching marks nothing read"


# --- from the PR review ----------------------------------------------------


def test_promote_refuses_a_title_in_a_channel_before_changing_anything(
    cli: FakeTransport, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Channels have no member titles, so the tag call would fail -- after the
    member had already been promoted."""
    cli.add_simple_response(
        "searchPublicChat",
        {"@type": "chat", "id": -1009, "type": {"@type": "chatTypeSupergroup",
                                                "supergroup_id": 9, "is_channel": True}},
    )
    monkeypatch.setattr(cli_context, "interactive", lambda: True)
    argv = ["--yes", "admin", "promote", "--chat", "news", "--user", "2", "--title", "Editor"]
    result = runner.invoke(cli_main.app, argv, input="setChatMemberStatus\n")
    assert result.exit_code == 1
    assert "setChatMemberStatus" not in _sent(cli), "nothing may change before the refusal"


def test_unread_chats_are_found_past_the_first_thousand(cli: FakeTransport) -> None:
    """getChats returns a prefix of the list; one call of 1000 missed the rest."""
    ids = list(range(1, 2501))
    unread = {1500, 2400}
    cli.add_response(
        lambda r: r.get("@type") == "getChats",
        lambda r: {"@type": "chats", "chat_ids": ids[: r["limit"]]},
    )
    cli.add_response(
        lambda r: r.get("@type") == "getChat",
        lambda r: {"@type": "chat", "id": r["chat_id"], "title": f"c{r['chat_id']}",
                   "unread_count": 1 if r["chat_id"] in unread else 0},
    )
    result = runner.invoke(cli_main.app, ["chat", "list", "--unread"])
    assert result.exit_code == 0
    assert [r["chat_id"] for r in _lines(result)] == [1500, 2400]
    limits = [req["limit"] for _, req in cli.sent if req.get("@type") == "getChats"]
    assert limits == [1000, 2000, 4000], "a longer prefix each time, until TDLib runs out"


def test_until_a_date_includes_that_whole_day(cli: FakeTransport) -> None:
    afternoon = 1_788_274_800  # 2026-09-01T15:00:00Z
    said = [{**_message_in(5, 2, "that afternoon"), "date": afternoon}]
    cli.add_simple_response("searchPublicChat", {"@type": "chat", "id": 5})
    cli.add_response(
        lambda r: r.get("@type") == "getChatHistory",
        lambda r: {"@type": "messages", "messages": said if r["from_message_id"] == 0 else []},
    )
    argv = ["chat", "history", "--chat", "x", "--until", "2026-09-01"]
    assert [r["text"] for r in _lines(runner.invoke(cli_main.app, argv))] == ["that afternoon"]


def test_a_schedule_of_now_is_refused(cli: FakeTransport) -> None:
    cli.add_simple_response("searchPublicChat", {"@type": "chat", "id": 5})
    cli.add_simple_response("sendMessage", {"@type": "message", "id": 9})
    argv = ["--yes", "msg", "send", "--chat", "somechat", "--text", "x", "--schedule", "0m"]
    result = runner.invoke(cli_main.app, argv)
    assert result.exit_code == 1
    assert "sendMessage" not in _sent(cli)
    assert "future" in _lines(result)[0]["error"]["message"]
