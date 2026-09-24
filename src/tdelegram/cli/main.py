"""tdelegram CLI: Typer tree over the domain APIs."""

from __future__ import annotations

import json
from collections.abc import Iterable
from typing import Any

import typer

from tdelegram import safety
from tdelegram.cli.context import (
    Ctx,
    handle_errors,
    perform,
    run_call,
    session,
    setup_session,
    show_preview,
)
from tdelegram.cli.output import emit, emit_many, warn

app = typer.Typer(no_args_is_help=True, help="TDelegram: a TDLib-backed Telegram client")
_state: Ctx = Ctx()

# Telegram chat ids for groups and channels are negative, and a bare -100...
# is otherwise parsed as a cluster of short options. Every command taking a
# positional chat or user reference needs this.
REF_ARGS = {"ignore_unknown_options": True}


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


def _emit(record: dict[str, Any]) -> None:
    emit(record, fmt=_ctx().fmt, out=_ctx().output)


def _emit_many(records: Iterable[dict[str, Any]]) -> None:
    emit_many(records, fmt=_ctx().fmt, out=_ctx().output)


# -- auth ---------------------------------------------------------------
auth_app = typer.Typer(no_args_is_help=True, help="Authentication")
app.add_typer(auth_app, name="auth")


@auth_app.command("login")
@handle_errors
def auth_login() -> None:
    from tdelegram.cli.context import ensure_login

    with session(_ctx(), login=False) as client:
        ensure_login(client, _ctx())
        _emit({"ok": True, "profile": _ctx().profile})


@auth_app.command("logout")
@handle_errors
def auth_logout() -> None:
    run_call(_ctx(), "logOut", {})
    _emit({"ok": True})


@auth_app.command("status")
@handle_errors
def auth_status() -> None:
    """Report whether this profile is logged in, without prompting."""
    from tdelegram.cli.context import report_auth_state

    _emit(report_auth_state(_ctx()))


# -- account ------------------------------------------------------------
account_app = typer.Typer(no_args_is_help=True)
app.add_typer(account_app, name="account")


@account_app.command("info")
@handle_errors
def account_info() -> None:
    _emit(run_call(_ctx(), "getMe", {}))


@account_app.command("sessions")
@handle_errors
def account_sessions() -> None:
    _emit(run_call(_ctx(), "getActiveSessions", {}))


# -- chat ---------------------------------------------------------------
chat_app = typer.Typer(no_args_is_help=True)
app.add_typer(chat_app, name="chat")


@chat_app.command("list")
@handle_errors
def chat_list(
    scope: str = typer.Option("main", help="main|archive|all"),
    limit: int | None = typer.Option(None, help="Max chats"),
    unread: bool = typer.Option(False, "--unread", help="Only chats with unread messages"),
) -> None:
    from tdelegram.api import chats

    with session(_ctx()) as client:
        _emit_many(chats.iter_list(client, scope=scope, maximum=limit, unread_only=unread))


@chat_app.command("info", context_settings=REF_ARGS)
@handle_errors
def chat_info(chat: str = typer.Argument(...)) -> None:
    from tdelegram.api import chats

    with session(_ctx()) as client:
        _emit(chats.info(client, chat))


@chat_app.command("resolve", context_settings=REF_ARGS)
@handle_errors
def chat_resolve(chat: str = typer.Argument(...)) -> None:
    from tdelegram.api import chats

    with session(_ctx()) as client:
        _emit(chats.resolve(client, chat))


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

    with session(_ctx()) as client:
        _emit_many(
            messages.iter_history(
                client,
                chat,
                maximum=limit,
                since=since,
                until=until,
                sender_id=sender,
                topic_id=topic,
                contains=[contains] if contains else None,
            )
        )


