"""TDelegram: a full-featured Telegram client library + CLI backed by TDLib."""

from importlib import metadata as _metadata

from tdelegram.client import TelegramClient
from tdelegram.config import ClientConfig
from tdelegram.errors import (
    AuthError,
    FloodWait,
    InvalidRequest,
    NotFoundError,
    TelegramError,
    TelegramPermissionError,
    TransportError,
)

__all__ = [
    "TelegramClient",
    "ClientConfig",
    "TelegramError",
    "AuthError",
    "TelegramPermissionError",
    "NotFoundError",
    "InvalidRequest",
    "FloodWait",
    "TransportError",
]


def _installed_version() -> str:
    # pyproject.toml is the only place the version is written. A second copy
    # here had to be bumped by hand and nothing checked that it was, so a
    # release that bumped only pyproject.toml would have reported the version
    # before it. Reinstall after a bump for a dev checkout to see it.
    try:
        return _metadata.version("tdelegram")
    except _metadata.PackageNotFoundError:
        # Imported from a source tree that was never installed.
        return "0+unknown"


__version__ = _installed_version()
