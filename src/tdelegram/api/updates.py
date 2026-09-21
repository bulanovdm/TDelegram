"""Live update stream."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

from tdelegram.client import TelegramClient


def follow(client: TelegramClient, *, timeout: float = 3600.0) -> Iterator[dict[str, Any]]:
    import time

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        event = client.next_update(timeout=1.0)
        if event is None:
            continue
        yield event


def follow_filtered(
    client: TelegramClient, types: list[str], *, timeout: float = 3600.0
) -> Iterator[dict[str, Any]]:
    for event in follow(client, timeout=timeout):
        if not types or event.get("@type") in types:
            yield event