@chat_app.command("search")
@handle_errors
def chat_search(
    chat: str = typer.Option(..., "--chat"),
    query: str = typer.Option(..., "--query"),
    limit: int = typer.Option(20, "--limit"),
    sender: int | None = typer.Option(None, "--sender", help="Only this user's messages"),
) -> None:
    """Server-side search in one chat; records shaped like `chat history`."""
    from tdelegram.api import search as search_api

    with session(_ctx()) as client:
        _emit_many(
            search_api.iter_chat_search(client, chat, query, sender_id=sender, maximum=limit)
        )


@chat_app.command("create", context_settings=REF_ARGS)
@handle_errors
def chat_create(title: str = typer.Argument(...)) -> None:
    from tdelegram.api import chats

    with session(_ctx()) as client:
        _emit(perform(_ctx(), lambda w, d: chats.create_supergroup(client, title, allow_write=w)))


@chat_app.command("join", context_settings=REF_ARGS)
@handle_errors
def chat_join(chat: str = typer.Argument(...)) -> None:
    from tdelegram.api import chats

    with session(_ctx()) as client:
        _emit(perform(_ctx(), lambda w, d: chats.join(client, chat, allow_write=w)))


@chat_app.command("leave", context_settings=REF_ARGS)
@handle_errors
def chat_leave(chat: str = typer.Argument(...)) -> None:
    from tdelegram.api import chats

    with session(_ctx()) as client:
        _emit(
            perform(
                _ctx(),
                lambda w, d: chats.leave(client, chat, allow_write=w, allow_destructive=d),
            )
        )


@chat_app.command("members", context_settings=REF_ARGS)
@handle_errors
def chat_members(chat: str = typer.Argument(...), limit: int = typer.Option(100)) -> None:
    from tdelegram.api import chats

    with session(_ctx()) as client:
        _emit(chats.members(client, chat, limit=limit))


@app.command("inbox")
@handle_errors
def inbox(
    chats: int = typer.Option(20, "--chats", help="At most this many unread chats"),
    per_chat: int = typer.Option(
        20, "--per-chat", help="At most this many messages from each; the newest are kept"
    ),
    scope: str = typer.Option("main", "--scope", help="main|archive|all"),
    include_muted: bool = typer.Option(
        False, "--include-muted", help="Muted chats too (they appear anyway if they mention you)"
    ),
) -> None:
    """Unread messages across chats, oldest first within each. Marks nothing read.

    For "what did I miss": nothing here tells a sender their message was seen.
    """
    from tdelegram.api import inbox as inbox_api

    with session(_ctx()) as client:
        _emit_many(
            inbox_api.iter_unread(
                client, scope=scope, chats=chats, per_chat=per_chat, include_muted=include_muted
            )
        )


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
    topic: int | None = typer.Option(None, "--topic", help="Forum topic to post in"),
    silent: bool = typer.Option(False, "--silent", help="Deliver without a notification"),
    schedule: str | None = typer.Option(
        None, "--schedule", help="Send later: 30m, 2h, 1d from now, or ISO-8601"
    ),
) -> None:
    from tdelegram.api import messages
    from tdelegram.dates import parse_future

    schedule_date = parse_future(schedule) if schedule else None
    with session(_ctx()) as client:
        _emit(
            perform(
                _ctx(),
                lambda w, d: messages.send(
                    client,
                    chat,
                    text,
                    parse_mode=parse_mode,
                    reply_to=reply_to,
                    topic_id=topic,
                    silent=silent,
                    schedule_date=schedule_date,
                    allow_write=w,
                ),
            )
        )


@msg_app.command("get")
@handle_errors
def msg_get(
    chat: str = typer.Option(..., "--chat"), message_id: int = typer.Option(..., "--id")
) -> None:
    from tdelegram.api import messages

    with session(_ctx()) as client:
        _emit(messages.get(client, chat, message_id))


