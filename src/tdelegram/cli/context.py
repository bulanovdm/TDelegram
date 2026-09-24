"""Shared CLI context: client construction, confirmation handling."""

from __future__ import annotations

import json
import sys
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, TypeVar

from tdelegram.auth import (
    ConsoleCredentialProvider,
    NonInteractiveCredentialProvider,
    current_state,
    run_auth,
)
from tdelegram.cli.output import emit, error_envelope, warn
from tdelegram.client import TelegramClient
from tdelegram.config import SessionLock, default_base_dir, discover_library
from tdelegram.errors import (
    ConfirmationRequired,
    DestructiveConfirmationRequired,
    TelegramError,
)
from tdelegram.transport import TdJsonTransport

T = TypeVar("T")


@dataclass
class Ctx:
    profile: str = "default"
    session_dir: str = ""
    fmt: str = "jsonl"
    output: str | None = None
    yes: bool = False
    verbose: bool = False
    no_retry: bool = False


def base_dir(ctx: Ctx) -> Path:
    """Where profiles and the secrets-file fallback live.

    `--session-dir` names the profile directory itself -- the one holding
    `tdlib/` and `files/` -- and secrets fall back to a file inside it.

    This used to sniff the path, climbing two levels when any component was
    literally named "profiles" and when the string happened to contain
    "profile". The same flag then meant different things for
    /opt/tg/profiles/work and /opt/tg/session, so where a secret was read
    from depended on how the user had named their directories.
    """
    if ctx.session_dir:
        return Path(ctx.session_dir).expanduser()
    return default_base_dir()


def make_client(
    ctx: Ctx, *, with_lock: bool = True, login: bool = True
) -> tuple[TelegramClient, SessionLock | None]:
    base = base_dir(ctx)
    profile_dir = (
        Path(ctx.session_dir).expanduser() if ctx.session_dir else (base / "profiles" / ctx.profile)
    )
    lock: SessionLock | None = None
    if with_lock:
        lock = SessionLock(profile_dir)
        lock.acquire()
    try:
        library = discover_library()
    except RuntimeError:
        if lock is not None:
            lock.release()
        raise
    if ctx.verbose:
        warn(f"Using libtdjson: {library}")
        warn(f"Profile dir: {profile_dir}")
    transport = TdJsonTransport(library)
    client = TelegramClient(transport)
    if ctx.no_retry:
        client.disable_retry()
    # Store dirs for auth.
    client._profile_dir = profile_dir  # type: ignore[attr-defined]
    client._base_dir = base  # type: ignore[attr-defined]
    # TDLib parameters live on the client instance, not in the database:
    # every process must replay the handshake before any call works.
    if login:
        try:
            ensure_login(client, ctx)
        except Exception:
            client.close()
            if lock is not None:
                lock.release()
            raise
    return client, lock


def ensure_login(client: TelegramClient, ctx: Ctx) -> None:
    profile_dir: Path = getattr(client, "_profile_dir", base_dir(ctx) / "profiles" / ctx.profile)
    db_dir = profile_dir / "tdlib"
    files_dir = profile_dir / "files"
    db_dir.mkdir(parents=True, exist_ok=True)
    files_dir.mkdir(parents=True, exist_ok=True)
    provider = ConsoleCredentialProvider(base_dir=getattr(client, "_base_dir", None))
    # Prime TDLib params via auth handshake if needed; run_auth drives updates.
    run_auth(
        client,
        provider,
        database_directory=str(db_dir),
        files_directory=str(files_dir),
    )


def report_auth_state(ctx: Ctx) -> dict[str, Any]:
    """Answer "am I logged in?" using stored secrets only, never a prompt."""
    client, lock = make_client(ctx, login=False)
    try:
        profile_dir: Path = getattr(
            client, "_profile_dir", base_dir(ctx) / "profiles" / ctx.profile
        )
        provider = NonInteractiveCredentialProvider(
            base_dir=getattr(client, "_base_dir", None)
        )
        state = current_state(
            client,
            provider,
            database_directory=str(profile_dir / "tdlib"),
            files_directory=str(profile_dir / "files"),
        )
        state["profile"] = ctx.profile
        return state
    finally:
        try:
            client.close()
        finally:
            if lock is not None:
                lock.release()


def interactive() -> bool:
    """Whether a human is at a terminal to answer a prompt."""
    return sys.stdin.isatty()


