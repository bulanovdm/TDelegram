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

    def receive_domain(self) -> str:
        """Identifies which transports share one `receive` source.

        Transports returning the same domain must be served by a single
        reader thread. libtdjson's `td_receive` is global to the loaded
        library, so every TdJsonTransport over the same library shares one.
        """
        ...


class TdJsonTransport:
    """Real transport over libtdjson via the modern C API."""

    def __init__(self, library_path: str) -> None:
        self._library_path = library_path
        self._lib = TdJsonLib(library_path)

    def receive_domain(self) -> str:
        # td_receive is global to the library, not to this object, so every
        # instance over the same library must share one reader thread.
        return f"tdjson:{self._library_path}"

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

    Every request is checked against the pinned TDLib schema. One that does
    not match is answered with the 400 real TDLib would give -- or, for a field
    TDLib would silently drop, the 400 it should give -- and recorded in
    `schema_violations`. A fake that answers anything is how requests with
    parameters TDLib does not have passed every test. Pass `validate=False`
    only to test transport mechanics with requests that are not TDLib's.
    """

    def __init__(self, *, validate: bool = True) -> None:
        self.sent: list[tuple[int, dict[str, Any]]] = []
        self.schema_violations: list[tuple[dict[str, Any], list[str]]] = []
        self._validate = validate
        self._rules: list[tuple[Matcher, Any]] = []
        self._inbox: queue.Queue[str] = queue.Queue()
        self._clients: dict[int, int] = {}
        self._next_client_id = 1
        self._lock = threading.Lock()
        self.default_client_id = 1

    def _schema_error(self, req: dict[str, Any]) -> dict[str, Any] | None:
        if not self._validate:
            return None
        from tdelegram import schema

        problems = schema.validate(req)
        if not problems:
            return None
        with self._lock:
            self.schema_violations.append((req, problems))
        return {"@type": "error", "code": 400, "message": "; ".join(problems)}

    def receive_domain(self) -> str:
        # Each fake owns its queues, so each gets its own reader thread.
        return f"fake:{id(self)}"

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
        refusal = self._schema_error(req)
        if refusal is not None:
            refusal["@client_id"] = client_id
            if "@extra" in req:
                refusal["@extra"] = req["@extra"]
            self._inbox.put(json.dumps(refusal))
            return
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
        refusal = self._schema_error(req)
        if refusal is not None:
            return json.dumps(refusal)
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
    """Approximate TDLib's MarkdownV2 stripping. Entities are not simulated."""
    if "markdown" in parse_mode.lower():
        for marker in ("```", "__", "*", "_", "~", "`"):
            text = text.replace(marker, "")
        return text
    if "html" in parse_mode.lower():
        import re

        return re.sub(r"<[^>]+>", "", text)
    return text