@msg_app.command("edit")
@handle_errors
def msg_edit(
    chat: str = typer.Option(..., "--chat"),
    message_id: int = typer.Option(..., "--id"),
    text: str = typer.Option(..., "--text"),
    parse_mode: str | None = typer.Option(None, "--parse-mode"),
) -> None:
    from tdelegram.api import messages

    with session(_ctx()) as client:
        _emit(
            perform(
                _ctx(),
                lambda w, d: messages.edit(
                    client, chat, message_id, text, parse_mode=parse_mode, allow_write=w
                ),
            )
        )


@msg_app.command("delete")
@handle_errors
def msg_delete(
    chat: str = typer.Option(..., "--chat"),
    message_ids: list[int] = typer.Option(..., "--id", help="Repeat for several messages"),
    only_for_me: bool = typer.Option(
        False, "--only-for-me", help="Keep them for the other side of a private chat"
    ),
) -> None:
    """Delete messages for everyone, unless --only-for-me.

    TDLib deletes only locally unless told to revoke, and this used to leave
    that unset: in a private chat the other side kept every "deleted" message.
    """
    from tdelegram.api import messages

    with session(_ctx()) as client:
        _emit(
            perform(
                _ctx(),
                lambda w, d: messages.delete(
                    client,
                    chat,
                    list(message_ids),
                    revoke=not only_for_me,
                    allow_write=w,
                    allow_destructive=d,
                ),
            )
        )


@msg_app.command("forward")
@handle_errors
def msg_forward(
    from_chat: str = typer.Option(..., "--from"),
    to_chat: str = typer.Option(..., "--to"),
    message_ids: list[int] = typer.Option(..., "--id", help="Repeat for several messages"),
) -> None:
    from tdelegram.api import messages

    with session(_ctx()) as client:
        _emit(
            perform(
                _ctx(),
                lambda w, d: messages.forward(
                    client, from_chat, to_chat, list(message_ids), allow_write=w
                ),
            )
        )


@msg_app.command("react")
@handle_errors
def msg_react(
    chat: str = typer.Option(..., "--chat"),
    message_id: int = typer.Option(..., "--id"),
    emoji: str = typer.Option("👍", "--emoji"),
) -> None:
    from tdelegram.api import messages

    with session(_ctx()) as client:
        _emit(
            perform(
                _ctx(), lambda w, d: messages.react(client, chat, message_id, emoji, allow_write=w)
            )
        )


@msg_app.command("link")
@handle_errors
def msg_link(
    chat: str = typer.Option(..., "--chat"), message_id: int = typer.Option(..., "--id")
) -> None:
    from tdelegram.api import messages

    with session(_ctx()) as client:
        _emit(messages.link(client, chat, message_id))


@msg_app.command("search")
@handle_errors
def msg_search(
    query: str = typer.Option(..., "--query"),
    limit: int = typer.Option(20, "--limit"),
    since: str | None = typer.Option(None, "--since"),
    until: str | None = typer.Option(None, "--until"),
) -> None:
    """Search every chat at once, newest first."""
    from tdelegram.api import search as search_api

    with session(_ctx()) as client:
        _emit_many(
            search_api.iter_global_search(client, query, maximum=limit, since=since, until=until)
        )


@msg_app.command("poll")
@handle_errors
def msg_poll(
    chat: str = typer.Option(..., "--chat"),
    message_id: int = typer.Option(..., "--id"),
    options: list[int] = typer.Option(..., "--option", help="Repeat in multiple-answer polls"),
) -> None:
    from tdelegram.api import polls

    with session(_ctx()) as client:
        _emit(
            perform(
                _ctx(),
                lambda w, d: polls.answer_poll(
                    client, chat, message_id, list(options), allow_write=w
                ),
            )
        )


# -- media --------------------------------------------------------------
media_app = typer.Typer(no_args_is_help=True)
app.add_typer(media_app, name="media")


@media_app.command("download", context_settings=REF_ARGS)
@handle_errors
def media_download(file_id: int = typer.Argument(...)) -> None:
    """Download a file by the `media.file_id` of a message record."""
    from tdelegram import normalize
    from tdelegram.api import media as media_api

    with session(_ctx()) as client:
        _emit(normalize.file_record(media_api.download(client, file_id)))