def confirm_destructive(method: str) -> bool:
    """Ask for the method name to be typed. Only a terminal can answer.

    That is the point of the second layer: `--yes` can be added by a script,
    a shell alias or an agent, so it is not evidence that a person looked. A
    destructive call therefore never runs from a pipe, a cron job or an agent's
    shell, only from a terminal where someone typed its name.
    """
    if not interactive():
        warn(
            f"{method} is destructive: after --yes it also needs its name typed at an "
            "interactive terminal, so it cannot run from a script, a pipe or an agent. "
            "Nothing was done."
        )
        return False
    # The prompt goes to stderr with everything else that is not data. input()
    # would print it on stdout, into the JSONL stream a caller may be parsing.
    warn(f"Type {method} to confirm, or anything else to cancel:")
    if sys.stdin.readline().strip() != method:
        warn("Not confirmed. Nothing was done.")
        return False
    return True


@contextmanager
def session(ctx: Ctx, *, login: bool = True) -> Iterator[TelegramClient]:
    """A client for this profile, holding its lock until the block ends."""
    client, lock = make_client(ctx, login=login)
    try:
        yield client
    finally:
        try:
            client.close()
        finally:
            if lock is not None:
                lock.release()


def show_preview(exc: ConfirmationRequired) -> None:
    """Put what a gated call would do on stderr, for a human to judge.

    A request that does not match TDLib's schema is flagged here too, because
    TDLib would run it with the unmatched fields silently dropped: approving
    the preview would approve something other than what it shows.
    """
    from tdelegram import safety, schema

    body: dict[str, Any] = {
        "method": exc.method,
        "verdict": exc.verdict,
        "reason": safety.reason(exc.method),
        "preview": exc.preview,
    }
    try:
        problems = schema.validate(exc.preview)
    except RuntimeError:
        problems = []
    if problems:
        body["schema_problems"] = problems
    warn(json.dumps({"confirmation_required": body}, indent=2, ensure_ascii=False))


def perform(ctx: Ctx, action: Callable[[bool, bool], T]) -> T:
    """Run `action(allow_write, allow_destructive)` under the CLI's gate rules.

    Without --yes, the first gated call raises: its preview goes to stderr and
    the command exits 2. With --yes, writes run and a destructive call stops
    once more for its method name at a terminal; confirmed, the action runs
    again with both permissions. Whatever it read before the gated call is
    simply read again.

    A `write` needs --yes. A `destructive` needs --yes *and* its typed name.
    The name used to be an alternative to --yes rather than an addition, so
    neither layer was actually required.
    """
    try:
        return action(ctx.yes, False)
    except ConfirmationRequired as exc:
        show_preview(exc)
        if not ctx.yes:
            warn("Preview only: re-run with --yes to perform.")
            raise SystemExit(2) from None
        if isinstance(exc, DestructiveConfirmationRequired) and confirm_destructive(exc.method):
            return action(True, True)
        raise SystemExit(2) from None


def run_call(
    ctx: Ctx,
    method: str,
    params: dict[str, Any],
    *,
    client: TelegramClient | None = None,
    login: bool = True,
) -> dict[str, Any]:
    """Call a method through the gate. Only --yes grants permission.

    There is deliberately no per-call-site "this is a write" flag: the
    registry decides, so forgetting to annotate a command cannot open a
    hole in the gate.
    """
    if client is not None:
        active = client
        return perform(
            ctx, lambda w, d: active.call(method, params, allow_write=w, allow_destructive=d)
        )
    with session(ctx, login=login) as own:
        return perform(
            ctx, lambda w, d: own.call(method, params, allow_write=w, allow_destructive=d)
        )


def handle_errors(func: Any) -> Any:
    import functools

    @functools.wraps(func)
    def _wrapper(*args: Any, **kwargs: Any) -> Any:
        try:
            return func(*args, **kwargs)
        except SystemExit:
            raise
        except ConfirmationRequired as exc:
            warn(
                json.dumps(
                    {"confirmation_required": {"method": exc.method, "verdict": exc.verdict}},
                    indent=2,
                )
            )
            warn("Preview only: re-run with --yes to perform.")
            raise SystemExit(2) from None
        except TelegramError as exc:
            emit(error_envelope(exc.code, exc.message, exc.method))
            raise SystemExit(1) from None
        except (RuntimeError, ValueError, TimeoutError) as exc:
            emit(error_envelope(1, str(exc)))
            raise SystemExit(1) from None

    return _wrapper
