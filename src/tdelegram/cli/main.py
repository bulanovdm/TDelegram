"""tdelegram CLI: Typer tree over the domain APIs."""

from __future__ import annotations

import json
from typing import Any

import typer

from tdelegram import safety
from tdelegram.cli.context import Ctx, handle_errors, make_client, run_call
from tdelegram.cli.output import emit, emit_many, warn

app = typer.Typer(no_args_is_help=True, help="TDelegram: a TDLib-backed Telegram client")
_state: Ctx = Ctx()


@app.callback()
def _global(
    profile: str = typer.Option("default", "--profile", help="Profile name under ~/.tdelegram/"),
    session_dir: str = typer.Option("", "--session-dir", help="Session directory override"),
    fmt: str = typer.Option("jsonl", "--format", help="Output format: jsonl|json|table"),
    output: str | None = typer.Option(None, "--output", help="Write output to file"),
    yes: bool = typer.Option(False, "--yes", help="Perform mutating operations"),
    verbose: bool = typer.Option(False, "--verbose", help="Verbose diagnostics on stderr"),
    no_retry: bool = typer.Option(False, "--no-retry", help="Disable FloodWait auto-retry"),
) -> None:
    if fmt not in ("jsonl", "json", "table"):
        raise typer.BadParameter("--format must be jsonl|json|table")
    _state.profile = profile
    _state.session_dir = session_dir
    _state.fmt = fmt
    _state.output = output
    _state.yes = yes
    _state.verbose = verbose
    _state.no_retry = no_retry


def _ctx() -> Ctx:
    return _state


def _client_and_lock() -> tuple[Any, Any]:
    return make_client(_ctx())


# -- auth ---------------------------------------------------------------
auth_app = typer.Typer(no_args_is_help=True, help="Authentication")
app.add_typer(auth_app, name="auth")


@auth_app.command("login")
@handle_errors
def auth_login() -> None:
    from tdelegram.cli.context import ensure_login

    client, lock = _client_and_lock()
    try:
        ensure_login(client, _ctx())
        emit({"ok": True, "profile": _ctx().profile}, fmt=_ctx().fmt, out=_ctx().output)
    finally:
        client.close()
        if lock is not None:
            lock.release()


@auth_app.command("logout")
@handle_errors
def auth_logout() -> None:
    run_call(_ctx(), "logOut", {}, is_destructive=True)
    emit({"ok": True}, fmt=_ctx().fmt, out=_ctx().output)


@auth_app.command("status")
@handle_errors
def auth_status() -> None:
    """Report whether this profile is logged in, without prompting."""
    from tdelegram.cli.context import report_auth_state

    emit(report_auth_state(_ctx()), fmt=_ctx().fmt, out=_ctx().output)


# -- account ------------------------------------------------------------
account_app = typer.Typer(no_args_is_help=True)
app.add_typer(account_app, name="account")


@account_app.command("info")
@handle_errors
def account_info() -> None:
    emit(run_call(_ctx(), "getMe", {}), fmt=_ctx().fmt, out=_ctx().output)


@account_app.command("sessions")
@handle_errors
def account_sessions() -> None:
    emit(run_call(_ctx(), "getActiveSessions", {}), fmt=_ctx().fmt, out=_ctx().output)


# -- chat ---------------------------------------------------------------
chat_app = typer.Typer(no_args_is_help=True)
app.add_typer(chat_app, name="chat")


@chat_app.command("list")
@handle_errors
def chat_list(
    scope: str = typer.Option("main", help="main|archive|all"),
    limit: int | None = typer.Option(None, help="Max chats"),
) -> None:
    from tdelegram.api import chats

    client, lock = _client_and_lock()
    try:
        emit_many(
            chats.iter_list(client, scope=scope, maximum=limit), fmt=_ctx().fmt, out=_ctx().output
        )
    finally:
        client.close()
        if lock is not None:
            lock.release()


@chat_app.command("info")
@handle_errors
def chat_info(chat: str = typer.Argument(...)) -> None:
    from tdelegram.api import chats

    client, lock = _client_and_lock()
    try:
        emit(chats.info(client, chat), fmt=_ctx().fmt, out=_ctx().output)
    finally:
        client.close()
        if lock is not None:
            lock.release()


