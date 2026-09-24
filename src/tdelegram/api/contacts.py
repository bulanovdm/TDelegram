"""Contacts: listing, adding, importing, removing."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

from tdelegram.client import TelegramClient


def list_contacts(client: TelegramClient) -> dict[str, Any]:
    """TDLib's answer as it is: a count and the contacts' user ids."""
    return client.call("getContacts", {})


def iter_contacts(client: TelegramClient) -> Iterator[dict[str, Any]]:
    """The account's contacts as user records -- names and usernames, not bare ids."""
    from tdelegram.api.users import get_user

    for user_id in list_contacts(client).get("user_ids") or []:
        yield get_user(client, int(user_id))


def add_contact(
    client: TelegramClient,
    user_id: int,
    *,
    first_name: str = "",
    last_name: str = "",
    allow_write: bool = False,
) -> dict[str, Any]:
    return client.call(
        "addContact",
        {
            "user_id": user_id,
            "contact": {
                "@type": "importedContact",
                "phone_number": "",
                "first_name": first_name,
                "last_name": last_name,
            },
            "share_phone_number": False,
        },
        allow_write=allow_write,
    )


def import_contacts(
    client: TelegramClient, phones: list[str], *, allow_write: bool = False
) -> dict[str, Any]:
    return client.call(
        "importContacts",
        {
            "contacts": [
                {"@type": "importedContact", "phone_number": p, "first_name": p, "last_name": ""}
                for p in phones
            ]
        },
        allow_write=allow_write,
    )


def remove_contacts(
    client: TelegramClient,
    user_ids: list[int],
    *,
    allow_write: bool = False,
    allow_destructive: bool = False,
) -> dict[str, Any]:
    return client.call(
        "removeContacts",
        {"user_ids": user_ids},
        allow_write=allow_write,
        allow_destructive=allow_destructive,
    )


# -- search -----------------------------------------------------------