@media_app.command("upload")
@handle_errors
def media_upload(
    chat: str = typer.Option(..., "--chat"),
    path: str = typer.Option(..., "--path"),
    caption: str = typer.Option("", "--caption"),
) -> None:
    from tdelegram.api import media as media_api

    with session(_ctx()) as client:
        _emit(
            perform(
                _ctx(),
                lambda w, d: media_api.upload(client, chat, path, caption=caption, allow_write=w),
            )
        )


# -- contact / user -----------------------------------------------------
contact_app = typer.Typer(no_args_is_help=True)
app.add_typer(contact_app, name="contact")


@contact_app.command("list")
@handle_errors
def contact_list() -> None:
    _emit(run_call(_ctx(), "getContacts", {}))


user_app = typer.Typer(no_args_is_help=True)
app.add_typer(user_app, name="user")


@user_app.command("info", context_settings=REF_ARGS)
@handle_errors
def user_info(user_id: int = typer.Argument(...)) -> None:
    _emit(run_call(_ctx(), "getUser", {"user_id": user_id}))


# -- admin --------------------------------------------------------------
admin_app = typer.Typer(no_args_is_help=True)
app.add_typer(admin_app, name="admin")


@admin_app.command("ban")
@handle_errors
def admin_ban(
    chat: str = typer.Option(..., "--chat"), user: int = typer.Option(..., "--user")
) -> None:
    from tdelegram.api import admin

    with session(_ctx()) as client:
        _emit(
            perform(
                _ctx(),
                lambda w, d: admin.ban(client, chat, user, allow_write=w, allow_destructive=d),
            )
        )


@admin_app.command("promote")
@handle_errors
def admin_promote(
    chat: str = typer.Option(..., "--chat"),
    user: int = typer.Option(..., "--user"),
    rights: list[str] = typer.Option(
        [], "--right", help="e.g. delete_messages, pin_messages; repeat. Default: manage_chat"
    ),
    title: str = typer.Option("", "--title", help="Custom title shown next to the admin"),
) -> None:
    from tdelegram.api import admin

    granted = list(rights) or list(admin.DEFAULT_RIGHTS)
    # Fail on a misspelled right before anything is previewed or sent.
    admin.administrator_rights(granted)
    with session(_ctx()) as client:
        _emit(
            perform(
                _ctx(),
                lambda w, d: admin.promote(
                    client,
                    chat,
                    user,
                    rights=granted,
                    title=title,
                    allow_write=w,
                    allow_destructive=d,
                ),
            )
        )


# -- topic / folder / draft ---------------------------------------------
topic_app = typer.Typer(no_args_is_help=True)
app.add_typer(topic_app, name="topic")


@topic_app.command("list", context_settings=REF_ARGS)
@handle_errors
def topic_list(chat: str = typer.Argument(...)) -> None:
    from tdelegram.api import topics

    with session(_ctx()) as client:
        _emit_many(topics.iter_topics(client, chat))


folder_app = typer.Typer(no_args_is_help=True)
app.add_typer(folder_app, name="folder")


@folder_app.command("list")
@handle_errors
def folder_list() -> None:
    from tdelegram.api import folders

    with session(_ctx()) as client:
        _emit_many(folders.list_folders(client))


draft_app = typer.Typer(no_args_is_help=True)
app.add_typer(draft_app, name="draft")


@draft_app.command("set")
@handle_errors
def draft_set(
    chat: str = typer.Option(..., "--chat"), text: str = typer.Option(..., "--text")
) -> None:
    from tdelegram.api import drafts

    with session(_ctx()) as client:
        _emit(perform(_ctx(), lambda w, d: drafts.set_draft(client, chat, text, allow_write=w)))


# -- bot / story / secret / proxy ---------------------------------------
bot_app = typer.Typer(no_args_is_help=True)
app.add_typer(bot_app, name="bot")


