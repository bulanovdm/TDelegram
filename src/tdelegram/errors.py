"""Structured errors for TDelegram with flood handling."""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any


class TelegramError(Exception):
    """Base error for a TDLib `error` object."""

    def __init__(self, code: int, message: str, method: str = "") -> None:
        super().__init__(f"TDLib error {code} in {method or 'request'}: {message}")
        self.code = code
        self.message = message
        self.method = method


class AuthError(TelegramError):
    """Authentication / authorization failures (401)."""


class TelegramPermissionError(TelegramError):
    """Forbidden / missing rights (403)."""


class NotFoundError(TelegramError):
    """Requested object does not exist (404)."""


class InvalidRequest(TelegramError):
    """Bad request arguments (400 and friends)."""


class FloodWait(TelegramError):
    """Rate limited. `retry_after` is seconds to wait."""

    def __init__(self, code: int, message: str, method: str, retry_after: int) -> None:
        super().__init__(code, message, method)
        self.retry_after = retry_after


class TransportError(TelegramError):
    """Local transport failure (no TDLib error code)."""

    def __init__(self, message: str) -> None:
        super().__init__(-1, message, "transport")


class TelegramTimeoutError(TransportError):
    """Timed out waiting for a TDLib response."""


class ConfirmationRequired(TelegramError):
    """A mutating call was attempted without confirmation."""

    def __init__(self, method: str, verdict: str, preview: dict[str, Any]) -> None:
        super().__init__(0, f"{verdict} operation {method} requires confirmation", method)
        self.verdict = verdict
        self.preview = preview


class WriteConfirmationRequired(ConfirmationRequired):
    def __init__(self, method: str, preview: dict[str, Any]) -> None:
        super().__init__(method, "write", preview)


class DestructiveConfirmationRequired(ConfirmationRequired):
    def __init__(self, method: str, preview: dict[str, Any]) -> None:
        super().__init__(method, "destructive", preview)


def parse_retry_after(message: str) -> int:
    """Extract retry seconds from a FLOOD_WAIT / SLOWMODE_WAIT message."""
    import re

    match = re.search(r"retry after (\d+)", message, re.IGNORECASE)
    if match:
        return int(match.group(1))
    match = re.search(r"wait (\d+)", message, re.IGNORECASE)
    if match:
        return int(match.group(1))
    return 0


def from_td_error(obj: dict[str, Any], method: str = "") -> TelegramError:
    """Map a TDLib `error` object to the exception hierarchy."""
    code = int(obj.get("code", 0))
    message = str(obj.get("message", "unknown error"))
    lowered = message.lower()
    if "flood" in lowered or code == 429 or "slowmode" in lowered or "retry after" in lowered:
        return FloodWait(code, message, method, parse_retry_after(message))
    if code == 401:
        return AuthError(code, message, method)
    if code == 403:
        return TelegramPermissionError(code, message, method)
    if code == 404:
        return NotFoundError(code, message, method)
    if code == 400:
        return InvalidRequest(code, message, method)
    if code == 420:
        return FloodWait(code, message, method, parse_retry_after(message))
    if code == 429:
        return FloodWait(code, message, method, parse_retry_after(message))
    return TelegramError(code, message, method)


@dataclass(frozen=True)
class RetryPolicy:
    """Bounded retry for reads. Writes never auto-retry on FloodWait."""

    max_attempts: int = 4
    max_total_wait: float = 60.0
    base_delay: float = 1.0

    def delay_for(self, attempt: int, retry_after: int) -> float:
        hinted = float(retry_after) if retry_after > 0 else self.base_delay * (2.0**attempt)
        return min(hinted, self.max_total_wait)


def should_retry_read(error: TelegramError, attempt: int, policy: RetryPolicy) -> bool:
    return isinstance(error, FloodWait) and attempt < policy.max_attempts


def sleep_retry(delay: float) -> None:
    time.sleep(delay)
