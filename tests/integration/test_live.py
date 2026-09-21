"""Opt-in tests against real libtdjson and a real authorized session.

    TDELEGRAM_INTEGRATION=1 pytest tests/integration -q

Everything here is strictly read-only. No test performs a write: the two
gate tests assert that a mutating call is *refused*, and the refusal
happens in `TelegramClient.call()` before anything reaches TDLib, so
nothing is sent even if the gate were broken in the other direction.

These exist because the unit suite passed green while the CLI could not
complete a single real call. A fake that always answers `ok` cannot tell
you that TDLib never received its parameters.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from typing import Any

import pytest

from tdelegram.errors import DestructiveConfirmationRequired, WriteConfirmationRequired

pytestmark = pytest.mark.skipif(
    os.environ.get("TDELEGRAM_INTEGRATION") != "1",
    reason="opt-in: needs libtdjson and an authorized session",
)


@pytest.fixture(scope="module")
def client() -> Iterator[Any]:
    """An authorized client on the ambient profile, or a skip explaining why."""
    from tdelegram.auth import NonInteractiveCredentialProvider, current_state, run_auth
    from tdelegram.client import TelegramClient
    from tdelegram.config import default_base_dir, discover_library
    from tdelegram.loop import reset_loops
    from tdelegram.transport import TdJsonTransport

    try:
        library = discover_library()
    except RuntimeError as exc:
        pytest.skip(f"libtdjson not installed: {exc}")

    profile = default_base_dir() / "profiles" / os.environ.get("TDELEGRAM_PROFILE", "default")
    db_dir, files_dir = profile / "tdlib", profile / "files"
    if not db_dir.exists():
        pytest.skip(f"no session at {db_dir}; run `tdelegram auth login` first")

    conn = TelegramClient(TdJsonTransport(library))
    provider = NonInteractiveCredentialProvider(base_dir=default_base_dir())
    state = current_state(
        conn,
        provider,
        database_directory=str(db_dir),
        files_directory=str(files_dir),
    )
    if not state["authorized"]:
        conn.close()
        reset_loops()
        pytest.skip(f"session is not authorized: needs {state['needs']}")
    run_auth(
        conn,
        provider,
        timeout=30.0,
        database_directory=str(db_dir),
        files_directory=str(files_dir),
    )
    yield conn
    conn.close()
    reset_loops()


def test_handshake_reaches_ready(client: Any) -> None:
    """The bug that shipped: a saved session that never becomes usable."""
    state = client.call("getAuthorizationState", {})
    assert state["@type"] == "authorizationStateReady"


def test_a_read_returns_real_data(client: Any) -> None:
    me = client.call("getMe", {})
    assert me["@type"] == "user"
    assert isinstance(me.get("id"), int) and me["id"] > 0


def test_normalization_matches_a_real_object(client: Any) -> None:
    """Flat records must survive a real TDLib object, not just a fixture."""
    from tdelegram import normalize

    record = normalize.user_record(client.call("getMe", {}))
    assert isinstance(record["user_id"], int)
    assert set(record) >= {"user_id", "first_name", "username", "is_bot", "is_premium"}


def test_chat_listing_streams(client: Any) -> None:
    from tdelegram.api import chats

    records = list(chats.iter_list(client, scope="main", maximum=3))
    assert len(records) <= 3
    for record in records:
        assert isinstance(record["chat_id"], int)
        assert record["chat_list"] == "main"


def test_a_write_is_refused_without_permission(client: Any) -> None:
    """Refused inside call(), so nothing reaches TDLib."""
    with pytest.raises(WriteConfirmationRequired):
        client.call("sendMessage", {"chat_id": 0, "input_message_content": {}})


def test_a_destructive_call_is_refused_without_permission(client: Any) -> None:
    with pytest.raises(DestructiveConfirmationRequired):
        client.call("deleteChatHistory", {"chat_id": 0})


def test_an_audited_method_is_gated(client: Any) -> None:
    """getInlineQueryResults notifies a third-party bot; it must not be a read."""
    with pytest.raises(WriteConfirmationRequired):
        client.call("getInlineQueryResults", {"bot_user_id": 0, "chat_id": 0, "query": "x"})


def test_unknown_methods_fail_closed(client: Any) -> None:
    with pytest.raises(RuntimeError, match="no registry verdict"):
        client.call("notARealTdlibMethod", {})


def test_td_execute_formats_entities(client: Any) -> None:
    """Outbound formatting goes through real td_execute, not the fake.

    MarkdownV2: bold is `*bold*`. `**bold**` yields plain text and no
    entity at all, which is how formatting gets lost without an error.
    """
    from tdelegram.entities import parse_entities

    formatted = parse_entities(client.transport, "*bold*", "markdown")
    assert formatted["@type"] == "formattedText"
    assert formatted["text"] == "bold"
    assert any(e["type"]["@type"] == "textEntityTypeBold" for e in formatted["entities"])

    swallowed = parse_entities(client.transport, "**bold**", "markdown")
    assert swallowed["entities"] == [], "documents the V1-vs-V2 trap"


def test_markdown_round_trips(client: Any) -> None:
    """render_entities must emit markup that parse_entities reads back.

    The renderer emitted `**bold**` while the parser wanted `*bold*`, so a
    rendered message lost its formatting when sent back through.
    """
    from tdelegram.entities import parse_entities, render_entities

    for source in ("*bold*", "_italic_", "`code`", "__underline__", "~struck~"):
        parsed = parse_entities(client.transport, source, "markdown")
        assert parsed["entities"], f"{source} produced no entity"
        rendered = render_entities(parsed, "markdown")
        reparsed = parse_entities(client.transport, rendered, "markdown")
        assert reparsed["text"] == parsed["text"], f"{source} -> {rendered}"
        assert [e["type"]["@type"] for e in reparsed["entities"]] == [
            e["type"]["@type"] for e in parsed["entities"]
        ], f"{source} rendered as {rendered!r} and lost its entity"