@bot_app.command("callback", context_settings=REF_ARGS)
@handle_errors
def bot_callback(query_id: int = typer.Argument(...)) -> None:
    _emit(run_call(_ctx(), "answerCallbackQuery", {"callback_query_id": query_id}))


@bot_app.command("inline")
@handle_errors
def bot_inline(
    bot: int = typer.Option(..., "--bot"), query: str = typer.Option(..., "--query")
) -> None:
    from tdelegram.api import bots

    with session(_ctx()) as client:
        _emit(perform(_ctx(), lambda w, d: bots.inline_results(client, bot, query, allow_write=w)))


story_app = typer.Typer(no_args_is_help=True)
app.add_typer(story_app, name="story")


@story_app.command("list", context_settings=REF_ARGS)
@handle_errors
def story_list(chat: str = typer.Argument(...)) -> None:
    from tdelegram.api import stories

    with session(_ctx()) as client:
        _emit(stories.list_archived_stories(client, chat))


secret_app = typer.Typer(no_args_is_help=True)
app.add_typer(secret_app, name="secret")


@secret_app.command("create", context_settings=REF_ARGS)
@handle_errors
def secret_create(user: int = typer.Argument(...)) -> None:
    from tdelegram.api import secret

    with session(_ctx()) as client:
        _emit(perform(_ctx(), lambda w, d: secret.create_secret(client, user, allow_write=w)))


proxy_app = typer.Typer(
    no_args_is_help=True,
    help="Reach Telegram through a proxy. Works before login, which is when a blocked "
    "network needs it: add one, then run `auth login`.",
)
app.add_typer(proxy_app, name="proxy")


@proxy_app.command("list")
@handle_errors
def proxy_list() -> None:
    """Stored proxies; secrets and passwords are left out."""
    from tdelegram.api import proxies

    with setup_session(_ctx()) as client:
        _emit_many(proxies.proxy_record(p) for p in proxies.list_proxies(client).get("proxies", []))


@proxy_app.command("add")
@handle_errors
def proxy_add(
    link: str = typer.Argument(
        ..., help="tg://proxy?..., t.me/proxy?..., tg://socks?..., socks5://host:port, http://..."
    ),
    comment: str = typer.Option("", "--comment"),
    enable: bool = typer.Option(True, "--enable/--no-enable", help="Switch to it now"),
) -> None:
    """Store a proxy from a shared link, and by default switch to it."""
    from tdelegram.api import proxies

    proxies.parse_proxy_link(link)  # a bad link fails before anything is opened
    with setup_session(_ctx()) as client:
        added = perform(
            _ctx(),
            lambda w, d: proxies.add_proxy_link(
                client, link, enable=enable, comment=comment, allow_write=w
            ),
        )
        _emit(proxies.proxy_record(added))


@proxy_app.command("enable")
@handle_errors
def proxy_enable(proxy_id: int = typer.Argument(...)) -> None:
    from tdelegram.api import proxies

    with setup_session(_ctx()) as client:
        _emit(perform(_ctx(), lambda w, d: proxies.enable_proxy(client, proxy_id, allow_write=w)))


@proxy_app.command("disable")
@handle_errors
def proxy_disable() -> None:
    """Connect directly again."""
    from tdelegram.api import proxies

    with setup_session(_ctx()) as client:
        _emit(perform(_ctx(), lambda w, d: proxies.disable_proxy(client, allow_write=w)))


@proxy_app.command("remove")
@handle_errors
def proxy_remove(proxy_id: int = typer.Argument(...)) -> None:
    from tdelegram.api import proxies

    with setup_session(_ctx()) as client:
        _emit(
            perform(
                _ctx(),
                lambda w, d: proxies.remove_proxy(
                    client, proxy_id, allow_write=w, allow_destructive=d
                ),
            )
        )


