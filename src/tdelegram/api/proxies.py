"""Proxy configuration and connectivity probes.

Proxies are how Telegram stays reachable where it is blocked, so they have to
work before anything else does: TDLib accepts every call here before the
account is authorized, and keeps the proxies it is given in the profile's
database for every later process.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import unquote, urlsplit

from tdelegram.client import TelegramClient

_TELEGRAM_HOSTS = frozenset({"t.me", "www.t.me", "telegram.me", "telegram.dog"})


def parse_proxy_link(link: str) -> dict[str, Any]:
    """A TDLib `proxy` object from a link in any of the forms proxies circulate in.

    - `tg://proxy?server=H&port=P&secret=S` or `https://t.me/proxy?...` -- MTProto
    - `tg://socks?server=H&port=P&user=U&pass=W` or `https://t.me/socks?...` -- SOCKS5
    - `socks5://[user:pass@]host:port` -- SOCKS5, e.g. Tor on 127.0.0.1:9050
    - `http://[user:pass@]host:port` -- an HTTP CONNECT proxy

    Parsed here rather than by TDLib so that it works before there is a
    session at all, which is exactly when someone behind a block needs it.
    """
    text = link.strip()
    if "://" not in text and text.split("/", 1)[0].lower() in _TELEGRAM_HOSTS:
        text = "https://" + text  # t.me/proxy?... pasted without a scheme
    try:
        parts = urlsplit(text)
        host = (parts.hostname or "").lower()
        port = parts.port
    except ValueError as exc:
        raise ValueError(f"Not a usable proxy link: {link!r} ({exc}).") from None
    scheme = parts.scheme.lower()
    if scheme == "tg" or (scheme in ("http", "https") and host in _TELEGRAM_HOSTS):
        kind = host if scheme == "tg" else parts.path.strip("/").lower()
        query = _query(parts.query)
        if kind == "proxy":
            secret = query.get("secret", "")
            if not secret:
                raise ValueError(f"MTProto proxy link has no secret: {link!r}.")
            proxy_type: dict[str, Any] = {"@type": "proxyTypeMtproto", "secret": secret}
        elif kind == "socks":
            proxy_type = {
                "@type": "proxyTypeSocks5",
                "username": query.get("user", ""),
                "password": query.get("pass", ""),
            }
        else:
            raise ValueError(f"Not a proxy link: {link!r}.")
        return _proxy(query.get("server", ""), query.get("port", ""), proxy_type, link)
    credentials = {
        "username": unquote(parts.username or ""),
        "password": unquote(parts.password or ""),
    }
    if scheme in ("socks5", "socks5h", "socks"):
        return _proxy(host, port, {"@type": "proxyTypeSocks5", **credentials}, link)
    if scheme == "http":
        return _proxy(
            host, port, {"@type": "proxyTypeHttp", **credentials, "http_only": False}, link
        )
    raise ValueError(
        f"Not a proxy link: {link!r}. Expected tg://proxy, t.me/proxy, tg://socks, "
        "socks5://host:port or http://host:port."
    )


def _query(raw: str) -> dict[str, str]:
    # parse_qs would turn a "+" into a space, and base64 proxy secrets use "+".
    pairs = (item.partition("=") for item in raw.split("&") if item)
    return {unquote(key): unquote(value) for key, _, value in pairs}


def _proxy(server: str, port: Any, proxy_type: dict[str, Any], link: str) -> dict[str, Any]:
    if not server:
        raise ValueError(f"Proxy link names no server: {link!r}.")
    try:
        number = int(port)
    except (TypeError, ValueError):
        raise ValueError(f"Proxy link has no valid port: {link!r}.") from None
    if not 0 < number < 65536:
        raise ValueError(f"Proxy port out of range in {link!r}.")
    return {"@type": "proxy", "server": server, "port": number, "type": proxy_type}


def list_proxies(client: TelegramClient) -> dict[str, Any]:
    return client.call("getProxies", {})


def _add(
    client: TelegramClient,
    proxy: dict[str, Any],
    *,
    enable: bool,
    comment: str,
    allow_write: bool,
) -> dict[str, Any]:
    return client.call(
        "addProxy",
        {"proxy": proxy, "enable": enable, "comment": comment},
        allow_write=allow_write,
    )


def add_proxy(
    client: TelegramClient,
    server: str,
    port: int,
    *,
    proxy_type: dict[str, Any] | None = None,
    enable: bool = False,
    comment: str = "",
    allow_write: bool = False,
) -> dict[str, Any]:
    proxy = {
        "@type": "proxy",
        "server": server,
        "port": port,
        "type": proxy_type or {"@type": "proxyTypeSocks5", "username": "", "password": ""},
    }
    return _add(client, proxy, enable=enable, comment=comment, allow_write=allow_write)


def add_proxy_link(
    client: TelegramClient,
    link: str,
    *,
    enable: bool = True,
    comment: str = "",
    allow_write: bool = False,
) -> dict[str, Any]:
    """Store the proxy a link describes, and by default switch to it."""
    return _add(
        client, parse_proxy_link(link), enable=enable, comment=comment, allow_write=allow_write
    )


def enable_proxy(
    client: TelegramClient, proxy_id: int, *, allow_write: bool = False
) -> dict[str, Any]:
    return client.call("enableProxy", {"proxy_id": proxy_id}, allow_write=allow_write)


def disable_proxy(client: TelegramClient, *, allow_write: bool = False) -> dict[str, Any]:
    return client.call("disableProxy", {}, allow_write=allow_write)


def remove_proxy(
    client: TelegramClient,
    proxy_id: int,
    *,
    allow_write: bool = False,
    allow_destructive: bool = False,
) -> dict[str, Any]:
    return client.call(
        "removeProxy",
        {"proxy_id": proxy_id},
        allow_write=allow_write,
        allow_destructive=allow_destructive,
    )


def stored_proxy(client: TelegramClient, proxy_id: int) -> dict[str, Any]:
    """The `proxy` object stored under an id, which is what the probes take."""
    for added in list_proxies(client).get("proxies") or []:
        if added.get("id") == proxy_id and isinstance(added.get("proxy"), dict):
            return dict(added["proxy"])
    raise ValueError(f"No stored proxy has id {proxy_id}; see `tdelegram proxy list`.")


def ping_proxy(client: TelegramClient, proxy_id: int | None = None) -> dict[str, Any]:
    """Seconds to reach Telegram through a stored proxy, or directly with none.

    pingProxy takes the proxy itself now rather than its id, so the id is
    looked up first.
    """
    proxy = stored_proxy(client, proxy_id) if proxy_id is not None else None
    return client.call("pingProxy", {"proxy": proxy})


def check_proxy(
    client: TelegramClient, proxy_id: int, *, dc_id: int = 2, timeout: float = 10.0
) -> dict[str, Any]:
    """Whether a stored proxy can reach a Telegram data center at all (testProxy).

    Not named test_proxy: pytest would collect it from any test that imports it.
    """
    return client.call(
        "testProxy",
        {"proxy": stored_proxy(client, proxy_id), "dc_id": dc_id, "timeout": timeout},
        timeout=timeout + 5.0,
    )
