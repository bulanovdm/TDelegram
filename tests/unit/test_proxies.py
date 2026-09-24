"""Proxy links, parsed without TDLib.

Someone behind a block needs a proxy before there is any session to ask TDLib
with, so the links are parsed locally, in every form they are shared in.
"""

from __future__ import annotations

import pytest

from tdelegram import schema
from tdelegram.api.proxies import parse_proxy_link

MTPROTO_SECRET = "ee1603010200010001fc030386e24c3add6e2e616b616d61692e636f6d"


@pytest.mark.parametrize(
    "link",
    [
        f"tg://proxy?server=proxy.example.org&port=443&secret={MTPROTO_SECRET}",
        f"https://t.me/proxy?server=proxy.example.org&port=443&secret={MTPROTO_SECRET}",
        f"t.me/proxy?server=proxy.example.org&port=443&secret={MTPROTO_SECRET}",
        f"https://telegram.me/proxy?port=443&server=proxy.example.org&secret={MTPROTO_SECRET}",
    ],
)
def test_mtproto_links_in_every_shared_form(link: str) -> None:
    proxy = parse_proxy_link(link)
    assert proxy == {
        "@type": "proxy",
        "server": "proxy.example.org",
        "port": 443,
        "type": {"@type": "proxyTypeMtproto", "secret": MTPROTO_SECRET},
    }


def test_a_base64_secret_keeps_its_plus_signs() -> None:
    """parse_qs would read "+" as a space and corrupt the secret."""
    proxy = parse_proxy_link("tg://proxy?server=h&port=1&secret=7gAB+C/9")
    assert proxy["type"]["secret"] == "7gAB+C/9"


def test_socks_links_carry_their_credentials() -> None:
    proxy = parse_proxy_link("tg://socks?server=10.0.0.1&port=1080&user=me&pass=p%40ss")
    assert proxy["type"] == {"@type": "proxyTypeSocks5", "username": "me", "password": "p@ss"}
    assert parse_proxy_link("https://t.me/socks?server=h&port=1080")["type"]["username"] == ""


def test_a_plain_socks5_url_such_as_tor() -> None:
    proxy = parse_proxy_link("socks5://127.0.0.1:9050")
    assert (proxy["server"], proxy["port"]) == ("127.0.0.1", 9050)
    assert proxy["type"]["@type"] == "proxyTypeSocks5"
    auth = parse_proxy_link("socks5://alice:s%3Acret@proxy.lan:1080")
    assert auth["type"]["username"] == "alice" and auth["type"]["password"] == "s:cret"


def test_an_http_proxy_url() -> None:
    proxy = parse_proxy_link("http://user:pw@proxy.corp:3128")
    assert proxy["type"] == {
        "@type": "proxyTypeHttp",
        "username": "user",
        "password": "pw",
        "http_only": False,
    }


@pytest.mark.parametrize(
    "link",
    [
        "tg://proxy?server=h&port=443",  # an MTProto proxy without its secret
        "tg://proxy?port=443&secret=abc",  # no server
        "tg://proxy?server=h&port=http&secret=abc",  # no usable port
        "tg://socks?server=h&port=70000",  # port out of range
        "socks5://127.0.0.1:99999",
        "tg://resolve?domain=durov",  # a Telegram link, not a proxy
        "ftp://example.org:21",
        "just some text",
    ],
)
def test_what_is_not_a_usable_proxy_is_refused(link: str) -> None:
    with pytest.raises(ValueError):
        parse_proxy_link(link)


def test_every_parsed_proxy_is_one_tdlib_accepts() -> None:
    for link in (
        f"tg://proxy?server=h&port=443&secret={MTPROTO_SECRET}",
        "tg://socks?server=h&port=1080&user=u&pass=p",
        "http://h:3128",
    ):
        request = {"@type": "addProxy", "proxy": parse_proxy_link(link), "enable": True}
        assert schema.validate(request) == [], link