@proxy_app.command("ping")
@handle_errors
def proxy_ping(
    proxy_id: int | None = typer.Argument(None, help="Omit to ping Telegram directly"),
) -> None:
    """Seconds to reach Telegram through a stored proxy, or directly."""
    from tdelegram.api import proxies

    with setup_session(_ctx()) as client:
        seconds = proxies.ping_proxy(client, proxy_id).get("seconds")
        _emit({"proxy_id": proxy_id, "seconds": seconds})


@proxy_app.command("check")
@handle_errors
def proxy_check(
    proxy_id: int = typer.Argument(...),
    timeout: float = typer.Option(10.0, "--timeout", help="Seconds to wait"),
) -> None:
    """Whether a stored proxy can reach Telegram at all; fails if it cannot."""
    from tdelegram.api import proxies

    with setup_session(_ctx()) as client:
        proxies.check_proxy(client, proxy_id, timeout=timeout)
        _emit({"proxy_id": proxy_id, "ok": True})


# -- updates ------------------------------------------------------------
updates_app = typer.Typer(no_args_is_help=True)
app.add_typer(updates_app, name="updates")


@updates_app.command("follow")
@handle_errors
def updates_follow(types: str = typer.Option("", help="Comma-separated @type filter")) -> None:
    from tdelegram.api import updates as updates_api

    wanted = [t.strip() for t in types.split(",") if t.strip()]
    with session(_ctx()) as client:
        _emit_many(updates_api.follow_filtered(client, wanted))


# -- raw call (escape hatch, same gate) ----------------------------------
@app.command("call")
@handle_errors
def raw_call(
    request: str = typer.Option(..., "--request", help="Raw TDLib JSON request"),
    validate: bool = typer.Option(
        True,
        "--validate/--no-validate",
        help="Refuse a request that does not match the pinned TDLib schema",
    ),
) -> None:
    from tdelegram import schema
    from tdelegram.errors import DestructiveConfirmationRequired, WriteConfirmationRequired

    try:
        obj = json.loads(request)
    except json.JSONDecodeError as exc:
        warn(f"Invalid --request JSON: {exc}")
        raise SystemExit(2) from None
    if not isinstance(obj, dict):
        warn("--request must be a JSON object.")
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
    problems = schema.validate(obj) if validate else []
    if problems:
        # TDLib would not refuse most of these: it drops a field it does not
        # know and runs the call without it.
        warn(json.dumps({"method": method, "schema_problems": problems}, indent=2))
        warn(
            f"Refused: TDLib would run {method} with these silently ignored. Check "
            f"`tdelegram describe {method}`, or pass --no-validate if the loaded TDLib "
            "is newer than the schema this build was made from."
        )
        raise SystemExit(2) from None
    if verdict != "read" and not _ctx().yes:
        # Previewing needs no session, so refuse before logging in.
        gated = (
            DestructiveConfirmationRequired(method, obj)
            if verdict == "destructive"
            else WriteConfirmationRequired(method, obj)
        )
        show_preview(gated)
        warn("Preview only: re-run with --yes to perform.")
        raise SystemExit(2) from None
    _emit(run_call(_ctx(), method, params))


@app.command("describe")
@handle_errors
def describe(name: str = typer.Argument(..., help="A TDLib function, object or type")) -> None:
    """What the pinned TDLib schema says about a name, and the gate's verdict.

    For composing `call` requests: every parameter and its type, the objects an
    abstract type accepts, and a link to TDLib's own documentation.
    """
    from tdelegram import schema

    entry = schema.describe(name)
    if entry is None:
        close = schema.suggest(name)
        hint = f" Did you mean: {', '.join(close)}?" if close else ""
        raise ValueError(f"{name!r} is not in the TDLib schema.{hint}")
    if entry["kind"] == "function":
        entry["verdict"] = safety.verdict(name)
        entry["reason"] = safety.reason(name)
    _emit(entry)


@app.command("version")
def version() -> None:
    from tdelegram import __version__

    typer.echo(__version__)


def main() -> None:
    app()


if __name__ == "__main__":
    main()
