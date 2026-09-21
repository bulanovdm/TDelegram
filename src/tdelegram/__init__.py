"""TDelegram: a full-featured Telegram client library + CLI backed by TDLib."""

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

__version__ = "0.1.0"
