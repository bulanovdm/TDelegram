"""Contacts: listing, adding, importing, removing."""

from __future__ import annotations

from typing import Any

from tdelegram.client import TelegramClient


def list_contacts(client: TelegramClient) -> dict[str, Any]:
    return client.call("getContacts", {})


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
            "contact": {
                "@type": "contact",
                "phone_number": "",
                "first_name": first_name,
                "last_name": last_name,
                "user_id": user_id,
            }
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
                {
                    "@type": "contact",
                    "phone_number": p,
                    "first_name": p,
                    "last_name": "",
                    "user_id": 0,
                }
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
