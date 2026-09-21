"""CLI behaviour through Typer's runner, on a FakeTransport.

The CLI layer went untested and unmeasured for a long time, and both
launch-blocking bugs lived here: `auth login` never sent a bootstrap
request, and no data command replayed the TDLib handshake. These tests
exercise the command bodies rather than just importing them.
"""

from __future__ import annotations

import json
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

    monkeypatch.setattr(cli_main, "make_client", _fake_make_client)
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

    Scripts a session that is not yet unlocked, so the handshake has to do
    real work rather than answering "ready" on the first probe.
    """
    # FakeTransport matches the first rule that fits, so the fixture's
    # already-authorized answer has to go before a different one is scripted.
    cli._rules.clear()
    cli.add_simple_response(
        "getAuthorizationState", {"@type": "authorizationStateWaitEncryptionKey"}
    )

    def _unlocked(request: dict[str, Any]) -> dict[str, Any]:
        cli.add_update(
            {
                "@type": "updateAuthorizationState",
                "authorization_state": {"@type": "authorizationStateReady"},
            }
        )
        return {"@type": "ok"}

    cli.add_response(lambda r: r.get("@type") == "checkDatabaseEncryptionKey", _unlocked)
    cli.add_simple_response("getMe", {"@type": "user", "id": 7})

    result = runner.invoke(cli_main.app, ["account", "info"])
    assert result.exit_code == 0
    sent = _sent(cli)
    assert sent[0] == "getAuthorizationState", "handshake must be probed before any call"
    assert "checkDatabaseEncryptionKey" in sent, "handshake must actually be driven"
    assert sent.index("checkDatabaseEncryptionKey") < sent.index("getMe")


def test_auth_status_does_not_log_in(cli: FakeTransport) -> None:
    """`status` reports the state as found; logging in first would defeat it."""
    result = runner.invoke(cli_main.app, ["auth", "status"])
    assert result.exit_code == 0
    assert _sent(cli) == ["getAuthorizationState"]


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
    ("answerCallbackQuery", ["bot", "callback", "9"]),
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
