"""Admin: promote/demote/ban/restrict/permissions/slowmode/invites."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from tdelegram.api.chats import resolve, resolve_id
from tdelegram.client import TelegramClient

# The least an administrator can hold, and what `promote` grants unless told
# otherwise: every other right implies it.
DEFAULT_RIGHTS = ("manage_chat",)


def administrator_rights(rights: Iterable[str]) -> dict[str, Any]:
    """chatAdministratorRights granting exactly `rights`, and nothing else.

    Names are the schema's fields with or without the `can_` prefix, so
    `delete_messages` and `can_delete_messages` both work; `is_anonymous` is
    taken as it is.
    """
    from tdelegram import schema

    entry = schema.constructor("chatAdministratorRights")
    known = list(entry["fields"]) if entry else []
    granted: dict[str, Any] = {"@type": "chatAdministratorRights"}
    for right in rights:
        name = right.strip().lower().replace("-", "_")
        field = name if name in known else f"can_{name}"
        if field not in known:
            short = ", ".join(k.removeprefix("can_") for k in known)
            raise ValueError(f"Unknown administrator right {right!r}; choose from: {short}.")
        granted[field] = True
    return granted


def _set_status(
    client: TelegramClient,
    chat_id: int,
    user_id: int,
    status: dict[str, Any],
    *,
    allow_write: bool,
    allow_destructive: bool,
) -> dict[str, Any]:
    return client.call(
        "setChatMemberStatus",
        {
            "chat_id": chat_id,
            "member_id": {"@type": "messageSenderUser", "user_id": user_id},
            "status": status,
        },
        allow_write=allow_write,
        allow_destructive=allow_destructive,
    )


def set_member_status(
    client: TelegramClient,
    chat_ref: str,
    user_id: int,
    status: dict[str, Any],
    *,
    allow_write: bool = False,
    allow_destructive: bool = False,
) -> dict[str, Any]:
    """Set a member's status. Can ban, so it is gated like banChatMember."""
    return _set_status(
        client,
        resolve_id(client, chat_ref),
        user_id,
        status,
        allow_write=allow_write,
        allow_destructive=allow_destructive,
    )


def promote(
    client: TelegramClient,
    chat_ref: str,
    user_id: int,
    *,
    rights: Iterable[str] = DEFAULT_RIGHTS,
    title: str = "",
    allow_write: bool = False,
    allow_destructive: bool = False,
) -> dict[str, Any]:
    """Make a member an administrator with exactly `rights`.

    The status used to carry `custom_title` and no `rights`, neither of which
    TDLib's administrator status takes any more, so the promotion granted
    nothing. A title is now the member's tag, set by a second call -- which
    channels do not have, so a title there is refused before anything changes
    rather than failing after the promotion went through.
    """
    chat = resolve(client, chat_ref)
    if title and (chat.get("type") or {}).get("is_channel"):
        raise ValueError("Channels have no member titles; promote without --title.")
    chat_id = int(chat["id"])
    result = _set_status(
        client,
        chat_id,
        user_id,
        {"@type": "chatMemberStatusAdministrator", "rights": administrator_rights(rights)},
        allow_write=allow_write,
        allow_destructive=allow_destructive,
    )
    if title:
        client.call(
            "setChatMemberTag",
            {"chat_id": chat_id, "user_id": user_id, "tag": title},
            allow_write=allow_write,
        )
    return result


def demote(
    client: TelegramClient,
    chat_ref: str,
    user_id: int,
    *,
    allow_write: bool = False,
    allow_destructive: bool = False,
) -> dict[str, Any]:
    return set_member_status(
        client,
        chat_ref,
        user_id,
        {"@type": "chatMemberStatusMember"},
        allow_write=allow_write,
        allow_destructive=allow_destructive,
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
    return client.call(
        "banChatMember",
        {
            "chat_id": resolve_id(client, chat_ref),
            "member_id": {"@type": "messageSenderUser", "user_id": user_id},
            "banned_until_date": until,
        },
        allow_write=allow_write,
        allow_destructive=allow_destructive,
    )


def restrict(
    client: TelegramClient,
    chat_ref: str,
    user_id: int,
    *,
    permissions: dict[str, Any] | None = None,
    allow_write: bool = False,
    allow_destructive: bool = False,
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
        allow_destructive=allow_destructive,
    )


def unrestrict(
    client: TelegramClient,
    chat_ref: str,
    user_id: int,
    *,
    allow_write: bool = False,
    allow_destructive: bool = False,
) -> dict[str, Any]:
    return demote(
        client, chat_ref, user_id, allow_write=allow_write, allow_destructive=allow_destructive
    )


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