@chat_app.command("resolve")
@handle_errors
def chat_resolve(chat: str = typer.Argument(...)) -> None:
    from tdelegram.api import chats

    client, lock = _client_and_lock()
    try:
        emit(chats.resolve(client, chat), fmt=_ctx().fmt, out=_ctx().output)
    finally:
        client.close()
        if lock is not None:
            lock.release()


@chat_app.command("history")
@handle_errors
def chat_history(
    chat: str = typer.Option(..., "--chat"),
    limit: int | None = typer.Option(5, "--limit"),
    since: str | None = typer.Option(None, "--since"),
    until: str | None = typer.Option(None, "--until"),
    sender: int | None = typer.Option(None, "--sender"),
    topic: int | None = typer.Option(None, "--topic"),
    contains: str | None = typer.Option(None, "--contains"),
) -> None:
    from tdelegram.api import messages

    client, lock = _client_and_lock()
    try:
        emit_many(
            messages.iter_history(
                client,
                chat,
                maximum=limit,
                since=since,
                until=until,
                sender_id=sender,
                topic_id=topic,
                contains=[contains] if contains else None,
            ),
            fmt=_ctx().fmt,
            out=_ctx().output,
        )
    finally:
        client.close()
        if lock is not None:
            lock.release()


@chat_app.command("search")
@handle_errors
def chat_search(
    chat: str = typer.Option(..., "--chat"),
    query: str = typer.Option(..., "--query"),
    limit: int = typer.Option(20, "--limit"),
) -> None:
    client, lock = _client_and_lock()
    try:
        result = client.call(
            "searchChatMessages",
            {"chat_id": _resolve(client, chat), "query": query, "limit": limit},
        )
        for message in result.get("messages", []):
            emit(message, fmt=_ctx().fmt, out=_ctx().output)
    finally:
        client.close()
        if lock is not None:
            lock.release()


def _resolve(client: Any, ref: str) -> int:
    from tdelegram.api import chats

    return chats.resolve_id(client, ref)



@chat_app.command("create")
@handle_errors
def chat_create(title: str = typer.Argument(...)) -> None:
    result = run_call(_ctx(), "createNewSupergroupChat", {"title": title}, is_write=True)
    emit(result, fmt=_ctx().fmt, out=_ctx().output)


@chat_app.command("join")
@handle_errors
def chat_join(chat: str = typer.Argument(...)) -> None:
    from tdelegram.api import chats

    client, lock = _client_and_lock()
    try:
        emit(chats.join(client, chat, allow_write=_ctx().yes), fmt=_ctx().fmt, out=_ctx().output)
    except Exception:
        # Fall back to gate-aware run_call for preview semantics.
        result = run_call(_ctx(), "joinChat", {"chat_id": chat}, is_write=True)
        emit(result, fmt=_ctx().fmt, out=_ctx().output)
    finally:
        try:
            client.close()
        except Exception:
            pass
        if lock is not None:
            lock.release()


@chat_app.command("leave")
@handle_errors
def chat_leave(chat: str = typer.Argument(...)) -> None:
    result = run_call(_ctx(), "leaveChat", {"chat_id": chat}, is_write=True)
    emit(result, fmt=_ctx().fmt, out=_ctx().output)


@chat_app.command("members")
@handle_errors
def chat_members(chat: str = typer.Argument(...), limit: int = typer.Option(100)) -> None:
    result = run_call(_ctx(), "getSupergroupMembers", {"supergroup_id": chat, "limit": limit})
    emit(result, fmt=_ctx().fmt, out=_ctx().output)


# -- msg ----------------------------------------------------------------
msg_app = typer.Typer(no_args_is_help=True)
app.add_typer(msg_app, name="msg")


