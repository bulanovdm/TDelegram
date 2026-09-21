"""Single global reader thread with @extra/@client_id routing.

`td_receive(timeout)` is global, not per-client: exactly one thread may
call it. This module provides one DispatchLoop per Transport, shared by
every client in the process.

Responses are routed by `@extra` through a pending dict, with timed-out
requests parked in an abandoned set so a late reply is dropped rather
than mismatched. Updates fan out to per-client bounded queues.
"""

from __future__ import annotations

import json
import queue
import threading
import time
import uuid
from collections.abc import Callable
from concurrent.futures import Future
from concurrent.futures import TimeoutError as FuturesTimeoutError
from typing import Any

from tdelegram.errors import TelegramTimeoutError
from tdelegram.transport import Transport

UpdateHandler = Callable[[dict[str, Any]], None]

SUBSCRIBER_QUEUE_SIZE = 1000


class _Subscriber:
    def __init__(self, maxsize: int = SUBSCRIBER_QUEUE_SIZE) -> None:
        self.queue: queue.Queue[dict[str, Any]] = queue.Queue(maxsize=maxsize)
        self.dropped = 0
        self.handlers: list[UpdateHandler] = []

    def push(self, event: dict[str, Any]) -> None:
        try:
            self.queue.put_nowait(event)
        except queue.Full:
            try:
                self.queue.get_nowait()
            except queue.Empty:
                pass
            self.dropped += 1
            try:
                self.queue.put_nowait(event)
            except queue.Full:
                pass
        for handler in list(self.handlers):
            try:
                handler(event)
            except Exception:
                continue


class DispatchLoop:
    """Routes td_receive events to pending futures or client subscribers."""

    def __init__(self, transport: Transport) -> None:
        self._transport = transport
        self._pending: dict[str, Future[dict[str, Any]]] = {}
        self._abandoned: set[str] = set()
        self._subscribers: dict[int, _Subscriber] = {}
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._running = False

    def start(self) -> None:
        with self._lock:
            if self._running:
                return
            self._running = True
            self._thread = threading.Thread(target=self._run, daemon=True, name="tdelegram-loop")
            self._thread.start()

    def stop(self) -> None:
        with self._lock:
            self._running = False

    def ensure_client(self, client_id: int) -> _Subscriber:
        with self._lock:
            sub = self._subscribers.get(client_id)
            if sub is None:
                sub = _Subscriber()
                self._subscribers[client_id] = sub
            return sub

    def call(
        self, client_id: int, request: dict[str, Any], timeout: float = 30.0
    ) -> dict[str, Any]:
        self.start()
        self.ensure_client(client_id)
        extra = uuid.uuid4().hex
        payload = dict(request)
        payload["@extra"] = extra
        future: Future[dict[str, Any]] = Future()
        with self._lock:
            self._pending[extra] = future
        data = json.dumps(payload, separators=(",", ":"))
        self._transport.send(client_id, data)
        try:
            return future.result(timeout=timeout)
        # Future.result raises concurrent.futures.TimeoutError, which only
        # became an alias of the builtin in 3.11. Catching the builtin
        # lets the raw error escape on 3.10, so catch the real one.
        except FuturesTimeoutError as exc:
            with self._lock:
                self._pending.pop(extra, None)
                self._abandoned.add(extra)
            raise TelegramTimeoutError(
                f"Timed out waiting for {request.get('@type', 'request')}."
            ) from exc

    def _run(self) -> None:
        while True:
            with self._lock:
                if not self._running:
                    return
            try:
                raw = self._transport.receive(1.0)
            except Exception:
                time.sleep(0.05)
                continue
            if raw is None:
                continue
            try:
                event = json.loads(raw)
            except (UnicodeDecodeError, json.JSONDecodeError):
                continue
            if not isinstance(event, dict):
                continue
            self._dispatch(event)

    def _dispatch(self, event: dict[str, Any]) -> None:
        extra = event.get("@extra")
        if isinstance(extra, str) and extra:
            with self._lock:
                if extra in self._abandoned:
                    self._abandoned.discard(extra)
                    return
                future = self._pending.pop(extra, None)
            if future is not None and not future.done():
                future.set_result(event)
                return
        client_id = event.get("@client_id")
        if isinstance(client_id, int):
            with self._lock:
                sub = self._subscribers.get(client_id)
            if sub is not None:
                sub.push(event)
            return
        # No @client_id (e.g. some td_execute-shaped events): broadcast.
        with self._lock:
            subs = list(self._subscribers.values())
        for sub in subs:
            sub.push(event)


_loops: dict[int, DispatchLoop] = {}
_loops_lock = threading.Lock()


def loop_for(transport: Transport) -> DispatchLoop:
    """Return the shared DispatchLoop for a transport, creating it on demand."""
    key = id(transport)
    with _loops_lock:
        loop = _loops.get(key)
        if loop is None:
            loop = DispatchLoop(transport)
            _loops[key] = loop
        return loop


def reset_loops() -> None:
    """Test helper: stop and drop all shared loops."""
    with _loops_lock:
        for loop in _loops.values():
            try:
                loop.stop()
            except Exception:
                continue
        _loops.clear()


def pending_count(loop: DispatchLoop) -> int:
    with loop._lock:
        return len(loop._pending)


def abandoned_count(loop: DispatchLoop) -> int:
    with loop._lock:
        return len(loop._abandoned)
