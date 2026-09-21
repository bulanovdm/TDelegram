"""Account: info, profile, privacy, sessions."""

from __future__ import annotations

from typing import Any

from tdelegram.client import TelegramClient


def info(client: TelegramClient) -> dict[str, Any]:
    return client.call("getMe", {})


def full_info(client: TelegramClient, user_id: int) -> dict[str, Any]:
    return client.call("getUserFullInfo", {"user_id": user_id})


def set_profile(
    client: TelegramClient,
    *,
    first_name: str | None = None,
    last_name: str | None = None,
    bio: str | None = None,
    allow_write: bool = False,
) -> dict[str, Any]:
    results: dict[str, Any] = {}
    if first_name is not None or last_name is not None:
        results["name"] = client.call(
            "setName",
            {"first_name": first_name or "", "last_name": last_name or ""},
            allow_write=allow_write,
        )
    if bio is not None:
        results["bio"] = client.call("setBio", {"bio": bio}, allow_write=allow_write)
    return results or {"@type": "ok"}


def sessions(client: TelegramClient) -> dict[str, Any]:
    return client.call("getActiveSessions", {})


def terminate_session(
    client: TelegramClient,
    session_id: int,
    *,
    allow_write: bool = False,
    allow_destructive: bool = False,
) -> dict[str, Any]:
    return client.call(
        "terminateSession",
        {"session_id": session_id},
        allow_write=allow_write,
        allow_destructive=allow_destructive,
    )


def terminate_others(
    client: TelegramClient, *, allow_write: bool = False, allow_destructive: bool = False
) -> dict[str, Any]:
    return client.call(
        "terminateAllOtherSessions",
        {},
        allow_write=allow_write,
        allow_destructive=allow_destructive,
    )


def privacy(client: TelegramClient, setting: str) -> dict[str, Any]:
    return client.call("getUserPrivacySettingRules", {"setting": {"@type": setting}})