@msg_app.command("send")
@handle_errors
def msg_send(
    chat: str = typer.Option(..., "--chat"),
    text: str = typer.Option(..., "--text"),
    parse_mode: str | None = typer.Option(None, "--parse-mode"),
    reply_to: int | None = typer.Option(None, "--reply-to"),
) -> None:
    from tdelegram.api import messages

    client, lock = _client_and_lock()
    try:
        emit(
            messages.send(
                client, chat, text, parse_mode=parse_mode, reply_to=reply_to, allow_write=_ctx().yes
            ),
            fmt=_ctx().fmt,
            out=_ctx().output,
        )
    except Exception as exc:
        from tdelegram.errors import ConfirmationRequired as _CR

        if isinstance(exc, _CR):
            warn(json.dumps({"preview": exc.preview, "verdict": exc.verdict}, indent=2))
            warn("Preview only: re-run with --yes to perform.")
            raise SystemExit(2) from None
        raise
    finally:
        client.close()
        if lock is not None:
            lock.release()


@msg_app.command("get")
@handle_errors
def msg_get(
    chat: str = typer.Option(..., "--chat"), message_id: int = typer.Option(..., "--id")
) -> None:
    from tdelegram.api import messages

    client, lock = _client_and_lock()
    try:
        emit(messages.get(client, chat, message_id), fmt=_ctx().fmt, out=_ctx().output)
    finally:
        client.close()
        if lock is not None:
            lock.release()


@msg_app.command("edit")
@handle_errors
def msg_edit(
    chat: str = typer.Option(..., "--chat"),
    message_id: int = typer.Option(..., "--id"),
    text: str = typer.Option(..., "--text"),
) -> None:
    result = run_call(
        _ctx(),
        "editMessageText",
        {"chat_id": chat, "message_id": message_id, "text": text},
        is_write=True,
    )
    emit(result, fmt=_ctx().fmt, out=_ctx().output)


@msg_app.command("delete")
@handle_errors
def msg_delete(
    chat: str = typer.Option(..., "--chat"),
    message_id: int = typer.Option(..., "--id"),
) -> None:
    result = run_call(
        _ctx(),
        "deleteMessages",
        {"chat_id": chat, "message_ids": [message_id]},
        is_destructive=True,
    )
    emit(result, fmt=_ctx().fmt, out=_ctx().output)


@msg_app.command("forward")
@handle_errors
def msg_forward(
    from_chat: str = typer.Option(..., "--from"),
    to_chat: str = typer.Option(..., "--to"),
    message_id: int = typer.Option(..., "--id"),
) -> None:
    result = run_call(
        _ctx(),
        "forwardMessages",
        {"from_chat_id": from_chat, "chat_id": to_chat, "message_ids": [message_id]},
        is_write=True,
    )
    emit(result, fmt=_ctx().fmt, out=_ctx().output)


@msg_app.command("react")
@handle_errors
def msg_react(
    chat: str = typer.Option(..., "--chat"),
    message_id: int = typer.Option(..., "--id"),
    emoji: str = typer.Option("👍", "--emoji"),
) -> None:
    result = run_call(
        _ctx(),
        "addMessageReaction",
        {"chat_id": chat, "message_id": message_id, "emoji": emoji},
        is_write=True,
    )
    emit(result, fmt=_ctx().fmt, out=_ctx().output)


@msg_app.command("link")
@handle_errors
def msg_link(
    chat: str = typer.Option(..., "--chat"), message_id: int = typer.Option(..., "--id")
) -> None:
    emit(
        run_call(_ctx(), "getMessageLink", {"chat_id": chat, "message_id": message_id}),
        fmt=_ctx().fmt,
        out=_ctx().output,
    )


@msg_app.command("search")
@handle_errors
def msg_search(
    query: str = typer.Option(..., "--query"), limit: int = typer.Option(20, "--limit")
) -> None:
    from tdelegram.api import search as search_api

    client, lock = _client_and_lock()
    try:
        for record in search_api.iter_global_search(client, query, maximum=limit):
            emit(record, fmt=_ctx().fmt, out=_ctx().output)
    finally:
        client.close()
        if lock is not None:
            lock.release()


@msg_app.command("poll")
@handle_errors
def msg_poll(
    chat: str = typer.Option(..., "--chat"),
    message_id: int = typer.Option(..., "--id"),
    option: int = typer.Option(..., "--option"),
) -> None:
    result = run_call(
        _ctx(),
        "setPollAnswer",
        {"chat_id": chat, "message_id": message_id, "option_ids": [option]},
        is_write=True,
    )
    emit(result, fmt=_ctx().fmt, out=_ctx().output)


