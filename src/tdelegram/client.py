"""TelegramClient facade: the single chokepoint for every TDLib call."""

from __future__ import annotations

import json
import queue
import time
from collections.abc import Callable
from typing import Any

from tdelegram import safety
from tdelegram.errors import (
    FloodWait,
    RetryPolicy,
    TelegramError,
    TelegramTimeoutError,
    from_td_error,
    should_retry_read,
    sleep_retry,
)
from tdelegram.loop import loop_for
from tdelegram.transport import Transport

UpdateHandler = Callable[[dict[str, Any]], None]


class Subscription:
    def __init__(self, unsubscribe: Callable[[], None]) -> None:
        self._unsubscribe = unsubscribe

    def unsubscribe(self) -> None:
        self._unsubscribe()


class TelegramClient:
    """Synchronous facade over a Transport + shared DispatchLoop."""

    def __init__(
        self,
        transport: Transport,
        *,
        retry_policy: RetryPolicy | None = None,
        default_timeout: float = 30.0,
    ) -> None:
        self._transport = transport
        self._loop = loop_for(transport)
        self._loop.start()
        self._client_id = transport.create_client_id()
        self._subscriber = self._loop.ensure_client(self._client_id)
        self._retry = retry_policy or RetryPolicy()
        self._default_timeout = default_timeout
        self._closed = False
        self._no_retry = False

    @property
    def client_id(self) -> int:
        return self._client_id

    @property
    def transport(self) -> Transport:
        return self._transport

    def disable_retry(self) -> None:
        self._no_retry = True

    # -- low-level -----------------------------------------------------
    def send_request(self, request: dict[str, Any]) -> dict[str, Any]:
        """Send without safety gate or error mapping (used by auth handshake)."""
        return self._loop.call(self._client_id, request, timeout=self._default_timeout)

    def execute(self, request: dict[str, Any]) -> dict[str, Any]:
        raw = self._transport.execute(json.dumps(request, separators=(",", ":")))
        if not raw:
            raise TelegramError(0, "td_execute returned no data", str(request.get("@type", "")))
        try:
            obj = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise TelegramError(
                0, "td_execute returned invalid JSON", str(request.get("@type", ""))
            ) from exc
        if isinstance(obj, dict) and obj.get("@type") == "error":
            raise from_td_error(obj, str(request.get("@type", "")))
        return obj  # type: ignore[no-any-return]

    # -- main entry ----------------------------------------------------
    def call(
        self,
        method: str,
        params: dict[str, Any] | None = None,
        *,
        timeout: float | None = None,
        allow_write: bool = False,
        allow_destructive: bool = False,
    ) -> dict[str, Any]:
        """Call a TDLib function through the write gate with error mapping.

        Raises WriteConfirmationRequired / DestructiveConfirmationRequired
        unless the matching allow flag is set. FloodWait auto-retries for
        reads only; writes always raise.
        """
        request = {"@type": method, **(params or {})}
        verdict = safety.verdict(method)
        if verdict == "write" and not allow_write:
            from tdelegram.errors import WriteConfirmationRequired

            raise WriteConfirmationRequired(method, request)
        if verdict == "destructive" and not (allow_write and allow_destructive):
            from tdelegram.errors import DestructiveConfirmationRequired

            raise DestructiveConfirmationRequired(method, request)
        effective_timeout = self._default_timeout if timeout is None else timeout
        attempt = 0
        while True:
            raw = self._loop.call(self._client_id, request, timeout=effective_timeout)
            if raw.get("@type") == "error":
                err = from_td_error(raw, method)
                if (
                    isinstance(err, FloodWait)
                    and verdict == "read"
                    and not self._no_retry
                    and should_retry_read(err, attempt, self._retry)
                ):
                    delay = self._retry.delay_for(attempt, err.retry_after)
                    sleep_retry(min(delay, 5.0))
                    attempt += 1
                    continue
                raise err
            return raw

    # -- updates -------------------------------------------------------
    def on_update(self, handler: UpdateHandler) -> Subscription:
        self._subscriber.handlers.append(handler)

        def _off() -> None:
            try:
                self._subscriber.handlers.remove(handler)
            except ValueError:
                pass

        return Subscription(_off)

    def next_update(self, timeout: float = 1.0) -> dict[str, Any] | None:
        try:
            return self._subscriber.queue.get(timeout=timeout)
        except queue.Empty:
            return None

    def dropped_updates(self) -> int:
        return self._subscriber.dropped

    # -- lifecycle -----------------------------------------------------
    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            try:
                self._loop.call(self._client_id, {"@type": "close"}, timeout=2.0)
            except (TelegramError, TelegramTimeoutError):
                pass
            deadline = time.monotonic() + 1.0
            while time.monotonic() < deadline:
                event = self.next_update(timeout=0.1)
                if event is None:
                    continue
                if event.get("@type") == "updateAuthorizationState":
                    state = event.get("authorization_state", {}).get("@type")
                    if state == "authorizationStateClosed":
                        break
        finally:
            pass

    def __enter__(self) -> TelegramClient:
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()

