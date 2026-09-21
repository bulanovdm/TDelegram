"""Admin: promote/demote/ban/restrict/permissions/slowmode/invites."""

from __future__ import annotations

from typing import Any

from tdelegram.api.chats import resolve_id
from tdelegram.client import TelegramClient


def set_member_status(
    client: TelegramClient,
    chat_ref: str,
    user_id: int,
    status: dict[str, Any],
    *,
    allow_write: bool = False,
) -> dict[str, Any]:
    return client.call(
        "setChatMemberStatus",
        {
            "chat_id": resolve_id(client, chat_ref),
            "member_id": {"@type": "messageSenderUser", "user_id": user_id},
            "status": status,
        },
        allow_write=allow_write,
    )


def promote(
    client: TelegramClient,
    chat_ref: str,
    user_id: int,
    *,
    title: str = "",
    allow_write: bool = False,
) -> dict[str, Any]:
    return set_member_status(
        client,
        chat_ref,
        user_id,
        {"@type": "chatMemberStatusAdministrator", "custom_title": title, "can_be_edited": True},
        allow_write=allow_write,
    )


def demote(
    client: TelegramClient, chat_ref: str, user_id: int, *, allow_write: bool = False
) -> dict[str, Any]:
    return set_member_status(
        client, chat_ref, user_id, {"@type": "chatMemberStatusMember"}, allow_write=allow_write
    )


def ban(
    client: TelegramClient,
    chat_ref: str,
    user_id: int,
    *,
    until: int = 0,
    allow_write: bool = False,
    allow_destructive: bool = False,
) -> dict[str, Any]:
    return (
        set_member_status(
            client,
            chat_ref,
            user_id,
            {"@type": "chatMemberStatusBanned", "banned_until_date": until},
            allow_write=allow_write,
        )
        if False
        else client.call(
            "banChatMember",
            {
                "chat_id": resolve_id(client, chat_ref),
                "member_id": {"@type": "messageSenderUser", "user_id": user_id},
                "banned_until_date": until,
            },
            allow_write=allow_write,
            allow_destructive=allow_destructive,
        )
    )


def restrict(
    client: TelegramClient,
    chat_ref: str,
    user_id: int,
    *,
    permissions: dict[str, Any] | None = None,
    allow_write: bool = False,
) -> dict[str, Any]:
    return set_member_status(
        client,
        chat_ref,
        user_id,
        {
            "@type": "chatMemberStatusRestricted",
            "permissions": permissions
            or {"@type": "chatPermissions", "can_send_basic_messages": False},
            "is_member": True,
        },
        allow_write=allow_write,
    )


def unrestrict(
    client: TelegramClient, chat_ref: str, user_id: int, *, allow_write: bool = False
) -> dict[str, Any]:
    return demote(client, chat_ref, user_id, allow_write=allow_write)


def slowmode(
    client: TelegramClient, chat_ref: str, delay_seconds: int, *, allow_write: bool = False
) -> dict[str, Any]:
    return client.call(
        "setChatSlowModeDelay",
        {"chat_id": resolve_id(client, chat_ref), "slow_mode_delay": delay_seconds},
        allow_write=allow_write,
    )


def create_invite(
    client: TelegramClient, chat_ref: str, *, allow_write: bool = False
) -> dict[str, Any]:
    return client.call(
        "createChatInviteLink", {"chat_id": resolve_id(client, chat_ref)}, allow_write=allow_write
    )


def revoke_invite(
    client: TelegramClient,
    chat_ref: str,
    invite_link: str,
    *,
    allow_write: bool = False,
    allow_destructive: bool = False,
) -> dict[str, Any]:
    return client.call(
        "revokeChatInviteLink",
        {"chat_id": resolve_id(client, chat_ref), "invite_link": invite_link},
        allow_write=allow_write,
        allow_destructive=allow_destructive,
    )
