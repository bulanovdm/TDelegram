"""Proxy configuration and connectivity probes."""

from __future__ import annotations

from typing import Any

from tdelegram.client import TelegramClient


def list_proxies(client: TelegramClient) -> dict[str, Any]:
    return client.call("getProxies", {})


def add_proxy(
    client: TelegramClient,
    server: str,
    port: int,
    *,
    proxy_type: dict[str, Any] | None = None,
    allow_write: bool = False,
) -> dict[str, Any]:
    return client.call(
        "addProxy",
        {
            "server": server,
            "port": port,
            "type": proxy_type or {"@type": "proxyTypeSocks5", "username": "", "password": ""},
        },
        allow_write=allow_write,
    )


def enable_proxy(
    client: TelegramClient, proxy_id: int, *, allow_write: bool = False
) -> dict[str, Any]:
    return client.call("enableProxy", {"proxy_id": proxy_id}, allow_write=allow_write)


def disable_proxy(client: TelegramClient, *, allow_write: bool = False) -> dict[str, Any]:
    return client.call("disableProxy", {}, allow_write=allow_write)


def ping_proxy(client: TelegramClient, proxy_id: int) -> dict[str, Any]:
    return client.call("pingProxy", {"proxy_id": proxy_id})
