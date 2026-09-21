"""Safety gate: every path through TelegramClient.call, including raw call."""

from __future__ import annotations

import json

import pytest

from tdelegram import safety
from tdelegram.errors import DestructiveConfirmationRequired, WriteConfirmationRequired


def test_reads_pass_without_confirmation(client: object) -> None:
    from tdelegram.client import TelegramClient

    assert isinstance(client, TelegramClient)
    result = client.call("getMe", {})
    assert result["@type"] == "ok"


def test_write_requires_confirmation(client: object) -> None:
    from tdelegram.client import TelegramClient

    assert isinstance(client, TelegramClient)
    with pytest.raises(WriteConfirmationRequired):
        client.call("sendMessage", {"chat_id": 1})


def test_destructive_requires_confirmation(client: object) -> None:
    from tdelegram.client import TelegramClient

    assert isinstance(client, TelegramClient)
    with pytest.raises(DestructiveConfirmationRequired):
        client.call("deleteChatHistory", {"chat_id": 1})


def test_raw_call_bypass_closed(client: object) -> None:
    """The raw escape hatch must gate too, or it is a way around the gate."""
    from tdelegram.client import TelegramClient

    assert isinstance(client, TelegramClient)
    with pytest.raises(DestructiveConfirmationRequired):
        client.call("deleteChatHistory", {"chat_id": 1, "remove_from_chat_list": True})


def test_verdicts_match_plan() -> None:
    assert safety.verdict("getMessageLink") == "read"
    assert safety.verdict("searchPublicChat") == "read"
    assert safety.verdict("viewMessages") == "read"
    assert safety.verdict("openChat") == "read"
    assert safety.verdict("downloadFile") == "read"
    assert safety.verdict("loadChats") == "read"
    for method in (
        "logOut",
        "deleteAccount",
        "terminateAllOtherSessions",
        "deleteChatHistory",
        "banChatMember",
    ):
        assert safety.verdict(method) == "destructive"


def test_unknown_method_fails_closed(client: object) -> None:
    from tdelegram.client import TelegramClient

    assert isinstance(client, TelegramClient)
    with pytest.raises(RuntimeError, match="fail closed"):
        client.call("definitelyNotATdlibMethod", {})


def test_cli_raw_call_preview_and_exit_nonzero() -> None:
    """Regression: `call --request deleteChatHistory` without --yes previews, exits != 0."""
    from typer.testing import CliRunner

    from tdelegram.cli.main import app

    runner = CliRunner()
    result = runner.invoke(
        app,
        ["call", "--request", json.dumps({"@type": "deleteChatHistory", "chat_id": 1})],
    )
    assert result.exit_code != 0
    combined = (result.output or "") + (result.stderr or "")
    assert "Preview" in combined or "preview" in combined or "confirmation" in combined.lower()