# -- media --------------------------------------------------------------
media_app = typer.Typer(no_args_is_help=True)
app.add_typer(media_app, name="media")


@media_app.command("download")
@handle_errors
def media_download(file_id: int = typer.Argument(...)) -> None:
    from tdelegram.api import media as media_api

    client, lock = _client_and_lock()
    try:
        emit(media_api.download(client, file_id), fmt=_ctx().fmt, out=_ctx().output)
    finally:
        client.close()
        if lock is not None:
            lock.release()


@media_app.command("upload")
@handle_errors
def media_upload(
    chat: str = typer.Option(..., "--chat"), path: str = typer.Option(..., "--path")
) -> None:
    from tdelegram.api import media as media_api

    client, lock = _client_and_lock()
    try:
        emit(media_api.upload(client, chat, path), fmt=_ctx().fmt, out=_ctx().output)
    except Exception as exc:
        from tdelegram.errors import ConfirmationRequired as _CR

        if isinstance(exc, _CR):
            warn(json.dumps({"preview": exc.preview}, indent=2))
            raise SystemExit(2) from None
        raise
    finally:
        client.close()
        if lock is not None:
            lock.release()


# -- contact / user -----------------------------------------------------
contact_app = typer.Typer(no_args_is_help=True)
app.add_typer(contact_app, name="contact")


@contact_app.command("list")
@handle_errors
def contact_list() -> None:
    emit(run_call(_ctx(), "getContacts", {}), fmt=_ctx().fmt, out=_ctx().output)


user_app = typer.Typer(no_args_is_help=True)
app.add_typer(user_app, name="user")


@user_app.command("info")
@handle_errors
def user_info(user_id: int = typer.Argument(...)) -> None:
    emit(run_call(_ctx(), "getUser", {"user_id": user_id}), fmt=_ctx().fmt, out=_ctx().output)


# -- admin --------------------------------------------------------------
admin_app = typer.Typer(no_args_is_help=True)
app.add_typer(admin_app, name="admin")


@admin_app.command("ban")
@handle_errors
def admin_ban(
    chat: str = typer.Option(..., "--chat"), user: int = typer.Option(..., "--user")
) -> None:
    result = run_call(
        _ctx(), "banChatMember", {"chat_id": chat, "user_id": user}, is_destructive=True
    )
    emit(result, fmt=_ctx().fmt, out=_ctx().output)


@admin_app.command("promote")
@handle_errors
def admin_promote(
    chat: str = typer.Option(..., "--chat"), user: int = typer.Option(..., "--user")
) -> None:
    result = run_call(
        _ctx(), "setChatMemberStatus", {"chat_id": chat, "user_id": user}, is_write=True
    )
    emit(result, fmt=_ctx().fmt, out=_ctx().output)


# -- topic / folder / draft ---------------------------------------------
topic_app = typer.Typer(no_args_is_help=True)
app.add_typer(topic_app, name="topic")


@topic_app.command("list")
@handle_errors
def topic_list(chat: str = typer.Argument(...)) -> None:
    from tdelegram.api import topics

    client, lock = _client_and_lock()
    try:
        for topic in topics.iter_topics(client, chat):
            emit(topic, fmt=_ctx().fmt, out=_ctx().output)
    finally:
        client.close()
        if lock is not None:
            lock.release()


folder_app = typer.Typer(no_args_is_help=True)
app.add_typer(folder_app, name="folder")


@folder_app.command("list")
@handle_errors
def folder_list() -> None:
    emit(run_call(_ctx(), "getChatFolders", {}), fmt=_ctx().fmt, out=_ctx().output)


draft_app = typer.Typer(no_args_is_help=True)
app.add_typer(draft_app, name="draft")


@draft_app.command("set")
@handle_errors
def draft_set(
    chat: str = typer.Option(..., "--chat"), text: str = typer.Option(..., "--text")
) -> None:
    result = run_call(_ctx(), "setChatDraftMessage", {"chat_id": chat, "text": text}, is_write=True)
    emit(result, fmt=_ctx().fmt, out=_ctx().output)


