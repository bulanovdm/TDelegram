"""Reader loop: @extra routing, abandoned late responses, bounded queues."""

from __future__ import annotations

import time

from tdelegram.client import TelegramClient
from tdelegram.loop import abandoned_count, loop_for, pending_count
from tdelegram.transport import FakeTransport


def test_call_routes_by_extra() -> None:
    transport = FakeTransport()
    transport.add_simple_response("getMe", {"@type": "user", "id": 1})
    client = TelegramClient(transport)
    try:
        result = client.call("getMe", {}, allow_write=True)
        assert result["@type"] == "user"
    finally:
        client.close()


def test_timed_out_request_abandons_late_response() -> None:
    class SlowTransport(FakeTransport):
        def send(self, client_id: int, request: str) -> None:
            import json as _json

            req = _json.loads(request)
            # Never reply immediately; reply late after the caller timed out.
            import threading as _t

            def _late() -> None:
                time.sleep(0.3)
                obj = {"@type": "ok", "@client_id": client_id, "@extra": req.get("@extra")}
                self._inbox.put(_json.dumps(obj))

            _t.Thread(target=_late, daemon=True).start()

    transport = SlowTransport()
    client = TelegramClient(transport, default_timeout=0.1)
    try:
        try:
            client.call("getMe", {})
            raise AssertionError("should have timed out")
        except Exception as exc:
            assert "Timed out" in str(exc)
        loop = loop_for(transport)
        time.sleep(0.6)  # let the late response arrive and be dropped
        assert pending_count(loop) == 0
        # Abandoned set consumed the late response (no leak into subscriber queue).
        assert abandoned_count(loop) == 0
        assert client.next_update(timeout=0.2) is None
    finally:
        client.close()


def test_subscriber_queue_bounded_drop_oldest() -> None:
    transport = FakeTransport()
    client = TelegramClient(transport)
    try:
        for i in range(1200):
            transport.add_update({"@type": "updateNewMessage", "n": i}, client_id=client.client_id)
        time.sleep(1.0)
        assert client.dropped_updates() >= 100
        # Queue never grows unbounded.
        assert client._subscriber.queue.qsize() <= 1000
    finally:
        client.close()


def test_update_dispatch_by_client_id() -> None:
    transport = FakeTransport()
    a = TelegramClient(transport)
    b = TelegramClient(transport)
    try:
        transport.add_update({"@type": "updateNewMessage", "which": "a"}, client_id=a.client_id)
        transport.add_update({"@type": "updateNewMessage", "which": "b"}, client_id=b.client_id)
        time.sleep(0.5)
        ea = a.next_update(timeout=2.0)
        eb = b.next_update(timeout=2.0)
        assert ea is not None and ea.get("which") == "a"
        assert eb is not None and eb.get("which") == "b"
    finally:
        a.close()
        b.close()


def test_one_reader_thread_per_receive_domain() -> None:
    """Two real transports must share a loop, or TDLib aborts the process.

    `td_receive` is global to the loaded library. Loops were keyed on
    `id(transport)`, so a second TelegramClient started a second reader
    thread and libtdjson killed the process with "Receive must not be
    called simultaneously from two different threads".
    """
    from tdelegram.loop import loop_for, reset_loops
    from tdelegram.transport import FakeTransport

    class LibraryBacked(FakeTransport):
        """Stands in for TdJsonTransport: a shared, global receive source."""

        def receive_domain(self) -> str:
            return "tdjson:/usr/lib/libtdjson.so"

    reset_loops()
    try:
        assert loop_for(LibraryBacked()) is loop_for(LibraryBacked()), (
            "two transports over one library must share a reader thread"
        )
        # Fakes own their queues, so they stay independent.
        assert loop_for(FakeTransport()) is not loop_for(FakeTransport())
    finally:
        reset_loops()


def test_a_shared_loop_serves_several_clients() -> None:
    """Sharing a loop must not merge the clients' update streams."""
    from tdelegram.client import TelegramClient
    from tdelegram.loop import loop_for, reset_loops
    from tdelegram.transport import FakeTransport

    class LibraryBacked(FakeTransport):
        def receive_domain(self) -> str:
            return "tdjson:shared"

    reset_loops()
    transport = LibraryBacked()
    try:
        first, second = TelegramClient(transport), TelegramClient(transport)
        assert first.client_id != second.client_id
        assert loop_for(transport) is loop_for(transport)
        transport.add_update({"@type": "updateNewMessage"}, client_id=second.client_id)
        assert second.next_update(timeout=2.0) is not None
        assert first.next_update(timeout=0.2) is None, "updates leaked across clients"
    finally:
        reset_loops()
