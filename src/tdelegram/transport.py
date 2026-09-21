"""Transport seam: the only boundary between TDLib and everything else."""

from __future__ import annotations

import json
import queue
import threading
from collections.abc import Callable
from typing import Any, Protocol

from tdelegram.tdjson import TdJsonLib


class Transport(Protocol):
    def create_client_id(self) -> int: ...
    def send(self, client_id: int, request: str) -> None: ...
    def receive(self, timeout: float) -> str | None: ...
    def execute(self, request: str) -> str | None: ...


class TdJsonTransport:
    """Real transport over libtdjson via the modern C API."""

    def __init__(self, library_path: str) -> None:
        self._lib = TdJsonLib(library_path)

    def create_client_id(self) -> int:
        return self._lib.create_client_id()

    def send(self, client_id: int, request: str) -> None:
        self._lib.send(client_id, request)

    def receive(self, timeout: float) -> str | None:
        return self._lib.receive(timeout)

    def execute(self, request: str) -> str | None:
        return self._lib.execute(request)


Matcher = Callable[[dict[str, Any]], bool]


class FakeTransport:
    """Scripted transport for tests: no account, no network, no TDLib.

    - `add_response(matcher, response)`: when a sent request matches,
      the response (dict or callable returning dict) is queued for receive.
    - `add_update(update, client_id)`: queue an unsolicited update.
    - `sent`: log of (client_id, request_dict) tuples.
    """

    def __init__(self) -> None:
        self.sent: list[tuple[int, dict[str, Any]]] = []
        self._rules: list[tuple[Matcher, Any]] = []
        self._inbox: queue.Queue[str] = queue.Queue()
        self._clients: dict[int, int] = {}
        self._next_client_id = 1
        self._lock = threading.Lock()
        self.default_client_id = 1

    def create_client_id(self) -> int:
        with self._lock:
            cid = self._next_client_id
            self._next_client_id += 1
            return cid

    def add_response(self, matcher: Matcher, response: Any) -> None:
        self._rules.append((matcher, response))

    def add_simple_response(self, method: str, response: dict[str, Any]) -> None:
        def _match(req: dict[str, Any]) -> bool:
            return req.get("@type") == method

        self.add_response(_match, response)

    def add_update(self, update: dict[str, Any], client_id: int = 1) -> None:
        obj = dict(update)
        obj.setdefault("@client_id", client_id)
        self._inbox.put(json.dumps(obj))

    def send(self, client_id: int, request: str) -> None:
        req = json.loads(request)
        with self._lock:
            self.sent.append((client_id, req))
            rules = list(self._rules)
        for matcher, response in rules:
            try:
                matched = matcher(req)
            except Exception:
                continue
            if matched:
                payload = response(req) if callable(response) else response
                obj = dict(payload)
                obj.setdefault("@client_id", client_id)
                if "@extra" in req and "@extra" not in obj:
                    obj["@extra"] = req["@extra"]
                self._inbox.put(json.dumps(obj))
                return
        # No rule matched: reply with a generic ok echoing @extra so
        # callers do not hang. Tests can assert on `sent` for exactness.
        fallback: dict[str, Any] = {"@type": "ok", "@client_id": client_id}
        if "@extra" in req:
            fallback["@extra"] = req["@extra"]
        self._inbox.put(json.dumps(fallback))

    def receive(self, timeout: float) -> str | None:
        try:
            return self._inbox.get(timeout=timeout)
        except queue.Empty:
            return None

    def execute(self, request: str) -> str | None:
        req = json.loads(request)
        if req.get("@type") == "parseTextEntities":
            text = req.get("text", "")
            parse_mode = (req.get("parse_mode") or {}).get("@type", "")
            return json.dumps(
                {
                    "@type": "formattedText",
                    "text": _strip_markup(text, parse_mode),
                    "entities": [],
                }
            )
        return json.dumps({"@type": "ok"})


def _strip_markup(text: str, parse_mode: str) -> str:
    if "markdown" in parse_mode.lower():
        return text.replace("**", "").replace("__", "").replace("`", "")
    if "html" in parse_mode.lower():
        import re

        return re.sub(r"<[^>]+>", "", text)
    return text
