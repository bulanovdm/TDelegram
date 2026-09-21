"""Shared CLI context: client construction, confirmation handling."""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

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
    WriteConfirmationRequired,
)
from tdelegram.transport import TdJsonTransport


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
    if ctx.session_dir:
        # --session-dir points at the profile dir or its parent; accept both.
        p = Path(ctx.session_dir).expanduser()
        if p.name == "tdlib" or p.name == "files":
            return p.parent.parent
        if (p / "tdlib").exists() or "profile" in str(p):
            # Heuristic: if it looks like a profile dir, use its parent parent.
            if p.parent.name == "profiles":
                return p.parent.parent
        return p
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
    own = client is None
    lock: SessionLock | None = None
    if own:
        client, lock = make_client(ctx, login=login)
        assert client is not None
    try:
        assert client is not None
        try:
            return client.call(
                method,
                params,
                allow_write=ctx.yes,
                allow_destructive=ctx.yes,
            )
        except (WriteConfirmationRequired, DestructiveConfirmationRequired) as exc:
            preview = {"preview": exc.preview, "verdict": exc.verdict, "method": method}
            warn(json.dumps({"confirmation_required": preview}, indent=2))
            if (
                isinstance(exc, DestructiveConfirmationRequired)
                and sys.stdin.isatty()
                and not ctx.yes
            ):
                answer = input("Type the method name to confirm destructive action: ").strip()
                if answer == method:
                    return client.call(method, params, allow_write=True, allow_destructive=True)
            warn("Preview only: re-run with --yes to perform.")
            raise SystemExit(2) from None
    finally:
        if own and client is not None:
            try:
                client.close()
            finally:
                if lock is not None:
                    lock.release()


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