# -- bot / story / secret / proxy ---------------------------------------
bot_app = typer.Typer(no_args_is_help=True)
app.add_typer(bot_app, name="bot")


@bot_app.command("callback")
@handle_errors
def bot_callback(query_id: int = typer.Argument(...)) -> None:
    result = run_call(_ctx(), "answerCallbackQuery", {"callback_query_id": query_id}, is_write=True)
    emit(result, fmt=_ctx().fmt, out=_ctx().output)


@bot_app.command("inline")
@handle_errors
def bot_inline(
    bot: int = typer.Option(..., "--bot"), query: str = typer.Option(..., "--query")
) -> None:
    emit(
        run_call(_ctx(), "getInlineQueryResults", {"bot_user_id": bot, "query": query}),
        fmt=_ctx().fmt,
        out=_ctx().output,
    )


story_app = typer.Typer(no_args_is_help=True)
app.add_typer(story_app, name="story")


@story_app.command("list")
@handle_errors
def story_list(chat: str = typer.Argument(...)) -> None:
    emit(
        run_call(_ctx(), "getChatArchivedStories", {"chat_id": chat}),
        fmt=_ctx().fmt,
        out=_ctx().output,
    )


secret_app = typer.Typer(no_args_is_help=True)
app.add_typer(secret_app, name="secret")


@secret_app.command("create")
@handle_errors
def secret_create(user: int = typer.Argument(...)) -> None:
    result = run_call(_ctx(), "createNewSecretChat", {"user_id": user}, is_write=True)
    emit(result, fmt=_ctx().fmt, out=_ctx().output)


proxy_app = typer.Typer(no_args_is_help=True)
app.add_typer(proxy_app, name="proxy")


@proxy_app.command("list")
@handle_errors
def proxy_list() -> None:
    emit(run_call(_ctx(), "getProxies", {}), fmt=_ctx().fmt, out=_ctx().output)


# -- updates ------------------------------------------------------------
updates_app = typer.Typer(no_args_is_help=True)
app.add_typer(updates_app, name="updates")


@updates_app.command("follow")
@handle_errors
def updates_follow(types: str = typer.Option("", help="Comma-separated @type filter")) -> None:
    from tdelegram.api import updates as updates_api

    wanted = [t.strip() for t in types.split(",") if t.strip()]
    client, lock = _client_and_lock()
    try:
        for event in updates_api.follow_filtered(client, wanted):
            emit(event, fmt=_ctx().fmt, out=_ctx().output)
    finally:
        client.close()
        if lock is not None:
            lock.release()


# -- raw call (escape hatch, same gate) ----------------------------------
@app.command("call")
@handle_errors
def raw_call(request: str = typer.Option(..., "--request", help="Raw TDLib JSON request")) -> None:
    try:
        obj = json.loads(request)
    except json.JSONDecodeError as exc:
        warn(f"Invalid --request JSON: {exc}")
        raise SystemExit(2) from None
    method = str(obj.get("@type", ""))
    if not method:
        warn("Request must contain @type.")
        raise SystemExit(2) from None
    params = {k: v for k, v in obj.items() if k != "@type"}
    try:
        verdict = safety.verdict(method)
    except RuntimeError as exc:
        warn(str(exc))
        raise SystemExit(2) from None
    is_write = verdict == "write"
    is_destructive = verdict == "destructive"
    if not _ctx().yes:
        warn(
            json.dumps(
                {
                    "preview": {"@type": method, **params},
                    "verdict": verdict,
                    "reason": safety.reason(method),
                },
                indent=2,
            )
        )
        if is_write or is_destructive:
            warn("Preview only: re-run with --yes to perform.")
            raise SystemExit(2) from None
    result = run_call(_ctx(), method, params, is_write=is_write, is_destructive=is_destructive)
    emit(result, fmt=_ctx().fmt, out=_ctx().output)


@app.command("version")
def version() -> None:
    from tdelegram import __version__

    typer.echo(__version__)


def main() -> None:
    app()


if __name__ == "__main__":
    main()
